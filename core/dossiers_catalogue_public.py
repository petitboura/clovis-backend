"""
Dossiers du catalogue public (28/08/2026, demande Bourama). Voir
migrations/2026_08_28_dossiers_catalogue_public.sql pour le schéma.

Différence clé avec core/dossiers_bibliotheque.py (perso) : un dossier
public a un `statut` choisi par son créateur à la création --
'contribution_libre' (tout utilisateur connecté peut y AJOUTER un
document) ou 'privee' (seul le créateur le peut). CORRECTIF 28/08
(Bourama : "tout le monde ne peut pas retirer un dossier public, ni
lui ni ses fichiers, seulement le créateur") : contribution_libre
donne le droit d'AJOUTER, jamais de RETIRER -- retirer un fichier d'un
dossier, renommer le dossier ou le supprimer restent réservés au
créateur dans TOUS les cas, y compris contribution_libre.

Supprimer un dossier public NE supprime JAMAIS les documents qu'il
contenait (contrairement au perso) : ce sont des ressources partagées
par toute la communauté, pas la propriété du dossier.
"""

from api.auth import supabase


def _dossier(dossier_id: str) -> dict | None:
    res = (
        supabase.table("dossiers_catalogue_public")
        .select("id, cree_par, nom, statut, dossier_parent_id")
        .eq("id", dossier_id)
        .maybe_single()
        .execute()
    )
    return res.data if res and res.data else None


def peut_ajouter_contenu(dossier_id: str, user_id: str) -> bool:
    """Renvoie True si `user_id` peut RANGER un document dans ce dossier (créateur toujours, ou n'importe qui si statut='contribution_libre')."""
    dossier = _dossier(dossier_id)
    if not dossier:
        return False
    return dossier["statut"] == "contribution_libre" or dossier["cree_par"] == user_id


def peut_retirer_contenu(dossier_id: str, user_id: str) -> bool:
    """Renvoie True si `user_id` peut RETIRER un document de ce dossier -- réservé au créateur, MÊME si statut='contribution_libre' (28/08, correctif Bourama)."""
    dossier = _dossier(dossier_id)
    if not dossier:
        return False
    return dossier["cree_par"] == user_id


def creer_dossier(user_id: str, nom: str, statut: str = "contribution_libre", dossier_parent_id: str = None) -> dict:
    insertion = supabase.table("dossiers_catalogue_public").insert({
        "cree_par": user_id,
        "nom": nom,
        "statut": statut,
        "dossier_parent_id": dossier_parent_id,
    }).execute()
    return insertion.data[0]


def renommer_dossier(dossier_id: str, nouveau_nom: str) -> None:
    supabase.table("dossiers_catalogue_public").update({"nom": nouveau_nom}).eq("id", dossier_id).execute()


def lister_dossiers() -> list:
    """Liste TOUS les dossiers du catalogue public, à plat -- visible par tout le monde, contrairement au perso."""
    return (
        supabase.table("dossiers_catalogue_public")
        .select("id, cree_par, nom, statut, dossier_parent_id, created_at")
        .order("created_at")
        .execute()
        .data
    )


def lister_fichiers_ids_dossier(dossier_id: str) -> list:
    res = (
        supabase.table("fichiers_dossiers_catalogue_public")
        .select("fichier_id")
        .eq("dossier_id", dossier_id)
        .execute()
    )
    return [ligne["fichier_id"] for ligne in res.data]


def ranger_fichier(fichier_id: str, dossier_id: str) -> None:
    supabase.table("fichiers_dossiers_catalogue_public").upsert({
        "fichier_id": fichier_id,
        "dossier_id": dossier_id,
    }).execute()


def retirer_fichier(fichier_id: str, dossier_id: str) -> None:
    supabase.table("fichiers_dossiers_catalogue_public").delete().eq("fichier_id", fichier_id).eq("dossier_id", dossier_id).execute()


def supprimer_dossier(dossier_id: str) -> None:
    """Supprime le dossier (et ses sous-dossiers, ON DELETE CASCADE) -- ne touche JAMAIS aux documents eux-mêmes, voir docstring du module."""
    supabase.table("dossiers_catalogue_public").delete().eq("id", dossier_id).execute()
