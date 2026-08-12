"""
Routes pour les notifications push. Contrairement aux autres routes
generation_*.py, celles-ci ne génèrent rien : elles gèrent l'abonnement
du navigateur (obligatoirement frontend, voir CONTRAT ci-dessous) et
exposent la clé publique VAPID nécessaire à `pushManager.subscribe()`.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.notifications_push import (
    enregistrer_abonnement,
    supprimer_abonnement,
    cle_publique_vapid,
    notifications_push_disponible,
)

router = APIRouter(prefix="/api/notifications-push", tags=["notifications-push"])


class Cles(BaseModel):
    p256dh: str
    auth: str


class Abonnement(BaseModel):
    endpoint: str
    keys: Cles


class Desabonnement(BaseModel):
    endpoint: str


@router.get("/cle-publique")
def obtenir_cle_publique():
    """
    CONTRAT FRONTEND : appeler cette route avant `pushManager.subscribe()`,
    passer la valeur renvoyée comme `applicationServerKey` (déjà en
    base64url, à convertir en Uint8Array côté JS -- fonction utilitaire
    standard "urlBase64ToUint8Array", largement documentée pour l'API
    Push).
    """
    if not notifications_push_disponible():
        raise erreur_api(503, "NOTIFICATIONS_PUSH_INDISPONIBLE")
    return {"cle_publique": cle_publique_vapid()}


@router.post("/abonnement", status_code=204)
def s_abonner(abonnement: Abonnement, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT FRONTEND : appeler après `pushManager.subscribe()` avec
    l'objet subscription tel quel (JSON.stringify du résultat, il a
    déjà exactement cette forme : {endpoint, keys: {p256dh, auth}}).
    """
    try:
        enregistrer_abonnement(utilisateur.id, abonnement.model_dump())
    except Exception as e:
        logging.error(f"ERREUR abonnement push (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DE_L")


@router.post("/desabonnement", status_code=204)
def se_desabonner(desabonnement: Desabonnement, utilisateur=Depends(utilisateur_courant)):
    try:
        supprimer_abonnement(utilisateur.id, desabonnement.endpoint)
    except Exception as e:
        logging.error(f"ERREUR desabonnement push (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DU_DESABONNEMENT")
