"""
Ajoute le 23/08/2026, Bourama : Lot 1 Partie 3 (app mobile), socle.

Canal dedie entre l'app mobile Clovis (Android/iOS, depot
clovis-mobile) et ce backend. Reutilise l'auth Supabase standard deja
en place (voir api/auth.py) : l'app mobile se connecte directement a
Supabase avec le SDK natif, puis envoie son access_token en Bearer sur
ces routes, exactement comme le fait clovis-frontend.

Pour l'instant, une seule capacite : synchroniser le temps passe par
app (UsageStatsManager cote Android). Les autres capacites du Lot 1
(fichiers, notifications, DND, connecteurs -- lots 2 a 5) viendront
sur ce meme routeur ou des routeurs freres, a construire lot par lot.
"""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.usage_appareil_mobile import enregistrer_usage, lire_usage

router = APIRouter(prefix="/api/appareils-mobiles", tags=["appareils-mobiles"])


class EntreeUsage(BaseModel):
    nom_app: str
    date: str  # "AAAA-MM-JJ"
    duree_secondes: int


class SynchronisationUsage(BaseModel):
    plateforme: str  # "android" ou "ios"
    entrees: list[EntreeUsage]


@router.post("/usage", status_code=204)
def synchroniser_usage(payload: SynchronisationUsage, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : appeler a chaque ouverture de l'ecran usage (ou
    en tache de fond periodique plus tard), avec le total du jour par
    app tel que calcule cote telephone. Ecrase la valeur precedente
    pour chaque (app, jour) concerne.
    """
    if payload.plateforme not in ("android", "ios"):
        raise erreur_api(400, "PLATEFORME_INCONNUE")

    try:
        enregistrer_usage(
            utilisateur.id,
            payload.plateforme,
            [entree.model_dump() for entree in payload.entrees],
        )
    except Exception as e:
        logging.error(f"ERREUR synchronisation usage mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_SYNCHRONISATION_USAGE")


@router.get("/usage")
def obtenir_usage(jours: int = 7, utilisateur=Depends(utilisateur_courant)):
    """
    Renvoie l'usage des `jours` derniers jours (7 par defaut) pour
    l'ecran minimal du Lot 1. `jours` reste un parametre, jamais une
    valeur figee cote frontend mobile.
    """
    if jours < 1 or jours > 90:
        raise erreur_api(400, "PLAGE_JOURS_INVALIDE")

    aujourdhui = date.today()
    depuis = (aujourdhui - timedelta(days=jours - 1)).isoformat()
    jusqua = aujourdhui.isoformat()

    try:
        lignes = lire_usage(utilisateur.id, depuis, jusqua)
    except Exception as e:
        logging.error(f"ERREUR lecture usage mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_LECTURE_USAGE")

    return {"usage": lignes}
