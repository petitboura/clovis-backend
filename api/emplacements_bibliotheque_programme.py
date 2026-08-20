"""
Routes REST pour classer/déclasser/lister des documents de la
bibliothèque personnelle à un emplacement du programme (programme /
matière / chapitre / exercice / examen) -- 17/08/2026, demande Bourama.

Toute la logique (vérification de propriété du fichier ET de
l'emplacement, upsert idempotent) vit déjà dans
core/bibliotheque_programme.py, écrite le 16/08 pour les outils MCP
(core/serveur_mcp_espace.py:classer_document_dans_programme /
retirer_document_du_programme). Ce fichier n'est qu'une fine couche
HTTP par-dessus les mêmes fonctions, pour que le FRONTEND (pas
seulement l'IA en conversation) puisse s'en servir -- jusqu'ici ce
mécanisme n'était exposé qu'à l'IA, jamais à l'écran "Mon programme".

Ne remplace PAS l'ancien documents_programme (titre+lien, un seul
niveau chapitre, voir api/contenu_programme.py) : coexistence
assumée, comme déjà écrit dans core/bibliotheque_programme.py.
"""

import logging
from typing import List, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant
from api.journal import journaliser
from core.erreurs import erreur_api
from core.bibliotheque_programme import (
    TYPES_EMPLACEMENT_BIBLIOTHEQUE,
    classer_document,
    declasser_document,
    emplacement_couvert_par_plugin_public,
    lister_documents_emplacement,
    proprietaire_emplacement,
)

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/emplacements", tags=["emplacements-bibliotheque-programme"])

TypeCible = Literal["programme", "matiere", "chapitre", "exercice", "examen"]


class FichierEmplacementReponse(BaseModel):
    id: str
    nom_fichier: str
    description: str | None = None
    type_mime: str
    url_publique: str
    created_at: str


class ClasserDocumentPayload(BaseModel):
    fichier_id: str


@router.get("/{type_cible}/{cible_id}/documents", response_model=List[FichierEmplacementReponse])
def lister(type_cible: TypeCible, cible_id: str, utilisateur=Depends(utilisateur_courant)):
    if type_cible not in TYPES_EMPLACEMENT_BIBLIOTHEQUE:
        raise erreur_api(422, "TYPE_D_EMPLACEMENT_INVALIDE")

    proprietaire_id = proprietaire_emplacement(type_cible, cible_id)
    if proprietaire_id is None:
        raise erreur_api(404, "EMPLACEMENT_INTROUVABLE")
    if proprietaire_id != utilisateur.id:
        # Pas propriétaire : autorisé quand même en lecture seule si cet
        # emplacement appartient à un plugin en contribution_libre (20/08)
        # -- n'importe qui doit pouvoir voir les documents d'un plugin
        # public, pas seulement son auteur. Couvre aussi les examens
        # transverses (voir emplacement_couvert_par_plugin_public).
        if not emplacement_couvert_par_plugin_public(type_cible, cible_id):
            raise erreur_api(403, "PAS_LE_DROIT_SUR_CET_EMPLACEMENT")

    return lister_documents_emplacement(type_cible, cible_id)


@router.post("/{type_cible}/{cible_id}/documents", status_code=201)
def classer(
    type_cible: TypeCible,
    cible_id: str,
    payload: ClasserDocumentPayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    resultat = classer_document(utilisateur.id, payload.fichier_id, type_cible, cible_id)
    if not resultat["ok"]:
        raise erreur_api(400, "CLASSEMENT_DOCUMENT_ECHEC", message=resultat["erreur"])

    journaliser(
        action="bibliotheque_programme.classe",
        user_id=utilisateur.id,
        cible_type="bibliotheque_emplacement_programme",
        cible_id=payload.fichier_id,
        details={"type_cible": type_cible, "cible_id": cible_id},
        request=request,
    )
    return {"ok": True}


@router.delete("/{type_cible}/{cible_id}/documents/{fichier_id}", status_code=204)
def declasser(
    type_cible: TypeCible,
    cible_id: str,
    fichier_id: str,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    resultat = declasser_document(utilisateur.id, fichier_id, type_cible, cible_id)
    if not resultat["ok"]:
        raise erreur_api(400, "DECLASSEMENT_DOCUMENT_ECHEC", message=resultat["erreur"])

    journaliser(
        action="bibliotheque_programme.declasse",
        user_id=utilisateur.id,
        cible_type="bibliotheque_emplacement_programme",
        cible_id=fichier_id,
        details={"type_cible": type_cible, "cible_id": cible_id},
        request=request,
    )
