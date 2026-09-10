"""
Signalements pédagogiques élève -> prof (refonte du 10/09/2026). Voir
core/signalements.py pour le détail complet du système.

Nom volontairement différent de api/signalements.py (droit d'auteur
bibliothèque publique) pour ne jamais confondre les deux -- aucun
rapport entre les deux systèmes.

Toutes les routes de gestion sont réservées au prof PROPRIÉTAIRE du
signalement (vérifié côté core/signalements.py via signalements.prof_id),
404 si le signalement n'existe pas OU n'appartient pas à l'appelant,
jamais de distinction (pas de fuite d'information).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.signalements import (
    creer_signalement as _creer_signalement,
    lister_signalements_prof as _lister_signalements_prof,
    lister_signalements_etudiant as _lister_signalements_etudiant,
    obtenir_signalement as _obtenir_signalement,
    demander_visibilite as _demander_visibilite,
    confirmer_visibilite as _confirmer_visibilite,
    ouvrir_discussion as _ouvrir_discussion,
    rattacher_signalement as _rattacher_signalement,
    dupliquer_signalement as _dupliquer_signalement,
    supprimer_signalement as _supprimer_signalement,
)

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/signalements-pedagogiques", tags=["signalements_pedagogiques"])


class CreerSignalementPayload(BaseModel):
    agent_id: str
    conversation_id: Optional[str] = None
    question_message_id: Optional[int] = None
    reponse_message_id: Optional[int] = None
    question_texte: str
    reponse_texte: str
    probleme_observe: Optional[str] = None
    visible_question: bool = True
    visible_reponse: bool = True
    visible_conversation: bool = False


class DemanderVisibilitePayload(BaseModel):
    champs: list[str]


class ConfirmerVisibilitePayload(BaseModel):
    reponses: dict[str, bool]


class OuvrirDiscussionPayload(BaseModel):
    conversation_discussion_id: str


class RattacherPayload(BaseModel):
    code_id: Optional[str] = None
    notion_id: Optional[str] = None


@router.post("", status_code=201)
def signaler(payload: CreerSignalementPayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _creer_signalement(
        agent_id=payload.agent_id,
        etudiant_id=utilisateur.id,
        conversation_id=payload.conversation_id,
        question_message_id=payload.question_message_id,
        reponse_message_id=payload.reponse_message_id,
        question_texte=payload.question_texte,
        reponse_texte=payload.reponse_texte,
        probleme_observe=payload.probleme_observe,
        visible_question=payload.visible_question,
        visible_reponse=payload.visible_reponse,
        visible_conversation=payload.visible_conversation,
    )
    if ligne is None:
        raise erreur_api(500, "IMPOSSIBLE_D_ENREGISTRER_CE_SIGNALEMENT")
    return ligne


@router.get("/mes-signalements")
def mes_signalements(utilisateur=Depends(utilisateur_courant)):
    return {"signalements": _lister_signalements_etudiant(utilisateur.id)}


@router.get("/recus")
def recus(statut: Optional[str] = None, utilisateur=Depends(utilisateur_courant)):
    return {"signalements": _lister_signalements_prof(utilisateur.id, statut=statut)}


def _signalement_visible_ou_404(signalement_id: str, utilisateur):
    ligne = _obtenir_signalement(signalement_id)
    if ligne is None or (ligne["etudiant_id"] != utilisateur.id and ligne["prof_id"] != utilisateur.id):
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.get("/{signalement_id}")
def obtenir(signalement_id: str, utilisateur=Depends(utilisateur_courant)):
    return _signalement_visible_ou_404(signalement_id, utilisateur)


@router.post("/{signalement_id}/demander-visibilite")
def demander_visibilite(signalement_id: str, payload: DemanderVisibilitePayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _demander_visibilite(signalement_id, utilisateur.id, payload.champs)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.post("/{signalement_id}/confirmer-visibilite")
def confirmer_visibilite(signalement_id: str, payload: ConfirmerVisibilitePayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _confirmer_visibilite(signalement_id, utilisateur.id, payload.reponses)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.post("/{signalement_id}/ouvrir-discussion")
def ouvrir_discussion(signalement_id: str, payload: OuvrirDiscussionPayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _ouvrir_discussion(signalement_id, utilisateur.id, payload.conversation_discussion_id)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.patch("/{signalement_id}/rattacher")
def rattacher(signalement_id: str, payload: RattacherPayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _rattacher_signalement(signalement_id, utilisateur.id, payload.code_id, payload.notion_id)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.post("/{signalement_id}/dupliquer", status_code=201)
def dupliquer(signalement_id: str, utilisateur=Depends(utilisateur_courant)):
    ligne = _dupliquer_signalement(signalement_id, utilisateur.id)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.delete("/{signalement_id}", status_code=204)
def supprimer(signalement_id: str, utilisateur=Depends(utilisateur_courant)):
    if not _supprimer_signalement(signalement_id, utilisateur.id):
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
