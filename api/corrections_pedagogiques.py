"""
Corrections pédagogiques élève -> prof (Point 2, Partie 4, 06/09/2026).

Système entièrement séparé du like/dislike existant (api/feedback.py) et
sans rapport avec api/signalements.py (droit d'auteur bibliothèque
publique), voir core/corrections_pedagogiques.py pour le détail des
deux types (A/B) et de la résolution automatique du prof.

Toutes les routes de gestion (activer/dupliquer/éditer/supprimer/
déplacer) sont réservées au prof PROPRIÉTAIRE de la correction (vérifié
côté core/corrections_pedagogiques.py via corrections_pedagogiques.prof_id,
jamais l'appartenance étudiant), 404 si la correction n'existe pas OU
n'appartient pas à l'appelant, jamais de distinction (pas de fuite
d'information sur l'existence d'une correction d'un autre prof).
"""

import logging
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.corrections_pedagogiques import (
    creer_correction as _creer_correction,
    lister_corrections_prof as _lister_corrections_prof,
    lister_corrections_etudiant as _lister_corrections_etudiant,
    obtenir_correction as _obtenir_correction,
    desactiver_activer_correction as _desactiver_activer_correction,
    editer_correction as _editer_correction,
    dupliquer_correction as _dupliquer_correction,
    supprimer_correction as _supprimer_correction,
    deplacer_correction as _deplacer_correction,
)

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/corrections-pedagogiques", tags=["corrections_pedagogiques"])


class CreerCorrectionPayload(BaseModel):
    agent_id: str
    type: Literal["A", "B"]
    conversation_id: Optional[str] = None
    question_message_id: Optional[int] = None
    reponse_message_id: Optional[int] = None
    question_texte: str
    reponse_texte: str


class EditerCorrectionPayload(BaseModel):
    correction_texte: str


class ActiverPayload(BaseModel):
    actif: bool


class DeplacerPayload(BaseModel):
    lien_type: Optional[str] = None
    lien_id: Optional[str] = None


@router.post("", status_code=201)
def signaler(payload: CreerCorrectionPayload, utilisateur=Depends(utilisateur_courant)):
    """Un élève signale une réponse (type A : correctif de fond, type B :
    comportement mal configuré, destiné à la Partie 10)."""
    try:
        ligne = _creer_correction(
            agent_id=payload.agent_id,
            etudiant_id=utilisateur.id,
            type_=payload.type,
            conversation_id=payload.conversation_id,
            question_message_id=payload.question_message_id,
            reponse_message_id=payload.reponse_message_id,
            question_texte=payload.question_texte,
            reponse_texte=payload.reponse_texte,
        )
    except ValueError:
        raise erreur_api(400, "TYPE_DE_SIGNALEMENT_INVALIDE")
    if ligne is None:
        raise erreur_api(500, "IMPOSSIBLE_D_ENREGISTRER_CE_SIGNALEMENT")
    return ligne


@router.get("/mes-signalements")
def mes_signalements(type: Optional[str] = None, utilisateur=Depends(utilisateur_courant)):
    """Signalements envoyés par l'utilisateur courant (élève)."""
    return {"corrections": _lister_corrections_etudiant(utilisateur.id, type_=type)}


@router.get("/a-corriger")
def a_corriger(statut: Optional[str] = "nouveau", utilisateur=Depends(utilisateur_courant)):
    """Signalements de type A reçus par l'utilisateur courant (prof) :
    "nouveau" par défaut (en attente de correction)."""
    return {"corrections": _lister_corrections_prof(utilisateur.id, type_="A", statut=statut)}


def _correction_visible_ou_404(correction_id: str, utilisateur):
    ligne = _obtenir_correction(correction_id)
    if ligne is None or (ligne["etudiant_id"] != utilisateur.id and ligne["prof_id"] != utilisateur.id):
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.get("/{correction_id}")
def obtenir(correction_id: str, utilisateur=Depends(utilisateur_courant)):
    """Détail d'un signalement, visible par l'élève qui l'a envoyé ou
    le prof qui l'a reçu, personne d'autre."""
    return _correction_visible_ou_404(correction_id, utilisateur)


@router.patch("/{correction_id}/actif")
def activer_desactiver(correction_id: str, payload: ActiverPayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _desactiver_activer_correction(correction_id, utilisateur.id, payload.actif)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.patch("/{correction_id}")
def editer(correction_id: str, payload: EditerCorrectionPayload, utilisateur=Depends(utilisateur_courant)):
    ligne = _editer_correction(correction_id, utilisateur.id, payload.correction_texte)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.post("/{correction_id}/dupliquer", status_code=201)
def dupliquer(correction_id: str, utilisateur=Depends(utilisateur_courant)):
    ligne = _dupliquer_correction(correction_id, utilisateur.id)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne


@router.delete("/{correction_id}", status_code=204)
def supprimer(correction_id: str, utilisateur=Depends(utilisateur_courant)):
    if not _supprimer_correction(correction_id, utilisateur.id):
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")


@router.patch("/{correction_id}/deplacer")
def deplacer(correction_id: str, payload: DeplacerPayload, utilisateur=Depends(utilisateur_courant)):
    """Rattache le comportement issu de cette correction à un
    emplacement (notion/chapitre/matière), en mode dégradé (portée
    "général" seulement) tant que la Partie 1 n'existe pas."""
    ligne = _deplacer_correction(correction_id, utilisateur.id, payload.lien_type, payload.lien_id)
    if ligne is None:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    return ligne
