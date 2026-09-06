"""
Structure des notions et statut d'avancement (06/09/2026, Partie 1 du
chantier "confiance pedagogique" -- voir clovis-plan-travail-10-
parties.md, Point 1 du document de vision).

Nouvel objet de donnees, INDEPENDANT de l'ancienne fonctionnalite
"Programme" supprimee le 28/08/2026 (voir
migrations/2026_08_28_suppression_programme.sql et
_desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md) : aucune table,
aucune colonne, aucun code de cet ancien systeme n'est reutilise ici.

Une notion est rattachee a un CODE (codes_partage), pas a un role --
meme principe que le reste de "Mes codes" (voir core/codes_partage.py).
Arborescence LIBRE via notion_parent_id (pas de niveaux figes type
matiere/chapitre) : un prof peut fusionner/renommer/reordonner/
supprimer sans contrainte de profondeur.

Ce fichier ne fait que le CRUD de base + reorganisation. La generation
d'une structure depuis un document (appel LLM) est un fichier separe
expres (core/generation_notions_llm.py) -- voir Partie 1 du plan de
travail : "fichiers differents = parties testables separement".
"""

import logging
import os
from datetime import datetime

from supabase import create_client

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

STATUTS_VALIDES = {"a_venir", "en_cours", "acquis"}

_COLONNES_NOTION = "id, code_id, notion_parent_id, nom, statut, ordre, created_at, updated_at"


def code_appartient_a(code_id: str, proprietaire_id: str) -> bool:
    """Verifie que code_id existe ET appartient bien a proprietaire_id --
    meme principe de verification d'appartenance que le reste de "Mes
    codes" (voir core/codes_partage.py:modifier_code), fait ici a chaque
    fonction mutante plutot qu'une seule fois cote route, pour que ce
    module reste utilisable seul (MCP, tache planifiee...) sans dependre
    de la couche API pour sa securite. Fonction PUBLIQUE (pas de prefixe
    "_") : reutilisee telle quelle par api/programme_notions.py::generer
    pour refuser tot un document uploade si le code n'appartient pas a
    l'utilisateur, avant meme d'appeler le LLM.
    """
    try:
        res = (
            supabase.table("codes_partage")
            .select("id")
            .eq("id", code_id)
            .eq("proprietaire_id", proprietaire_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (verification appartenance code {code_id}) : {e}")
        return False
    return bool(res and res.data)


def _notion_du_code(notion_id: str, code_id: str) -> dict | None:
    """Renvoie la notion si elle existe ET appartient bien a code_id (une
    notion d'un autre code ne doit jamais pouvoir etre touchee via ce
    code -- verifie explicitement, jamais suppose)."""
    try:
        res = (
            supabase.table("notions")
            .select(_COLONNES_NOTION)
            .eq("id", notion_id)
            .eq("code_id", code_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture notion {notion_id}) : {e}")
        return None
    return res.data if res and res.data else None


def lister_notions(code_id: str, proprietaire_id: str) -> list[dict] | None:
    """Liste TOUTES les notions du code, a plat (comme
    core/dossiers_bibliotheque.py:lister_dossiers) -- a l'appelant de
    reconstruire l'arborescence via notion_parent_id. None si le code
    n'existe pas ou n'appartient pas a proprietaire_id."""
    if not code_appartient_a(code_id, proprietaire_id):
        return None
    try:
        res = (
            supabase.table("notions")
            .select(_COLONNES_NOTION)
            .eq("code_id", code_id)
            .order("notion_parent_id", desc=False, nullsfirst=True)
            .order("ordre")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste notions du code {code_id}) : {e}")
        return None
    return res.data or []


def _prochain_ordre(code_id: str, notion_parent_id: str | None) -> int:
    """Place une nouvelle notion apres la derniere de son parent (meme
    code). Requete dediee plutot que reutiliser lister_notions pour ne
    pas re-verifier l'appartenance du code a chaque creation (deja fait
    par l'appelant, voir creer_notion)."""
    query = supabase.table("notions").select("ordre").eq("code_id", code_id)
    query = query.is_("notion_parent_id", "null") if notion_parent_id is None else query.eq("notion_parent_id", notion_parent_id)
    res = query.order("ordre", desc=True).limit(1).execute()
    if res.data:
        return res.data[0]["ordre"] + 1
    return 0


def creer_notion(code_id: str, proprietaire_id: str, nom: str, notion_parent_id: str | None = None) -> dict | None:
    """Cree une notion a la racine du code, ou comme sous-notion de
    notion_parent_id si fourni. None si le code n'appartient pas a
    proprietaire_id, ou si notion_parent_id est fourni mais n'appartient
    pas a ce meme code (jamais suppose, toujours verifie -- rattacher
    une notion au mauvais code casserait silencieusement l'arborescence
    d'un autre utilisateur)."""
    if not code_appartient_a(code_id, proprietaire_id):
        return None
    if notion_parent_id is not None and not _notion_du_code(notion_parent_id, code_id):
        return None
    nom = (nom or "").strip()
    if not nom:
        return None
    insertion = supabase.table("notions").insert({
        "code_id": code_id,
        "notion_parent_id": notion_parent_id,
        "nom": nom,
        "ordre": _prochain_ordre(code_id, notion_parent_id),
    }).execute()
    return insertion.data[0] if insertion.data else None


def renommer_notion(notion_id: str, code_id: str, proprietaire_id: str, nouveau_nom: str) -> dict | None:
    if not code_appartient_a(code_id, proprietaire_id):
        return None
    nouveau_nom = (nouveau_nom or "").strip()
    if not nouveau_nom or not _notion_du_code(notion_id, code_id):
        return None
    res = (
        supabase.table("notions")
        .update({"nom": nouveau_nom, "updated_at": datetime.utcnow().isoformat()})
        .eq("id", notion_id)
        .execute()
    )
    return res.data[0] if res.data else None


def changer_statut_notion(notion_id: str, code_id: str, proprietaire_id: str, statut: str) -> dict | None:
    if statut not in STATUTS_VALIDES:
        return None
    if not code_appartient_a(code_id, proprietaire_id) or not _notion_du_code(notion_id, code_id):
        return None
    res = (
        supabase.table("notions")
        .update({"statut": statut, "updated_at": datetime.utcnow().isoformat()})
        .eq("id", notion_id)
        .execute()
    )
    return res.data[0] if res.data else None


def reordonner_notions(code_id: str, proprietaire_id: str, notion_parent_id: str | None, notion_ids_ordonnes: list[str]) -> bool:
    """Reordonne EN UNE FOIS toutes les notions d'un meme parent (et d'un
    meme code) : notion_ids_ordonnes donne le nouvel ordre complet (la
    position dans la liste devient la valeur d'`ordre`). Les ids qui
    n'appartiennent pas a ce code+parent sont ignores silencieusement
    plutot que de faire echouer tout le reordonnancement -- une carte
    deplacee entre-temps par une autre session ne doit pas bloquer le
    reste."""
    if not code_appartient_a(code_id, proprietaire_id):
        return False
    for position, notion_id in enumerate(notion_ids_ordonnes):
        notion = _notion_du_code(notion_id, code_id)
        if not notion or notion["notion_parent_id"] != notion_parent_id:
            continue
        supabase.table("notions").update({"ordre": position, "updated_at": datetime.utcnow().isoformat()}).eq("id", notion_id).execute()
    return True


def _descendants(code_id: str, notion_id: str, toutes: list[dict] | None = None) -> set[str]:
    """Renvoie l'ensemble des ids descendants (tous niveaux) de
    notion_id -- meme logique en boucle a point fixe que
    core/dossiers_bibliotheque.py:supprimer_dossier, reprise ici pour la
    fusion (voir fusionner_notions)."""
    if toutes is None:
        toutes = supabase.table("notions").select("id, notion_parent_id").eq("code_id", code_id).execute().data or []
    a_supprimer: set[str] = set()
    changement = True
    while changement:
        changement = False
        for n in toutes:
            parent = n["notion_parent_id"]
            if (parent == notion_id or parent in a_supprimer) and n["id"] not in a_supprimer:
                a_supprimer.add(n["id"])
                changement = True
    return a_supprimer


def fusionner_notions(code_id: str, proprietaire_id: str, notion_source_id: str, notion_cible_id: str) -> dict | None:
    """Fusionne notion_source_id DANS notion_cible_id : les sous-notions
    directes de la source sont reparentees sous la cible (leur propre
    contenu/statut n'est jamais touche), puis la source est supprimee.
    Le statut et le nom de la cible sont conserves tels quels (aucune
    des deux notions n'a de contenu "de fond" au-dela du nom/statut a ce
    stade -- rien d'autre a fusionner).

    None si les deux notions n'appartiennent pas au meme code+proprietaire,
    si source == cible, ou si cible est un descendant de source (une
    fusion ne doit jamais creer de cycle dans l'arborescence)."""
    if notion_source_id == notion_cible_id or not code_appartient_a(code_id, proprietaire_id):
        return None
    source = _notion_du_code(notion_source_id, code_id)
    cible = _notion_du_code(notion_cible_id, code_id)
    if not source or not cible:
        return None
    if notion_cible_id in _descendants(code_id, notion_source_id):
        return None
    supabase.table("notions").update({
        "notion_parent_id": notion_cible_id,
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("code_id", code_id).eq("notion_parent_id", notion_source_id).execute()
    supabase.table("notions").delete().eq("id", notion_source_id).execute()
    return cible


def supprimer_notion(code_id: str, proprietaire_id: str, notion_id: str) -> bool:
    """Supprime une notion ET tous ses descendants (ON DELETE CASCADE
    cote base -- pas de mode "promouvoir les enfants", coherent avec le
    comportement deja adopte pour les dossiers de bibliotheque,
    core/dossiers_bibliotheque.py:supprimer_dossier)."""
    if not code_appartient_a(code_id, proprietaire_id) or not _notion_du_code(notion_id, code_id):
        return False
    supabase.table("notions").delete().eq("id", notion_id).execute()
    return True
