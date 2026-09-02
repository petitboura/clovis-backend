"""
Codes de partage (14/08/2026, demande Bourama) -- voir
core/codes_partage.py pour la logique et la philosophie complète.
Deux routeurs : un pour gérer SES PROPRES codes (créer, modifier,
activer/désactiver, supprimer), un pour ENTRER un code d'un autre
utilisateur et lister/retirer ses rattachements.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.codes_partage import (
    lister_mes_codes,
    creer_code,
    modifier_code,
    activer_desactiver_code,
    supprimer_code,
    entrer_code,
    lister_mes_rattachements,
    retirer_rattachement,
)

router_mes_codes = APIRouter(prefix="/api/codes", tags=["codes_partage"])
router_rattachements = APIRouter(prefix="/api/rattachements-codes", tags=["codes_partage"])


class CodePayload(BaseModel):
    nom: str | None = None
    # 18/08/2026, demande Bourama : sélection parmi les comportements déjà
    # créés dans "Mes comportements" (référence vivante), plus un texte
    # tapé directement ici.
    comportement_ids: list[str] | None = None
    programme_id: str | None = None
    # 02/09/2026, demande Bourama : remplace partage_bibliotheque (booléen
    # "toute la bibliothèque") par une sélection précise de dossiers déjà
    # créés dans la bibliothèque perso, plusieurs à la fois possibles.
    dossier_ids: list[str] | None = None
    texte_libre: str | None = None


class CodePatchPayload(BaseModel):
    """Modification partielle -- voir core/codes_partage.py::modifier_code :
    seuls les champs fournis (non None) sont mis à jour. Pour vider un
    champ texte, envoyer une chaîne vide plutôt que de l'omettre.
    comportement_ids/dossier_ids : None -> pas touché, liste (même vide)
    -> remplace entièrement la sélection."""
    nom: str | None = None
    comportement_ids: list[str] | None = None
    programme_id: str | None = None
    dossier_ids: list[str] | None = None
    texte_libre: str | None = None


@router_mes_codes.get("")
def lister(utilisateur=Depends(utilisateur_courant)):
    return lister_mes_codes(utilisateur.id)


@router_mes_codes.post("", status_code=201)
def creer(payload: CodePayload, utilisateur=Depends(utilisateur_courant)):
    return creer_code(
        proprietaire_id=utilisateur.id,
        nom=payload.nom,
        comportement_ids=payload.comportement_ids,
        programme_id=payload.programme_id,
        dossier_ids=payload.dossier_ids,
        texte_libre=payload.texte_libre,
    )


@router_mes_codes.patch("/{code_id}")
def modifier(code_id: str, payload: CodePatchPayload, utilisateur=Depends(utilisateur_courant)):
    resultat = modifier_code(
        code_id=code_id,
        proprietaire_id=utilisateur.id,
        nom=payload.nom,
        comportement_ids=payload.comportement_ids,
        programme_id=payload.programme_id,
        dossier_ids=payload.dossier_ids,
        texte_libre=payload.texte_libre,
    )
    if not resultat:
        raise erreur_api(404, "CODE_INTROUVABLE")
    return resultat


class ActiverPayload(BaseModel):
    actif: bool


@router_mes_codes.post("/{code_id}/actif")
def activer(code_id: str, payload: ActiverPayload, utilisateur=Depends(utilisateur_courant)):
    resultat = activer_desactiver_code(code_id, utilisateur.id, payload.actif)
    if not resultat:
        raise erreur_api(404, "CODE_INTROUVABLE")
    return resultat


@router_mes_codes.delete("/{code_id}", status_code=204)
def supprimer(code_id: str, utilisateur=Depends(utilisateur_courant)):
    if not supprimer_code(code_id, utilisateur.id):
        raise erreur_api(404, "CODE_INTROUVABLE")


class EntrerCodePayload(BaseModel):
    code: str


@router_rattachements.post("", status_code=201)
def entrer(payload: EntrerCodePayload, utilisateur=Depends(utilisateur_courant)):
    if not (payload.code or "").strip():
        raise erreur_api(400, "CODE_MANQUANT")
    resultat = entrer_code(payload.code, utilisateur.id)
    if not resultat:
        raise erreur_api(404, "CODE_INVALIDE_OU_INACTIF")
    return resultat


@router_rattachements.get("")
def lister_recus(utilisateur=Depends(utilisateur_courant)):
    return lister_mes_rattachements(utilisateur.id)


@router_rattachements.delete("/{rattachement_id}", status_code=204)
def retirer(rattachement_id: str, utilisateur=Depends(utilisateur_courant)):
    if not retirer_rattachement(rattachement_id, utilisateur.id):
        raise erreur_api(404, "RATTACHEMENT_INTROUVABLE")
