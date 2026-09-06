"""Partie 6 (plan confiance pédagogique, 06/09/2026, demande Bourama) --
voir core/mode_actif_conversation.py. Léger : deux routes, lire et
définir le mode actif d'une conversation.

Partie 7 (06/09) : pour un utilisateur mineur (profiles.est_majeur =
false explicitement), le premier choix fait pour une conversation est
définitif -- "pas de changement libre en cours de conversation" (voir
core/restriction_mineur.py). Le champ `verrouille` de la réponse GET dit
au frontend s'il doit désactiver le sélecteur.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.mode_actif_conversation import obtenir_mode_actif, definir_mode_actif
from core.restriction_mineur import est_mineur

router_mode_actif = APIRouter(prefix="/api/conversations", tags=["mode_actif"])


@router_mode_actif.get("/{conversation_id}/mode-actif")
def lire(conversation_id: str, utilisateur=Depends(utilisateur_courant)):
    ligne = obtenir_mode_actif(conversation_id, utilisateur.id)
    rattachement_id = ligne["rattachement_id"] if ligne else None
    verrouille = bool(rattachement_id) and est_mineur(utilisateur.id)
    return {"rattachement_id": rattachement_id, "verrouille": verrouille}


class ModeActifPayload(BaseModel):
    rattachement_id: str | None = None


@router_mode_actif.put("/{conversation_id}/mode-actif")
def definir(conversation_id: str, payload: ModeActifPayload, utilisateur=Depends(utilisateur_courant)):
    if est_mineur(utilisateur.id):
        ligne_actuelle = obtenir_mode_actif(conversation_id, utilisateur.id)
        deja_choisi = ligne_actuelle["rattachement_id"] if ligne_actuelle else None
        if deja_choisi and deja_choisi != payload.rattachement_id:
            raise erreur_api(403, "MODE_VERROUILLE_MINEUR")
    resultat = definir_mode_actif(conversation_id, utilisateur.id, payload.rattachement_id)
    if resultat is None and payload.rattachement_id is not None:
        raise erreur_api(404, "RATTACHEMENT_INTROUVABLE")
    return {"rattachement_id": resultat["rattachement_id"] if resultat else None}
