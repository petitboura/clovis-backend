"""
Routes REST pour les dossiers du catalogue public (28/08/2026, demande
Bourama). Toute la logique vit dans core/dossiers_catalogue_public.py,
voir sa docstring pour les règles contribution_libre/privee.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.dossiers_catalogue_public import (
    _dossier,
    creer_dossier,
    lister_dossiers,
    lister_fichiers_ids_dossier,
    peut_ajouter_contenu,
    peut_retirer_contenu,
    ranger_fichier,
    renommer_dossier,
    retirer_fichier,
    supprimer_dossier,
)
from core.dossiers_publics_attaches import (
    attacher_dossier as _attacher_dossier_public,
    detacher_dossier as _detacher_dossier_public,
    lister_dossiers_attaches,
    propager_fichier_public_range_dossier,
)
from core.listes_bibliotheque_publique import normaliser_et_enregistrer

router = APIRouter(prefix="/api/bibliotheque-publique/dossiers", tags=["dossiers-catalogue-public"])


class CreerDossierPayload(BaseModel):
    nom: str = ""
    statut: str = "contribution_libre"
    dossier_parent_id: str | None = None
    pays: str = ""
    classe: str = ""
    categorie: str = ""


class RenommerDossierPayload(BaseModel):
    nom: str


class RangerFichierPayload(BaseModel):
    fichier_id: str


@router.get("")
def lister(utilisateur=Depends(utilisateur_courant)):
    dossiers = lister_dossiers()
    for d in dossiers:
        d["fichier_ids"] = lister_fichiers_ids_dossier(d["id"])
    return dossiers


@router.post("", status_code=201)
def creer(payload: CreerDossierPayload, utilisateur=Depends(utilisateur_courant)):
    if payload.statut not in ("contribution_libre", "privee"):
        raise erreur_api(400, "STATUT_INVALIDE")
    # Nom optionnel (28/08, demande Bourama : "nom et description optionnels même pour dossier") -- repli sur "Nouveau dossier".
    nom = (payload.nom or "").strip() or "Nouveau dossier"
    return creer_dossier(
        utilisateur.id, nom, payload.statut, payload.dossier_parent_id,
        pays=normaliser_et_enregistrer("pays", payload.pays),
        classe=normaliser_et_enregistrer("classe", payload.classe),
        categorie=normaliser_et_enregistrer("categorie", payload.categorie),
    )


@router.patch("/{dossier_id}")
def renommer(dossier_id: str, payload: RenommerDossierPayload, utilisateur=Depends(utilisateur_courant)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if dossier["cree_par"] != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")
    nouveau_nom = (payload.nom or "").strip() or "Nouveau dossier"
    renommer_dossier(dossier_id, nouveau_nom)
    return {"id": dossier_id, "nom": nouveau_nom}


@router.delete("/{dossier_id}", status_code=204)
def supprimer(dossier_id: str, utilisateur=Depends(utilisateur_courant)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if dossier["cree_par"] != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")
    supprimer_dossier(dossier_id)


@router.post("/{dossier_id}/fichiers", status_code=201)
def ranger(dossier_id: str, payload: RangerFichierPayload, utilisateur=Depends(utilisateur_courant)):
    if not _dossier(dossier_id):
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if not peut_ajouter_contenu(dossier_id, utilisateur.id):
        raise erreur_api(403, "CE_DOSSIER_EST_PRIVE_A_SON_CREATEUR")
    ranger_fichier(payload.fichier_id, dossier_id)
    propager_fichier_public_range_dossier(payload.fichier_id, dossier_id)
    return {"dossier_id": dossier_id, "fichier_id": payload.fichier_id}


@router.delete("/{dossier_id}/fichiers/{fichier_id}", status_code=204)
def retirer(dossier_id: str, fichier_id: str, utilisateur=Depends(utilisateur_courant)):
    if not _dossier(dossier_id):
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    # 28/08, correctif Bourama : retirer reste réservé au créateur du
    # dossier, même en contribution_libre (contrairement à ranger,
    # ci-dessus, qui lui est ouvert à tous en contribution_libre).
    if not peut_retirer_contenu(dossier_id, utilisateur.id):
        raise erreur_api(403, "SEUL_LE_CREATEUR_DU_DOSSIER_PEUT_EN_RETIRER_UN_FICHIER")
    retirer_fichier(fichier_id, dossier_id)


# --- Attachement à la bibliothèque perso (02/09/2026, demande Bourama) -
# Copie réelle dans la bibliothèque perso (dossier miroir), synchronisée
# en continu -- voir docstring de core/dossiers_publics_attaches.py.
# Attacher est libre pour n'importe quel dossier public quel que soit
# son statut.


@router.get("/attaches")
def lister_attaches(utilisateur=Depends(utilisateur_courant)):
    return lister_dossiers_attaches(utilisateur.id)


@router.post("/{dossier_id}/attacher", status_code=201)
def attacher(dossier_id: str, utilisateur=Depends(utilisateur_courant)):
    dossier = _attacher_dossier_public(dossier_id, utilisateur.id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    return dossier


@router.delete("/{dossier_id}/attacher", status_code=204)
def detacher(dossier_id: str, utilisateur=Depends(utilisateur_courant)):
    _detacher_dossier_public(dossier_id, utilisateur.id)
