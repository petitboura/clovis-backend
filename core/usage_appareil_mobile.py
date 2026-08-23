"""
Ajoute le 23/08/2026, Bourama : Lot 1 Partie 3 (app mobile), socle.

Stocke le temps passe par app, remonte par l'app mobile Clovis
(Android via UsageStatsManager, iOS via Screen Time/Family Controls si
l'entitlement est obtenu -- voir 01-socle-app-android.md dans le
chantier "programme adaptatif etudiant, partie 3").

Une ligne = temps total sur UNE app, pour UN utilisateur, UNE
plateforme, UN jour donne (voir contrainte unique en base). L'app
mobile envoie un upsert par app a chaque synchronisation : on ecrase
la duree du jour avec la valeur la plus recente calculee cote
telephone (le telephone connait le vrai total du jour via l'API
systeme, pas la peine d'additionner cote serveur).

"App actuellement active" (deuxieme info demandee par le Lot 1) n'est
PAS stockee ici : c'est une info instantanee (quelle app est ouverte
MAINTENANT), sans interet a persister en base. Elle est calculee et
affichee directement cote telephone, voir l'ecran usage de l'app
mobile.
"""

from api.auth import supabase


def enregistrer_usage(user_id: str, plateforme: str, entrees: list[dict]) -> None:
    """
    `entrees` : liste de {"nom_app": str, "date": "AAAA-MM-JJ", "duree_secondes": int}
    envoyee par l'app mobile en une seule synchronisation (potentiellement
    plusieurs apps/jours a la fois).

    Upsert sur (user_id, plateforme, nom_app, date) : ecrase la duree
    existante par la nouvelle valeur du telephone.
    """
    if not entrees:
        return

    lignes = [
        {
            "user_id": user_id,
            "plateforme": plateforme,
            "nom_app": entree["nom_app"],
            "date": entree["date"],
            "duree_secondes": entree["duree_secondes"],
        }
        for entree in entrees
    ]

    supabase.table("usage_appareil_mobile").upsert(
        lignes, on_conflict="user_id,plateforme,nom_app,date"
    ).execute()


def lire_usage(user_id: str, depuis: str, jusqua: str) -> list[dict]:
    """
    Renvoie les lignes d'usage de l'utilisateur entre deux dates
    (incluses, format "AAAA-MM-JJ"), toutes plateformes confondues.
    """
    resultat = (
        supabase.table("usage_appareil_mobile")
        .select("plateforme, nom_app, date, duree_secondes")
        .eq("user_id", user_id)
        .gte("date", depuis)
        .lte("date", jusqua)
        .order("date", desc=True)
        .execute()
    )
    return resultat.data or []
