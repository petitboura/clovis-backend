"""
Section "Notion-like" (Partie 2) -- lecture/écriture pour les outils IA,
2026-08-20, demande Bourama : "il faut que l'IA puisse y naviguer et
s'orienter, pareil avec le MCP public". Même esprit que core/programme_llm.py
(lecture) + core/programme_ecriture.py (écriture), mais réunis dans un seul
fichier ici -- périmètre plus simple pour ce lot (pas de matières/chapitres
imbriqués sur plusieurs niveaux fixes, juste des pages qui peuvent
s'imbriquer librement).

Utilisé par les outils MCP des DEUX serveurs :
- core/serveur_mcp_generation.py (agent interne, conversation dans l'app)
- core/serveur_mcp_espace.py (MCP public, client externe type Claude)
Ni l'un ni l'autre ne redéfinit cette logique -- ils l'importent tous les
deux, pour ne jamais la dupliquer ni risquer de la désynchroniser (même
principe que documenté en tête de core/programme_ecriture.py).

Choix de périmètre pour ce lot (1/5) :
- Pas d'historique d'écriture / annuler_derniere_modification comme pour
  la structure programme (core/programme_ecriture.py) -- pas demandé ici,
  ajoutable plus tard si besoin. Suppression -> directe (voir
  OUTILS_SENSIBLES dans registre_outils.py pour la confirmation côté
  agent interne).
- Pas de vérification "code de partage" (peut_acceder_programme_recu) --
  une page n'est accessible qu'à son propriétaire direct, pas de partage
  pour ce lot.
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

TYPES_BLOCS_CONNUS = {
    "texte",
    "titre",
    "liste_puces",
    "liste_numerotee",
    "case_a_cocher",
    "citation",
    "separateur",
    "equation",
    "base_donnees",  # lot 3/5 -- contenu = {"base_donnees_id": "..."}
    # Partie 2, 22/08/2026 -- ajoutés ici aussi pour rester cohérent avec
    # api/pages_notion.py (voir le bug corrigé le 21/08 : ces deux listes
    # avaient divergé). "image"/"fichier" volontairement PAS repris ici --
    # ils nécessitent un vrai upload binaire (POST /api/blocs/upload),
    # hors de portée d'un outil texte MCP ; les créer via l'IA donnerait
    # un bloc sans url exploitable.
    "video",
    "embed",
    "bascule",
}

# Lot 2/5 -- pages carrefour : une page carrefour pointe vers un ou
# plusieurs éléments de la structure programme existante, jamais vers un
# autre système (bibliothèque personnelle par ex.) pour ce lot.
TYPES_CIBLE_CARREFOUR = ("programme", "matiere", "chapitre", "document")


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


def lister_mes_pages_racines_legeres(user_id: str) -> list[dict]:
    """Liste légère (id, titre) des pages racines (sans parent) de cet
    utilisateur -- point de départ avant tout autre outil "page" s'il n'a
    pas déjà un id précis en tête. Liste vide si rien ou si user_id est
    vide, jamais None."""
    if not user_id:
        return []
    try:
        res = (
            supabase.table("pages")
            .select("id, titre")
            .eq("proprietaire_id", user_id)
            .is_("parent_id", "null")
            .order("ordre")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste pages racines, user_id={user_id}) : {e}")
        return []
    return [{"id": ligne["id"], "titre": ligne["titre"] or "(sans titre)"} for ligne in (res.data or [])]


def _page_appartient_a(page_id: str, user_id: str) -> dict | None:
    try:
        res = (
            supabase.table("pages")
            .select("id, titre, proprietaire_id, est_carrefour")
            .eq("id", page_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture page {page_id}) : {e}")
        return None
    if not res or not res.data or res.data["proprietaire_id"] != user_id:
        return None
    return res.data


def _label_cible_carrefour(type_cible: str, cible_id: str) -> str | None:
    """Libellé lisible d'une cible référencée par une page carrefour --
    None si la cible n'existe pas (référence orpheline, ne doit jamais
    faire planter l'affichage de la page, juste être ignorée)."""
    try:
        if type_cible == "programme":
            res = supabase.table("programmes").select("niveau, nom").eq("id", cible_id).maybe_single().execute()
            if not res or not res.data:
                return None
            p = res.data
            return p["niveau"] + (f" ({p['nom']})" if p.get("nom") else "")
        if type_cible == "matiere":
            res = supabase.table("matieres").select("nom").eq("id", cible_id).maybe_single().execute()
            return res.data["nom"] if res and res.data else None
        if type_cible == "chapitre":
            res = supabase.table("chapitres").select("nom").eq("id", cible_id).maybe_single().execute()
            return res.data["nom"] if res and res.data else None
        if type_cible == "document":
            res = supabase.table("documents_programme").select("titre").eq("id", cible_id).maybe_single().execute()
            return res.data["titre"] if res and res.data else None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (label cible carrefour {type_cible}={cible_id}) : {e}")
        return None
    return None


def _proprietaire_cible_carrefour(type_cible: str, cible_id: str) -> str | None:
    """Propriétaire réel d'une cible (programme/matière/chapitre/document),
    pour vérifier qu'une page carrefour ne référence que du contenu qui
    appartient bien à cet utilisateur -- même logique que
    core/bibliotheque_programme.py::proprietaire_emplacement, réécrite
    ici en direct (via supabase) pour couvrir aussi "document", absent de
    cette fonction-là."""
    try:
        if type_cible == "programme":
            res = supabase.table("programmes").select("proprietaire_id").eq("id", cible_id).maybe_single().execute()
            return res.data["proprietaire_id"] if res and res.data else None
        if type_cible == "matiere":
            m = supabase.table("matieres").select("programme_id").eq("id", cible_id).maybe_single().execute()
            if not m or not m.data:
                return None
            return _proprietaire_cible_carrefour("programme", m.data["programme_id"])
        if type_cible == "chapitre":
            c = supabase.table("chapitres").select("matiere_id").eq("id", cible_id).maybe_single().execute()
            if not c or not c.data:
                return None
            return _proprietaire_cible_carrefour("matiere", c.data["matiere_id"])
        if type_cible == "document":
            d = (
                supabase.table("documents_programme")
                .select("chapitre_id")
                .eq("id", cible_id)
                .maybe_single()
                .execute()
            )
            if not d or not d.data:
                return None
            return _proprietaire_cible_carrefour("chapitre", d.data["chapitre_id"])
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (propriétaire cible carrefour {type_cible}={cible_id}) : {e}")
        return None
    return None


def ajouter_reference_carrefour(user_id: str, page_id: str, type_cible: str, cible_id: str) -> dict | None:
    """Ajoute une référence à une page carrefour -- la page devient
    carrefour automatiquement si elle ne l'était pas déjà (pas besoin
    d'un appel séparé pour "activer" le mode carrefour). None si la page
    ne lui appartient pas, si type_cible est invalide, ou si la cible ne
    lui appartient pas (jamais de référence vers le contenu de
    quelqu'un d'autre)."""
    if not user_id or type_cible not in TYPES_CIBLE_CARREFOUR:
        return None
    if not _page_appartient_a(page_id, user_id):
        return None
    if _proprietaire_cible_carrefour(type_cible, cible_id) != user_id:
        return None
    try:
        supabase.table("pages").update({"est_carrefour": True}).eq("id", page_id).execute()
        res = (
            supabase.table("pages_carrefour_references")
            .insert({"page_id": page_id, "type_cible": type_cible, "cible_id": cible_id})
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (ajout référence carrefour, page {page_id}) : {e}")
        return None
    return res.data[0] if res.data else None


def lister_references_carrefour(page_id: str) -> list[dict]:
    """Références d'une page carrefour, avec leur libellé résolu --
    les références orphelines (cible supprimée depuis) sont ignorées."""
    try:
        refs = (
            supabase.table("pages_carrefour_references")
            .select("id, type_cible, cible_id")
            .eq("page_id", page_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste références carrefour, page {page_id}) : {e}")
        return []
    resultat = []
    for r in refs:
        label = _label_cible_carrefour(r["type_cible"], r["cible_id"])
        if label is None:
            continue  # référence orpheline, ignorée silencieusement
        resultat.append({"id": r["id"], "type_cible": r["type_cible"], "cible_id": r["cible_id"], "label": label})
    return resultat


def supprimer_reference_carrefour(user_id: str, page_id: str, reference_id: str) -> bool:
    if not user_id or not _page_appartient_a(page_id, user_id):
        return False
    try:
        supabase.table("pages_carrefour_references").delete().eq("id", reference_id).eq(
            "page_id", page_id
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression référence carrefour {reference_id}) : {e}")
        return False
    return True


def obtenir_page(user_id: str, page_id: str) -> str | None:
    """Contenu d'UNE page précise : ses sous-pages (id + titre, pour
    permettre d'y naviguer ensuite), ses blocs (id + type + contenu,
    dans l'ordre), et si c'est une page carrefour, ses références vers
    la structure programme. Texte déjà formaté, prêt à renvoyer tel
    quel au modèle. None si introuvable ou pas propriétaire (jamais de
    fuite entre utilisateurs)."""
    if not user_id or not page_id:
        return None
    page = _page_appartient_a(page_id, user_id)
    if not page:
        return None
    try:
        sous_pages = (
            supabase.table("pages")
            .select("id, titre")
            .eq("parent_id", page_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
        blocs = (
            supabase.table("blocs")
            .select("id, type, contenu, ordre")
            .eq("page_id", page_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (contenu page {page_id}) : {e}")
        return None

    lignes = [f"Page : {page['titre'] or '(sans titre)'} (id={page['id']})"]
    if page.get("est_carrefour"):
        refs = lister_references_carrefour(page_id)
        lignes.append("")
        lignes.append("Page carrefour -- références :" if refs else "Page carrefour -- aucune référence pour l'instant")
        for r in refs:
            lignes.append(f"- [{r['type_cible']}] {r['label']} (id={r['cible_id']}, ref={r['id']})")
    lignes.append("")
    lignes.append("Sous-pages :" if sous_pages else "Sous-pages : aucune")
    for sp in sous_pages:
        lignes.append(f"- id={sp['id']} — {sp['titre'] or '(sans titre)'}")
    lignes.append("")
    lignes.append("Blocs :" if blocs else "Blocs : aucun")
    for b in blocs:
        contenu = b["contenu"] if isinstance(b["contenu"], dict) else {}
        texte_affiche = contenu.get("latex", "") if b["type"] == "equation" else contenu.get("texte", "")
        lignes.append(f"- id={b['id']} [{b['type']}] {texte_affiche}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Écriture -- pages
# ---------------------------------------------------------------------------


def ajouter_page(user_id: str, titre: str, parent_id: str | None = None) -> dict | None:
    """Crée une page pour cet utilisateur. Si parent_id est fourni, elle
    devient une sous-page (None si parent_id ne lui appartient pas)."""
    if not user_id:
        return None
    if parent_id and not _page_appartient_a(parent_id, user_id):
        return None
    try:
        res = (
            supabase.table("pages")
            .insert({"proprietaire_id": user_id, "parent_id": parent_id, "titre": (titre or "").strip()})
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création page, user_id={user_id}) : {e}")
        return None
    return res.data[0] if res.data else None


def modifier_page(user_id: str, page_id: str, titre: str | None = None) -> dict | None:
    if not user_id or not page_id or not _page_appartient_a(page_id, user_id):
        return None
    if titre is None:
        return None
    try:
        res = (
            supabase.table("pages")
            .update({"titre": titre.strip(), "updated_at": "now()"})
            .eq("id", page_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification page {page_id}) : {e}")
        return None
    return res.data[0] if res.data else None


def supprimer_page(user_id: str, page_id: str) -> bool:
    """Supprime la page -- cascade SQL sur ses blocs et sous-pages."""
    if not user_id or not page_id or not _page_appartient_a(page_id, user_id):
        return False
    try:
        supabase.table("pages").delete().eq("id", page_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression page {page_id}) : {e}")
        return False
    return True


# ---------------------------------------------------------------------------
# Écriture -- blocs
# ---------------------------------------------------------------------------


def ajouter_bloc(user_id: str, page_id: str, type_bloc: str, texte: str, ordre: int = 0) -> dict | None:
    if not user_id or not _page_appartient_a(page_id, user_id):
        return None
    type_bloc = type_bloc if type_bloc in TYPES_BLOCS_CONNUS else "texte"
    # Un bloc "equation" stocke son contenu sous la clé "latex" (code
    # LaTeX brut, rendu côté frontend -- lot 5), tous les autres types
    # sous "texte", voir obtenir_page ci-dessus pour la lecture symétrique.
    cle_contenu = "latex" if type_bloc == "equation" else "texte"
    try:
        res = (
            supabase.table("blocs")
            .insert(
                {
                    "page_id": page_id,
                    "type": type_bloc,
                    "contenu": {cle_contenu: texte or ""},
                    "ordre": ordre,
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création bloc, page {page_id}) : {e}")
        return None
    return res.data[0] if res.data else None


def _bloc_appartient_a(bloc_id: str, user_id: str) -> dict | None:
    try:
        res = supabase.table("blocs").select("id, page_id, type").eq("id", bloc_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture bloc {bloc_id}) : {e}")
        return None
    if not res or not res.data:
        return None
    bloc = res.data
    if not _page_appartient_a(bloc["page_id"], user_id):
        return None
    return bloc


def modifier_bloc(user_id: str, bloc_id: str, texte: str) -> dict | None:
    bloc = _bloc_appartient_a(bloc_id, user_id) if user_id else None
    if not bloc:
        return None
    cle_contenu = "latex" if bloc["type"] == "equation" else "texte"
    try:
        res = (
            supabase.table("blocs")
            .update({"contenu": {cle_contenu: texte or ""}, "updated_at": "now()"})
            .eq("id", bloc_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification bloc {bloc_id}) : {e}")
        return None
    return res.data[0] if res.data else None


def supprimer_bloc(user_id: str, bloc_id: str) -> bool:
    if not user_id or not _bloc_appartient_a(bloc_id, user_id):
        return False
    try:
        supabase.table("blocs").delete().eq("id", bloc_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression bloc {bloc_id}) : {e}")
        return False
    return True
