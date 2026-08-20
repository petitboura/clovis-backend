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
}


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
            .select("id, titre, proprietaire_id")
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


def obtenir_page(user_id: str, page_id: str) -> str | None:
    """Contenu d'UNE page précise : ses sous-pages (id + titre, pour
    permettre d'y naviguer ensuite) et ses blocs (id + type + contenu,
    dans l'ordre). Texte déjà formaté, prêt à renvoyer tel quel au
    modèle. None si introuvable ou pas propriétaire (jamais de fuite
    entre utilisateurs)."""
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
    lignes.append("")
    lignes.append("Sous-pages :" if sous_pages else "Sous-pages : aucune")
    for sp in sous_pages:
        lignes.append(f"- id={sp['id']} — {sp['titre'] or '(sans titre)'}")
    lignes.append("")
    lignes.append("Blocs :" if blocs else "Blocs : aucun")
    for b in blocs:
        texte_contenu = b["contenu"].get("texte", "") if isinstance(b["contenu"], dict) else ""
        lignes.append(f"- id={b['id']} [{b['type']}] {texte_contenu}")
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
    try:
        res = (
            supabase.table("blocs")
            .insert(
                {
                    "page_id": page_id,
                    "type": type_bloc,
                    "contenu": {"texte": texte or ""},
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
        res = supabase.table("blocs").select("id, page_id").eq("id", bloc_id).maybe_single().execute()
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
    if not user_id or not _bloc_appartient_a(bloc_id, user_id):
        return None
    try:
        res = (
            supabase.table("blocs")
            .update({"contenu": {"texte": texte or ""}, "updated_at": "now()"})
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
