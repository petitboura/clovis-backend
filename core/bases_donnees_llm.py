"""
Section "Notion-like" (Partie 2) -- bases de révision + gestion de tâches
(lot 3/5), 2026-08-20, demande Bourama. Voir
migrations/2026_08_20_bases_donnees_revision_taches.sql pour le schéma et
le choix d'un mécanisme unique pour les deux usages.

Même organisation que core/pages_notion_llm.py : ce module contient toute
la logique, importée par les deux serveurs MCP (core/serveur_mcp_generation.py
et core/serveur_mcp_espace.py) pour ne jamais la dupliquer.

Vérification de propriété : une base de données n'a pas de
proprietaire_id propre, elle est vérifiée via sa page (pages.proprietaire_id)
-- voir _base_appartient_a ci-dessous, même principe que les blocs dans
pages_notion_llm.py.

Ce lot ne stocke PAS les 4 vues séparément (liste/tableau/calendrier/
kanban) -- elles se calculent côté frontend (lot 5) à partir des mêmes
données. `vue_par_defaut` est juste un confort, jamais une contrainte.
"""

import logging
import os

from supabase import create_client


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

TYPES_PROPRIETES_CONNUS = {"texte", "nombre", "date", "statut", "case_a_cocher"}


# ---------------------------------------------------------------------------
# Propriété / vérification
# ---------------------------------------------------------------------------


def base_appartient_a(base_id: str, user_id: str) -> dict | None:
    """Charge une base en vérifiant, via sa page, qu'elle appartient
    bien à user_id. None si introuvable ou pas propriétaire."""
    try:
        res = (
            supabase.table("bases_donnees")
            .select("id, page_id, titre, vue_par_defaut")
            .eq("id", base_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture base {base_id}) : {e}")
        return None
    if not res or not res.data:
        return None
    base = res.data
    try:
        page = (
            supabase.table("pages")
            .select("proprietaire_id")
            .eq("id", base["page_id"])
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (page de la base {base_id}) : {e}")
        return None
    if not page or not page.data or page.data["proprietaire_id"] != user_id:
        return None
    return base


def _page_appartient_a(page_id: str, user_id: str) -> bool:
    try:
        res = (
            supabase.table("pages")
            .select("proprietaire_id")
            .eq("id", page_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture page {page_id}) : {e}")
        return False
    return bool(res and res.data and res.data["proprietaire_id"] == user_id)


# ---------------------------------------------------------------------------
# Bases de données
# ---------------------------------------------------------------------------


def ajouter_base(user_id: str, page_id: str, titre: str) -> dict | None:
    """Crée une base de données et le bloc "base_donnees" qui la
    représente sur la page, dans le même mouvement -- une base isolée
    sans bloc ne serait jamais visible en navigant la page."""
    if not user_id or not _page_appartient_a(page_id, user_id):
        return None
    try:
        res = (
            supabase.table("bases_donnees")
            .insert({"page_id": page_id, "titre": (titre or "").strip()})
            .execute()
        )
        base = res.data[0] if res.data else None
        if base:
            supabase.table("blocs").insert(
                {"page_id": page_id, "type": "base_donnees", "contenu": {"base_donnees_id": base["id"]}}
            ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création base, page {page_id}) : {e}")
        return None
    return base


def obtenir_base(user_id: str, base_id: str) -> str | None:
    """Contenu complet d'une base : ses propriétés (nom + type) et ses
    éléments avec leurs valeurs, dans l'ordre. Les sous-éléments
    (sous-tâches) sont indiqués avec leur parent. Texte déjà formaté,
    prêt pour le modèle. None si introuvable ou pas propriétaire."""
    if not user_id or not base_id:
        return None
    base = base_appartient_a(base_id, user_id)
    if not base:
        return None
    try:
        proprietes = (
            supabase.table("bases_donnees_proprietes")
            .select("id, nom, type, options")
            .eq("base_id", base_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
        elements = (
            supabase.table("bases_donnees_elements")
            .select("id, parent_element_id, ordre")
            .eq("base_id", base_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
        valeurs = (
            supabase.table("bases_donnees_valeurs")
            .select("element_id, propriete_id, valeur")
            .in_("element_id", [e["id"] for e in elements])
            .execute()
            .data
            if elements
            else []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (contenu base {base_id}) : {e}")
        return None

    nom_par_propriete = {p["id"]: p["nom"] for p in proprietes}
    valeurs_par_element: dict[str, dict] = {}
    for v in valeurs or []:
        valeurs_par_element.setdefault(v["element_id"], {})[nom_par_propriete.get(v["propriete_id"], "?")] = v[
            "valeur"
        ]

    lignes = [f"Base de données : {base['titre'] or '(sans titre)'} (id={base['id']}, vue par défaut : {base['vue_par_defaut']})"]
    lignes.append("")
    lignes.append("Propriétés :" if proprietes else "Propriétés : aucune")
    for p in proprietes:
        options = f" options={p['options']}" if p["type"] == "statut" and p["options"] else ""
        lignes.append(f"- id={p['id']} [{p['type']}] {p['nom']}{options}")
    lignes.append("")
    lignes.append("Éléments :" if elements else "Éléments : aucun")
    for el in elements:
        parent = f" (sous-élément de {el['parent_element_id']})" if el["parent_element_id"] else ""
        valeurs_texte = valeurs_par_element.get(el["id"], {})
        lignes.append(f"- id={el['id']}{parent} — {valeurs_texte}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Propriétés
# ---------------------------------------------------------------------------


def ajouter_propriete(user_id: str, base_id: str, nom: str, type_propriete: str, options: list | None = None) -> dict | None:
    if not user_id or not base_appartient_a(base_id, user_id):
        return None
    type_propriete = type_propriete if type_propriete in TYPES_PROPRIETES_CONNUS else "texte"
    try:
        res = (
            supabase.table("bases_donnees_proprietes")
            .insert(
                {
                    "base_id": base_id,
                    "nom": (nom or "").strip(),
                    "type": type_propriete,
                    "options": options or [],
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création propriété, base {base_id}) : {e}")
        return None
    return res.data[0] if res.data else None


# ---------------------------------------------------------------------------
# Éléments
# ---------------------------------------------------------------------------


def element_appartient_a(element_id: str, user_id: str) -> dict | None:
    try:
        res = (
            supabase.table("bases_donnees_elements")
            .select("id, base_id")
            .eq("id", element_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture élément {element_id}) : {e}")
        return None
    if not res or not res.data:
        return None
    if not base_appartient_a(res.data["base_id"], user_id):
        return None
    return res.data


def ajouter_element(user_id: str, base_id: str, valeurs: dict, parent_element_id: str | None = None) -> dict | None:
    """Crée un élément avec ses valeurs initiales. `valeurs` : dict
    {nom_propriete: valeur} -- les propriétés inconnues sont ignorées
    silencieusement (jamais de crash sur une faute de frappe du nom)."""
    if not user_id or not base_appartient_a(base_id, user_id):
        return None
    if parent_element_id and not element_appartient_a(parent_element_id, user_id):
        return None
    try:
        proprietes = (
            supabase.table("bases_donnees_proprietes").select("id, nom").eq("base_id", base_id).execute().data or []
        )
        id_par_nom = {p["nom"]: p["id"] for p in proprietes}
        res = (
            supabase.table("bases_donnees_elements")
            .insert({"base_id": base_id, "parent_element_id": parent_element_id})
            .execute()
        )
        element = res.data[0] if res.data else None
        if element and valeurs:
            lignes = [
                {"element_id": element["id"], "propriete_id": id_par_nom[nom], "valeur": val}
                for nom, val in valeurs.items()
                if nom in id_par_nom
            ]
            if lignes:
                supabase.table("bases_donnees_valeurs").insert(lignes).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création élément, base {base_id}) : {e}")
        return None
    return element


def modifier_valeurs_element(user_id: str, element_id: str, valeurs: dict) -> bool:
    """Met à jour (upsert) une ou plusieurs valeurs d'un élément déjà
    existant. Propriétés inconnues ignorées silencieusement."""
    element = element_appartient_a(element_id, user_id) if user_id else None
    if not element or not valeurs:
        return False
    try:
        proprietes = (
            supabase.table("bases_donnees_proprietes")
            .select("id, nom")
            .eq("base_id", element["base_id"])
            .execute()
            .data
            or []
        )
        id_par_nom = {p["nom"]: p["id"] for p in proprietes}
        for nom, val in valeurs.items():
            if nom not in id_par_nom:
                continue
            supabase.table("bases_donnees_valeurs").upsert(
                {"element_id": element_id, "propriete_id": id_par_nom[nom], "valeur": val},
                on_conflict="element_id,propriete_id",
            ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification valeurs élément {element_id}) : {e}")
        return False
    return True


def supprimer_element(user_id: str, element_id: str) -> bool:
    """Supprime l'élément -- cascade SQL sur ses valeurs et ses
    sous-éléments (sous-tâches)."""
    if not user_id or not element_appartient_a(element_id, user_id):
        return False
    try:
        supabase.table("bases_donnees_elements").delete().eq("id", element_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression élément {element_id}) : {e}")
        return False
    return True
