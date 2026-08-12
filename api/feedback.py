"""
Feedback (like/dislike) sur les messages assistant.

Règle de confidentialité stricte : le créateur ne reçoit JAMAIS le contenu
d'une conversation par défaut. Cette route n'expose le contenu de la
réponse assistant / de la question utilisateur QUE si reponse_partagee /
question_partagee ont été explicitement cochés par l'auteur du feedback --
c'est cette route (pas la RLS, qui gère seulement "qui voit la LIGNE de
feedback") qui fait respecter cette règle, en ne renvoyant jamais les
colonnes concernées si le booléen correspondant est faux.

La LIGNE de notification elle-même est créée par un trigger Postgres
(notifier_nouveau_feedback(), voir migration trigger_notif_feedback),
cohérent avec le pattern déjà en place pour follows/comments/ratings
(voir api/notifications.py) -- cette route ne fait qu'insérer le feedback,
jamais la notification directement.
"""

import logging
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class EnvoyerFeedbackPayload(BaseModel):
    conversation_id: str
    message_id: int  # id (historique_conversations) de la réponse assistant concernée
    question_message_id: Optional[int] = None  # id de la question utilisateur juste avant
    agent_id: str
    type: Literal["positif", "negatif"]
    categorie: Optional[str] = None  # uniquement pertinent si type="negatif"
    commentaire: Optional[str] = None
    reponse_partagee: bool = False
    question_partagee: bool = False


class FeedbackCree(BaseModel):
    id: int


@router.post("", response_model=FeedbackCree, status_code=201)
def envoyer_feedback(payload: EnvoyerFeedbackPayload, utilisateur=Depends(utilisateur_courant)):
    """
    Nécessite d'être connecté (contrairement au chat lui-même) : un
    feedback anonyme ne serait pas exploitable (pas de scope RLS possible
    côté lecture créateur) et n'a pas été demandé dans la spec.
    """
    try:
        res = (
            supabase.table("feedback_messages")
            .insert({
                "conversation_id": payload.conversation_id,
                "message_id": payload.message_id,
                "question_message_id": payload.question_message_id,
                "agent_id": payload.agent_id,
                "user_id": utilisateur.id,
                "type": payload.type,
                "categorie": payload.categorie if payload.type == "negatif" else None,
                "commentaire": payload.commentaire,
                "reponse_partagee": payload.reponse_partagee,
                "question_partagee": payload.question_partagee,
            })
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion feedback, agent_id={payload.agent_id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_D_ENVOYER_CE_RETOUR_POUR")

    lignes = res.data or []
    if not lignes:
        raise erreur_api(500, "IMPOSSIBLE_D_ENVOYER_CE_RETOUR_POUR")

    return FeedbackCree(id=lignes[0]["id"])


# Catégories de retour négatif (menu déroulant du popup -- "à définir selon le
# contexte Djiguignè AI"). Décision provisoire, à ajuster avec Bourama ;
# exposée via une route dédiée pour que le frontend n'ait pas à les
# dupliquer en dur.
CATEGORIES_RETOUR_NEGATIF = [
    {"id": "hors_sujet", "libelle": "Réponse hors sujet"},
    {"id": "information_incorrecte", "libelle": "Information incorrecte"},
    {"id": "ne_suit_pas_instructions", "libelle": "Ne suit pas les instructions"},
    {"id": "ton_inapproprie", "libelle": "Ton inapproprié"},
    {"id": "incomprehension", "libelle": "Ne comprend pas la question"},
    {"id": "autre", "libelle": "Autre"},
]


@router.get("/categories")
def lister_categories_negatif():
    return {"categories": CATEGORIES_RETOUR_NEGATIF}
