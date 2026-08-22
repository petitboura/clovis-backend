"""
Dossiers/sous-dossiers dans la bibliothèque personnelle (22/08/2026,
demande explicite de Bourama). Voir migrations/2026_08_22_dossiers_
bibliotheque.sql pour le schéma.

Principes confirmés par Bourama :
- Un dossier peut avoir des sous-dossiers (dossier_parent_id).
- Un fichier peut être rangé dans PLUSIEURS dossiers à la fois
  (table de liaison many-to-many fichiers_dossiers_bibliotheque), pas
  de dossier parent unique par fichier.
- Un dossier peut mélanger librement plusieurs types de fichiers
  (image + audio + document...).
- Les dossiers sont une couche PAR DESSUS l'existant : un fichier peut
  très bien n'être dans aucun dossier (rester "libre", visible tel
  quel dans "Tous" comme aujourd'hui) : l'ajout d'un fichier ne
  requiert jamais de choisir un dossier.
- Suppression d'un dossier : un fichier encore rattaché à au moins un
  autre dossier est seulement détaché de celui-ci (conservé). Un
  fichier qui n'était rattaché à AUCUN autre dossier est supprimé en
  même temps que le dossier (voir supprimer_dossier ci-dessous).

Sans rapport avec core/bibliotheque_programme.py (classement dans le
Programme, matière/chapitre) : ce fichier ne touche que l'organisation
interne de la bibliothèque personnelle elle-même.
"""

import logging

from api.auth import supabase
from core.bibliotheque_fichiers import supprimer_fichier

logging.basicConfig(level=logging.INFO)


def _proprietaire_dossier(dossier_id: str) -> str | None:
    """Renvoie le user_id propriétaire d'un dossier, ou None s'il n'existe pas."""
    res = supabase.table("dossiers_bibliotheque").select("user_id").eq("id", dossier_id).maybe_single().execute()
    return res.data["user_id"] if res and res.data else None


def creer_dossier(user_id: str, nom: str, dossier_parent_id: str = None) -> dict:
    """
    Crée un dossier à la racine de la bibliothèque de l'utilisateur, ou
    comme sous-dossier de `dossier_parent_id` si fourni. La vérification
    que `dossier_parent_id` appartient bien à `user_id` est à la charge
    de l'appelant (route API / outil MCP), comme la convention déjà en
    place dans bibliotheque_programme.py pour les emplacements.
    """
    insertion = supabase.table("dossiers_bibliotheque").insert({
        "user_id": user_id,
        "nom": nom,
        "dossier_parent_id": dossier_parent_id,
    }).execute()
    return insertion.data[0]


def renommer_dossier(dossier_id: str, nouveau_nom: str) -> None:
    supabase.table("dossiers_bibliotheque").update({"nom": nouveau_nom}).eq("id", dossier_id).execute()


def lister_dossiers(user_id: str) -> list:
    """
    Liste TOUS les dossiers de l'utilisateur, à plat (id, nom,
    dossier_parent_id), à l'appelant de reconstruire l'arborescence
    si besoin, comme fait côté frontend pour un arbre de composants.
    """
    return (
        supabase.table("dossiers_bibliotheque")
        .select("id, nom, dossier_parent_id, created_at")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
        .data
    )


def lister_fichiers_ids_dossier(dossier_id: str) -> list:
    """Renvoie la liste des fichier_id directement rattachés à ce dossier (pas les sous-dossiers)."""
    res = supabase.table("fichiers_dossiers_bibliotheque").select("fichier_id").eq("dossier_id", dossier_id).execute()
    return [ligne["fichier_id"] for ligne in res.data]


def lister_dossiers_du_fichier(fichier_id: str) -> list:
    """
    Renvoie les dossiers (id + nom) dans lesquels ce fichier est rangé,
    utilisé pour l'affichage "classé dans : ..." déjà en place pour
    le Programme (voir lister_emplacements_document), même principe
    ici pour les dossiers.
    """
    res = (
        supabase.table("fichiers_dossiers_bibliotheque")
        .select("dossier_id, dossiers_bibliotheque(id, nom)")
        .eq("fichier_id", fichier_id)
        .execute()
    )
    return [ligne["dossiers_bibliotheque"] for ligne in res.data if ligne.get("dossiers_bibliotheque")]


def ranger_fichier(fichier_id: str, dossier_id: str) -> None:
    """Rattache un fichier à un dossier. Idempotent (clé primaire composite fichier_id+dossier_id)."""
    supabase.table("fichiers_dossiers_bibliotheque").upsert({
        "fichier_id": fichier_id,
        "dossier_id": dossier_id,
    }).execute()


def retirer_fichier(fichier_id: str, dossier_id: str) -> None:
    """Détache un fichier d'un dossier (le fichier lui-même n'est jamais touché ici)."""
    supabase.table("fichiers_dossiers_bibliotheque").delete().eq("fichier_id", fichier_id).eq("dossier_id", dossier_id).execute()


def supprimer_dossier(dossier_id: str) -> None:
    """
    Supprime un dossier. Comportement confirmé par Bourama : un fichier
    encore rattaché à au moins un AUTRE dossier après cette suppression
    est conservé (juste détaché de celui-ci) ; un fichier qui n'était
    rattaché à AUCUN autre dossier est supprimé de la bibliothèque en
    même temps que le dossier.

    Les sous-dossiers (dossier_parent_id -> ON DELETE CASCADE) sont
    supprimés en cascade par la base, mais leurs propres fichiers
    passent par la même règle : on calcule donc d'abord la liste
    complète (ce dossier + tous ses descendants) avant de trancher quel
    fichier part avec.
    """
    tous_dossiers = lister_dossiers(_proprietaire_dossier(dossier_id))
    a_supprimer = {dossier_id}
    changement = True
    while changement:
        changement = False
        for d in tous_dossiers:
            if d["dossier_parent_id"] in a_supprimer and d["id"] not in a_supprimer:
                a_supprimer.add(d["id"])
                changement = True

    fichiers_concernes = set()
    for d_id in a_supprimer:
        fichiers_concernes.update(lister_fichiers_ids_dossier(d_id))

    fichiers_a_supprimer = []
    for f_id in fichiers_concernes:
        autres_dossiers = {
            d["id"] for d in lister_dossiers_du_fichier(f_id)
        } - a_supprimer
        if not autres_dossiers:
            fichiers_a_supprimer.append(f_id)

    # Suppression des dossiers (cascade sur fichiers_dossiers_bibliotheque
    # automatique via la contrainte ON DELETE CASCADE de la migration).
    supabase.table("dossiers_bibliotheque").delete().eq("id", dossier_id).execute()

    for f_id in fichiers_a_supprimer:
        try:
            supprimer_fichier(f_id)
        except Exception as e:
            logging.error(f"ERREUR suppression fichier {f_id} suite à suppression dossier {dossier_id} : {e}")
