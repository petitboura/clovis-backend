"""
Cascade de supervision (Point 6, Partie 10, 07/09/2026). Voir
core/cascade_supervision.py pour la logique complète - ce fichier ne
fait qu'exposer les endpoints de lecture (indicateur prof/établissement/
équipe Clovis) et l'action de résolution manuelle.

Aucun endpoint de déclenchement manuel : la détection est automatique
(voir core/corrections_pedagogiques.py::creer_correction), jamais
provoquée à la demande depuis l'API.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from api.permissions_hierarchie import _est_admin
from core.erreurs import erreur_api
from core.etablissements import obtenir_etablissement_par_profile as _obtenir_etablissement_par_profile
from core.cascade_supervision import (
    lister_mes_cascades as _lister_mes_cascades,
    lister_cascades_etablissement as _lister_cascades_etablissement,
    lister_cascades_equipe_clovis as _lister_cascades_equipe_clovis,
    resoudre_cascade as _resoudre_cascade,
)

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/cascades-supervision", tags=["cascade_supervision"])


class ResoudrePayload(BaseModel):
    note: Optional[str] = None


@router.get("/mon-etat")
def mon_etat(utilisateur=Depends(utilisateur_courant)):
    """Cascades concernant l'utilisateur courant en tant que prof --
    indicateur affiché dans Bureau."""
    return {"cascades": _lister_mes_cascades(utilisateur.id)}


@router.get("/etablissement")
def cascades_etablissement(utilisateur=Depends(utilisateur_courant)):
    """Cascades arrivées à l'étape J5 pour l'établissement de
    l'utilisateur courant (réservé au compte établissement lui-même,
    404 si l'utilisateur n'en gère aucun - même logique de non-fuite
    que le reste de l'API établissements)."""
    etablissement = _obtenir_etablissement_par_profile(utilisateur.id)
    if not etablissement:
        raise erreur_api(404, "AUCUN_ETABLISSEMENT_POUR_CET_UTILISATEUR")
    return {"cascades": _lister_cascades_etablissement(etablissement["id"])}


@router.get("/equipe-clovis")
def cascades_equipe_clovis(utilisateur=Depends(utilisateur_courant)):
    """Réservé à l'équipe Clovis (profiles.role='admin', même mécanisme
    que api/signalements.py)."""
    if not _est_admin(utilisateur.id):
        raise erreur_api(403, "RESERVE_EQUIPE_CLOVIS")
    return {"cascades": _lister_cascades_equipe_clovis()}


@router.post("/{cascade_id}/resoudre")
def resoudre(cascade_id: str, payload: ResoudrePayload, utilisateur=Depends(utilisateur_courant)):
    """Marque une cascade comme résolue - réservé au prof concerné, à
    l'établissement rattaché (étape J5), ou à l'équipe Clovis (voir
    core/cascade_supervision.py::resoudre_cascade pour la vérification
    exacte)."""
    cascade = _resoudre_cascade(cascade_id, utilisateur.id, payload.note)
    if cascade is None:
        raise erreur_api(404, "CASCADE_INTROUVABLE_OU_NON_AUTORISEE")
    return cascade
