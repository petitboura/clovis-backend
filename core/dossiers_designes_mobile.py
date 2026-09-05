"""
Cree le 26/08/2026, Bourama : brancher le cerveau (suite Lot 1A/1B,
actions_appareil_mobile.py), capacite "dossiers".

Miroir cote backend de la liste des dossiers designes par l'etudiant sur
son telephone (SAF Android / security-scoped bookmarks iOS). Contient
UNIQUEMENT le nom de chaque dossier, jamais l'URI/bookmark reel : l'URI
est propre a l'appareil, n'a aucun sens cote serveur, et l'agent ne doit
jamais la manipuler, voir migrations/2026_08_26_dossiers_designes_mobile.sql.

Synchronisation en mode MIROIR COMPLET, pas un upsert comme
usage_appareil_mobile.py : un dossier peut etre retire par l'etudiant,
donc a chaque synchronisation on remplace l'ensemble des lignes de
(user_id, appareil_id) par la liste envoyee, plutot que d'accumuler des
noms perimes. Appele par l'app (DossiersPlugin) apres chaque changement
(ajout/retrait) ET a chaque ouverture, voir
api/appareils_mobiles.py::synchroniser_dossiers.

Ajoute le 04/09/2026, Bourama : correction du bug "deux telephones
Android du meme compte se melangent" -- le miroir etait indexe par
(user_id, plateforme) uniquement, donc deux appareils de la meme
plateforme ecrasaient la liste l'un de l'autre. Indexe desormais par
(user_id, appareil_id) : `appareil_id` est un UUID genere une fois par
l'app et persiste localement (voir IdentifiantAppareil.kt/.swift cote
clovis-frontend), `plateforme` reste stockee pour info mais ne sert
plus de cle. `appareil_nom` est un libelle lisible (choisi par
l'etudiant ou genere par defaut a partir du modele du telephone) affiche
par l'agent SEULEMENT quand deux appareils partagent un meme nom de
dossier (voir _formatter_dossiers dans core/serveur_mcp_generation.py),
voir migrations/2026_09_04_appareil_id_ciblage.sql.
"""

from api.auth import supabase


def synchroniser_dossiers_designes(
    user_id: str, plateforme: str, appareil_id: str, appareil_nom: str | None, noms: list[str]
) -> None:
    """
    Remplace la liste complete des dossiers designes pour cet
    utilisateur et CET APPAREIL (plus seulement cette plateforme,
    depuis le 04/09/2026). `noms` peut etre vide (tous les dossiers ont
    ete retires), dans ce cas on supprime simplement toutes les lignes
    existantes pour cet appareil.
    """
    supabase.table("dossiers_designes_mobile").delete().eq("user_id", user_id).eq(
        "appareil_id", appareil_id
    ).execute()

    if not noms:
        return

    lignes = [
        {
            "user_id": user_id,
            "plateforme": plateforme,
            "appareil_id": appareil_id,
            "appareil_nom": appareil_nom,
            "nom": nom,
        }
        for nom in noms
    ]
    supabase.table("dossiers_designes_mobile").insert(lignes).execute()


def lire_dossiers_designes(user_id: str) -> list[dict]:
    """
    Renvoie les dossiers designes de cet utilisateur, tous appareils
    confondus (un meme nom peut exister sur plusieurs appareils sans
    lien entre eux, ce sont des dossiers distincts). Utilise par
    l'outil agent gerer_dossier_telephone (action "lister_dossiers",
    voir core/serveur_mcp_generation.py) pour savoir quels noms cibler
    AVANT de creer une action dessus, et par resoudre_appareil_cible
    ci-dessous pour trouver l'appareil proprietaire d'un nom donne.
    """
    resultat = (
        supabase.table("dossiers_designes_mobile")
        .select("nom, plateforme, appareil_id, appareil_nom")
        .eq("user_id", user_id)
        .order("nom")
        .execute()
    )
    return resultat.data or []


def resoudre_appareil_cible(
    user_id: str, dossier_nom: str, appareil_nom: str | None = None
) -> tuple[str | None, str | None]:
    """
    Ajoute le 04/09/2026, Bourama : point de resolution UNIQUE, reutilise
    par gerer_dossier_telephone (action "executer") ET explorer_dossier,
    pour ne jamais deviner quel appareil possede le dossier `dossier_nom`
    quand l'etudiant a plusieurs telephones.

    Renvoie (appareil_id, erreur) :
    - (id, None) si un seul appareil possede un dossier designe nomme
      `dossier_nom`, ou si plusieurs le possedent mais que `appareil_nom`
      (fourni par l'agent) permet de trancher sans ambiguite ;
    - (None, message) si le dossier n'existe sur aucun appareil, ou si
      plusieurs appareils le possedent et qu'aucun `appareil_nom` ne
      permet de choisir -- l'agent doit alors relayer le message et
      reappeler avec "appareil_nom" precise (voir la liste renvoyee par
      "lister_dossiers", qui indique le libelle de chaque appareil des
      qu'une collision existe).
    """
    candidats = [d for d in lire_dossiers_designes(user_id) if d["nom"] == dossier_nom]

    if not candidats:
        return None, f'Dossier "{dossier_nom}" introuvable sur aucun appareil de l\'étudiant.'

    if len(candidats) == 1:
        return candidats[0]["appareil_id"], None

    if appareil_nom:
        correspondants = [
            d for d in candidats
            if (d.get("appareil_nom") or d["plateforme"]) == appareil_nom
        ]
        if len(correspondants) == 1:
            return correspondants[0]["appareil_id"], None

    libelles = ", ".join(sorted({d.get("appareil_nom") or d["plateforme"] for d in candidats}))
    return None, (
        f'Plusieurs appareils ont un dossier nommé "{dossier_nom}" ({libelles}). '
        'Précise "appareil_nom" pour lever l\'ambiguïté.'
    )
