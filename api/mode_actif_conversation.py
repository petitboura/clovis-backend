"""Partie 6 (plan confiance pédagogique, 06/09/2026, demande Bourama) --
voir core/mode_actif_conversation.py. Léger : deux routes, lire et
définir le mode actif d'une conversation.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.mode_actif_conversation import obtenir_mode_actif, definir_mode_actif

router_mode_actif = APIRouter(prefix="/api/conversations", tags=["mode_actif"])


@router_mode_actif.get("/{conversation_id}/mode-actif")
def lire(conversation_id: str, utilisateur=Depends(utilisateur_courant)):
    ligne = obtenir_mode_actif(conversation_id, utilisateur.id)
    return {"rattachement_id": ligne["rattachement_id"] if ligne else None}


class ModeActifPayload(BaseModel):
    rattachement_id: str | None = None


@router_mode_actif.put("/{conversation_id}/mode-actif")
def definir(conversation_id: str, payload: ModeActifPayload, utilisateur=Depends(utilisateur_courant)):
    resultat = definir_mode_actif(conversation_id, utilisateur.id, payload.rattachement_id)
    if resultat is None and payload.rattachement_id is not None:
        raise erreur_api(404, "RATTACHEMENT_INTROUVABLE")
    return {"rattachement_id": resultat["rattachement_id"] if resultat else None}
