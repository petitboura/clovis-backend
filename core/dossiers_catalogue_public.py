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


# --- Attachement d'un dossier public à la bibliothèque personnelle -----
# (02/09/2026, demande Bourama : "ajouter un dossier public à sa
# bibliothèque perso, peu importe la contribution, et pouvoir librement
# le nourrir depuis sa bibliothèque personnelle"). Voir migrations/
# 2026_09_02_dossiers_publics_attaches.sql.
#
# Attacher est libre pour N'IMPORTE QUEL dossier public, quel que soit
# son statut, même créé par quelqu'un d'autre -- ce n'est qu'un
# raccourci/vue dans la bibliothèque perso, aucun droit supplémentaire.
# Le droit d'y AJOUTER un document (nourrir) continue de suivre
# EXACTEMENT peut_ajouter_contenu ci-dessus, pas de nouvelle règle.

def attacher_dossier(user_id: str, dossier_public_id: str) -> None:
    """Attache un dossier public à la bibliothèque perso de user_id. Idempotent (clé primaire composite)."""
    supabase.table("dossiers_publics_attaches").upsert({
        "user_id": user_id,
        "dossier_public_id": dossier_public_id,
    }).execute()


def detacher_dossier(user_id: str, dossier_public_id: str) -> None:
    """Détache un dossier public de la bibliothèque perso de user_id -- ne touche jamais au dossier public lui-même ni à son contenu."""
    supabase.table("dossiers_publics_attaches").delete().eq("user_id", user_id).eq("dossier_public_id", dossier_public_id).execute()


def lister_dossiers_attaches(user_id: str) -> list:
    """
    Liste les dossiers publics attachés à la bibliothèque perso de
    user_id, avec les mêmes champs que lister_dossiers() plus
    peut_ajouter (calculé pour user_id) afin que l'appelant (route API)
    sache directement si le bouton d'ajout doit publier dans le dossier
    public ou rester en privé -- voir docstring du module.
    """
    res = (
        supabase.table("dossiers_publics_attaches")
        .select("dossier_public_id, dossiers_catalogue_public(id, cree_par, nom, statut, dossier_parent_id, created_at)")
        .eq("user_id", user_id)
        .execute()
    )
    dossiers = []
    for ligne in res.data:
        dossier = ligne.get("dossiers_catalogue_public")
        if not dossier:
            continue  # dossier public supprimé entre-temps -- ligne orpheline ignorée, cascade Supabase la nettoiera
        dossier["fichier_ids"] = lister_fichiers_ids_dossier(dossier["id"])
        dossier["peut_ajouter"] = peut_ajouter_contenu(dossier["id"], user_id)
        dossiers.append(dossier)
    return dossiers
