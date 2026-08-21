"""
Section "Notion-like" (Partie 2), lot 4/5 -- répétition espacée,
2026-08-20, demande Bourama. Fine couche HTTP au-dessus de
core/revision_llm.py, même principe que les autres fichiers api/ de
cette section.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.revision_llm import (
    QUALITES_CONNUES,
    lister_elements_a_reviser as _lister_elements_a_reviser,
    enregistrer_reponse as _enregistrer_reponse,
)

logging.basicConfig(level=logging.INFO)

router_revision = APIRouter(prefix="/api/revision", tags=["revision"])


class ReponsePayload(BaseModel):
    qualite: str  # echec | difficile | correct | facile


@router_revision.get("/a-reviser")
def lister_a_reviser(base_id: str | None = None, utilisateur=Depends(utilisateur_courant)):
    return _lister_elements_a_reviser(utilisateur.id, base_id)


@router_revision.post("/{element_id}/reponse")
def repondre(element_id: str, payload: ReponsePayload, utilisateur=Depends(utilisateur_courant)):
    if payload.qualite not in QUALITES_CONNUES:
        raise erreur_api(422, "QUALITE_INVALIDE", message=f"doit être l'un de {', '.join(QUALITES_CONNUES)}")
    resultat = _enregistrer_reponse(utilisateur.id, element_id, payload.qualite)
    if resultat is None:
        raise erreur_api(404, "ELEMENT_INTROUVABLE")
    return resultat
