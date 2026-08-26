"""
Cree le 26/08/2026, Bourama : brancher le cerveau (suite Lot 1A/1B,
actions_appareil_mobile.py) -- capacite "dossiers".

Miroir cote backend de la liste des dossiers designes par l'etudiant sur
son telephone (SAF Android / security-scoped bookmarks iOS). Contient
UNIQUEMENT le nom de chaque dossier, jamais l'URI/bookmark reel : l'URI
est propre a l'appareil, n'a aucun sens cote serveur, et l'agent ne doit
jamais la manipuler -- voir migrations/2026_08_26_dossiers_designes_mobile.sql.

Synchronisation en mode MIROIR COMPLET, pas un upsert comme
usage_appareil_mobile.py : un dossier peut etre retire par l'etudiant,
donc a chaque synchronisation on remplace l'ensemble des lignes de
(user_id, plateforme) par la liste envoyee, plutot que d'accumuler des
noms perimes. Appele par l'app (DossiersPlugin) apres chaque changement
(ajout/retrait) ET a chaque ouverture, voir
api/appareils_mobiles.py::synchroniser_dossiers.
"""

from api.auth import supabase


def synchroniser_dossiers_designes(user_id: str, plateforme: str, noms: list[str]) -> None:
    """
    Remplace la liste complete des dossiers designes pour cet
    utilisateur et cette plateforme. `noms` peut etre vide (tous les
    dossiers ont ete retires) -- dans ce cas on supprime simplement
    toutes les lignes existantes.
    """
    supabase.table("dossiers_designes_mobile").delete().eq("user_id", user_id).eq(
        "plateforme", plateforme
    ).execute()

    if not noms:
        return

    lignes = [{"user_id": user_id, "plateforme": plateforme, "nom": nom} for nom in noms]
    supabase.table("dossiers_designes_mobile").insert(lignes).execute()


def lire_dossiers_designes(user_id: str) -> list[dict]:
    """
    Renvoie les dossiers designes de cet utilisateur, toutes plateformes
    confondues (un meme nom peut exister sur android ET ios sans lien
    entre les deux, ce sont deux telephones/dossiers distincts). Utilise
    par l'outil agent lister_dossiers_designes_mobile (voir
    core/serveur_mcp_generation.py) pour savoir quels noms cibler AVANT
    de creer une action dessus.
    """
    resultat = (
        supabase.table("dossiers_designes_mobile")
        .select("nom, plateforme")
        .eq("user_id", user_id)
        .order("nom")
        .execute()
    )
    return resultat.data or []
