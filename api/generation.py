"""
Génération de documents/code/images déclenchée par un BOUTON explicite
côté frontend (par opposition à l'agent qui décide seul via le serveur
MCP -- voir core/serveur_mcp_generation.py). Même logique métier
(core/generation_*.py), deux points d'entrée différents, comme discuté
avec Bourama le 2026-07-20.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from core.erreurs import erreur_api
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.generation_documents import generer_pdf_depuis_markdown
from core.generation_code import generer_zip_depuis_fichiers
from core.generation_donnees import exporter_donnees
from core.generation_signature import (
    envoyer_pour_signature,
    statut_signature,
    signature_disponible,
)
from core.generation_audio import generer_audio, audio_disponible
from core.generation_video import (
    lancer_generation_video,
    statut_video,
    video_disponible,
)
from core.generation_3d import (
    lancer_generation_3d,
    statut_modele_3d,
    modele_3d_disponible,
)
from core.generation_images import generer_image, image_generation_disponible

router = APIRouter(prefix="/api/generation", tags=["generation"])


class DemandeDocument(BaseModel):
    titre: str
    contenu_markdown: str


class DemandeCode(BaseModel):
    nom_projet: str
    fichiers: dict[str, str]


class DemandeImage(BaseModel):
    prompt: str


class DemandeDonnees(BaseModel):
    nom: str
    donnees: dict
    format: str = "json"


class Signataire(BaseModel):
    nom: str
    email: str


class DemandeAudio(BaseModel):
    texte: str
    voix: str = "austin"


class DemandeVideo(BaseModel):
    prompt: str
    duree_secondes: int = 5


class Demande3D(BaseModel):
    prompt: str


class DemandeSignature(BaseModel):
    titre: str
    contenu_markdown: str
    signataires: list[Signataire]
    jours_expiration: int = 14


class ReponseGeneration(BaseModel):
    url: str


@router.post("/document", response_model=ReponseGeneration)
def generer_document_route(demande: DemandeDocument, utilisateur=Depends(utilisateur_courant)):
    try:
        url = generer_pdf_depuis_markdown(demande.titre, demande.contenu_markdown)
    except Exception as e:
        logging.error(f"ERREUR génération document (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DE_LA_GENERATION_DU_DOCUMENT")
    return ReponseGeneration(url=url)


@router.post("/code", response_model=ReponseGeneration)
def generer_code_route(demande: DemandeCode, utilisateur=Depends(utilisateur_courant)):
    try:
        url = generer_zip_depuis_fichiers(demande.nom_projet, demande.fichiers)
    except Exception as e:
        logging.error(f"ERREUR génération code (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_GENERATION_ARCHIVE")
    return ReponseGeneration(url=url)


@router.post("/donnees", response_model=ReponseGeneration)
def exporter_donnees_route(demande: DemandeDonnees, utilisateur=Depends(utilisateur_courant)):
    try:
        url = exporter_donnees(demande.nom, demande.donnees, demande.format)
    except ValueError as e:
        raise erreur_api(400, "REQUETE_INVALIDE", message=str(e))
    except Exception as e:
        logging.error(f"ERREUR export données (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DE_L_EXPORT_REESSAIE")
    return ReponseGeneration(url=url)


@router.post("/signature")
def envoyer_pour_signature_route(demande: DemandeSignature, utilisateur=Depends(utilisateur_courant)):
    if not signature_disponible():
        raise erreur_api(503, "SIGNATURE_INDISPONIBLE")
    try:
        return envoyer_pour_signature(
            demande.titre,
            demande.contenu_markdown,
            [s.model_dump() for s in demande.signataires],
            demande.jours_expiration,
        )
    except Exception as e:
        logging.error(f"ERREUR envoi signature (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DE_L_ENVOI_POUR_SIGNATURE")


@router.get("/signature/{signature_request_id}")
def statut_signature_route(signature_request_id: str, utilisateur=Depends(utilisateur_courant)):
    if not signature_disponible():
        raise erreur_api(503, "SIGNATURE_INDISPONIBLE")
    try:
        return statut_signature(signature_request_id)
    except Exception as e:
        logging.error(f"ERREUR statut signature (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_RECUPERER_LE_STATUT")


@router.post("/3d")
def lancer_3d_route(demande: Demande3D, utilisateur=Depends(utilisateur_courant)):
    if not modele_3d_disponible():
        raise erreur_api(503, "GENERATION_3D_INDISPONIBLE")
    try:
        return lancer_generation_3d(demande.prompt)
    except Exception as e:
        logging.error(f"ERREUR lancement 3D (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_LANCEMENT_GENERATION_3D")


@router.get("/3d/{request_id}")
def statut_3d_route(request_id: str, utilisateur=Depends(utilisateur_courant)):
    if not modele_3d_disponible():
        raise erreur_api(503, "GENERATION_3D_INDISPONIBLE")
    try:
        return statut_modele_3d(request_id)
    except Exception as e:
        logging.error(f"ERREUR statut 3D (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_RECUPERER_LE_STATUT")


@router.post("/video")
def lancer_video_route(demande: DemandeVideo, utilisateur=Depends(utilisateur_courant)):
    if not video_disponible():
        raise erreur_api(503, "GENERATION_VIDEO_INDISPONIBLE")
    try:
        return lancer_generation_video(demande.prompt, demande.duree_secondes)
    except Exception as e:
        logging.error(f"ERREUR lancement vidéo (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_LANCEMENT_GENERATION_VIDEO")


@router.get("/video/{request_id}")
def statut_video_route(request_id: str, utilisateur=Depends(utilisateur_courant)):
    if not video_disponible():
        raise erreur_api(503, "GENERATION_VIDEO_INDISPONIBLE")
    try:
        return statut_video(request_id)
    except Exception as e:
        logging.error(f"ERREUR statut vidéo (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_RECUPERER_LE_STATUT")


@router.post("/audio", response_model=ReponseGeneration)
def generer_audio_route(demande: DemandeAudio, utilisateur=Depends(utilisateur_courant)):
    if not audio_disponible():
        raise erreur_api(503, "GENERATION_AUDIO_INDISPONIBLE")
    try:
        url = generer_audio(demande.texte, demande.voix)
    except Exception as e:
        logging.error(f"ERREUR génération audio (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DE_LA_GENERATION_AUDIO_REESSAIE")
    return ReponseGeneration(url=url)


@router.post("/image", response_model=ReponseGeneration)
def generer_image_route(demande: DemandeImage, utilisateur=Depends(utilisateur_courant)):
    # Plus de check "disponible" ici : generer_image() gère elle-même le
    # choix Pollinations (gratuit)/Together AI (payant) en interne, voir
    # generation_images.py. Toujours actif.
    try:
        url = generer_image(demande.prompt)
    except Exception as e:
        logging.error(f"ERREUR génération image (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_GENERATION_IMAGE")
    return ReponseGeneration(url=url)
