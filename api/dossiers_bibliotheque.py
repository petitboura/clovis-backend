"""
Routes REST pour les dossiers/sous-dossiers de la bibliothèque
personnelle (22/08/2026, demande explicite de Bourama).

Toute la logique vit dans core/dossiers_bibliotheque.py, ce fichier
n'est qu'une fine couche HTTP par-dessus, pour que le FRONTEND
(section Bibliothèque de "Mon espace") puisse créer/renommer/supprimer
des dossiers et y ranger/retirer des fichiers. Les mêmes actions sont
aussi exposées à l'IA via des outils MCP (core/serveur_mcp_espace.py
et core/serveur_mcp_generation.py).

Vérification de propriété systématique ici : chaque dossier/fichier
manipulé doit appartenir à l'utilisateur connecté, sinon 403/404,
même convention que api/bibliotheque_utilisateur.py.
"""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api
from core.dossiers_bibliotheque import (
    _proprietaire_dossier,
    creer_dossier,
    lister_dossiers,
    lister_dossiers_du_fichier,
    lister_fichiers_ids_dossier,
    ranger_fichier,
    renommer_dossier,
    retirer_fichier,
    supprimer_dossier,
)
from core.codes_partage import propager_fichier_range_dossier

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/bibliotheque/dossiers", tags=["dossiers-bibliotheque"])


def _fichier_appartient_a(fichier_id: str, user_id: str) -> bool:
    res = supabase.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
    return bool(res and res.data and res.data["user_id"] == user_id)


class CreerDossierPayload(BaseModel):
    nom: str = ""
    dossier_parent_id: str | None = None


class RenommerDossierPayload(BaseModel):
    nom: str


class RangerFichierPayload(BaseModel):
    fichier_id: str


@router.get("")
def lister(utilisateur=Depends(utilisateur_courant)):
    """
    Liste tous les dossiers de l'utilisateur, à plat, avec la liste des
    fichier_id directement rattachés à chacun, au frontend de
    reconstruire l'arborescence et de croiser avec sa propre liste de
    fichiers (déjà chargée par ailleurs via GET /api/bibliotheque).
    """
    dossiers = lister_dossiers(utilisateur.id)
    for d in dossiers:
        d["fichier_ids"] = lister_fichiers_ids_dossier(d["id"])
    return dossiers


@router.post("", status_code=201)
def creer(payload: CreerDossierPayload, request: Request, utilisateur=Depends(utilisateur_courant)):
    # Nom optionnel (28/08, demande Bourama : "nom optionnel même pour un
    # dossier", retrofit depuis le catalogue public) -- repli sur "Nouveau dossier".
    nom = (payload.nom or "").strip() or "Nouveau dossier"

    if payload.dossier_parent_id:
        proprietaire = _proprietaire_dossier(payload.dossier_parent_id)
        if proprietaire is None:
            raise erreur_api(404, "DOSSIER_PARENT_INTROUVABLE")
        if proprietaire != utilisateur.id:
            raise erreur_api(403, "CE_DOSSIER_PARENT_NE_T_APPARTIENT_PAS")

    dossier = creer_dossier(utilisateur.id, nom, payload.dossier_parent_id)

    journaliser(
        action="dossier_bibliotheque.cree",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"nom": nom, "dossier_id": dossier["id"]},
        request=request,
    )
    return dossier


@router.patch("/{dossier_id}")
def renommer(dossier_id: str, payload: RenommerDossierPayload, utilisateur=Depends(utilisateur_courant)):
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if proprietaire != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")

    nouveau_nom = (payload.nom or "").strip()
    if not nouveau_nom:
        raise erreur_api(400, "NOM_DE_DOSSIER_MANQUANT")

    renommer_dossier(dossier_id, nouveau_nom)
    return {"id": dossier_id, "nom": nouveau_nom}


@router.delete("/{dossier_id}", status_code=204)
def supprimer(dossier_id: str, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Supprime un dossier. Comportement confirmé par Bourama (voir
    core/dossiers_bibliotheque.py:supprimer_dossier) : un fichier encore
    rattaché à au moins un autre dossier est conservé, un fichier qui
    n'était rattaché à AUCUN autre dossier est supprimé avec lui.
    """
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if proprietaire != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")

    supprimer_dossier(dossier_id)

    journaliser(
        action="dossier_bibliotheque.supprime",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"dossier_id": dossier_id},
        request=request,
    )


@router.post("/{dossier_id}/fichiers", status_code=201)
def ranger(dossier_id: str, payload: RangerFichierPayload, utilisateur=Depends(utilisateur_courant)):
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if proprietaire != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")
    if not _fichier_appartient_a(payload.fichier_id, utilisateur.id):
        raise erreur_api(403, "CE_FICHIER_NE_T_APPARTIENT_PAS")

    ranger_fichier(payload.fichier_id, dossier_id)

    # 02/09/2026, demande Bourama : si ce dossier (ou un de ses ancêtres)
    # est partagé via un code actif, propager ce fichier vers chaque
    # receveur, rangé dans le dossier miroir correspondant. Non bloquant :
    # une erreur de propagation ne doit jamais faire échouer le rangement
    # lui-même (déjà réussi).
    try:
        propager_fichier_range_dossier(payload.fichier_id, dossier_id, utilisateur.id)
    except Exception as e:
        logging.error(f"ERREUR propagation dossier partagé (fichier {payload.fichier_id}, dossier {dossier_id}) : {e}")

    return {"dossier_id": dossier_id, "fichier_id": payload.fichier_id}


@router.delete("/{dossier_id}/fichiers/{fichier_id}", status_code=204)
def retirer(dossier_id: str, fichier_id: str, utilisateur=Depends(utilisateur_courant)):
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if proprietaire != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")

    retirer_fichier(fichier_id, dossier_id)


@router.get("/par-fichier/{fichier_id}")
def dossiers_du_fichier(fichier_id: str, utilisateur=Depends(utilisateur_courant)):
    if not _fichier_appartient_a(fichier_id, utilisateur.id):
        raise erreur_api(403, "CE_FICHIER_NE_T_APPARTIENT_PAS")
    return lister_dossiers_du_fichier(fichier_id)
