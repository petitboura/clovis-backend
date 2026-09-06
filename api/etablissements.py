"""
Créé le 06/09/2026, Bourama : endpoints des fondations du système
établissement (Partie 9). Voir core/etablissements.py pour la logique et
la philosophie complète. Un seul routeur, préfixe /api/etablissements.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.etablissements import (
    declarer_etablissement,
    modifier_profil_etablissement,
    obtenir_etablissement,
    obtenir_etablissement_par_profile,
    lister_etablissements_publics,
    suivre_etablissement,
    demander_connexion,
    accepter_rattachement,
    lister_rattachements,
    lister_mes_rattachements,
    publier,
    valider_publication,
    lister_publications_en_attente,
    lister_publications,
)
from core.erreurs import erreur_api

router_etablissements = APIRouter(prefix="/api/etablissements", tags=["etablissements"])


class DeclarationEtablissementPayload(BaseModel):
    nom: str
    description: str | None = None
    site_web: str | None = None
    contact: str | None = None


class ModificationEtablissementPayload(BaseModel):
    nom: str | None = None
    description: str | None = None
    site_web: str | None = None
    contact: str | None = None
    actif: bool | None = None


class PublicationPayload(BaseModel):
    contenu: str
    titre: str | None = None
    visibilite: str = "publique"


class ValidationPublicationPayload(BaseModel):
    approuver: bool


# --- Profil établissement ---------------------------------------------------

@router_etablissements.post("/declarer")
def declarer(payload: DeclarationEtablissementPayload, utilisateur=Depends(utilisateur_courant)):
    return declarer_etablissement(
        utilisateur.id, payload.nom, payload.description, payload.site_web, payload.contact
    )


@router_etablissements.patch("/{etablissement_id}")
def modifier(etablissement_id: str, payload: ModificationEtablissementPayload, utilisateur=Depends(utilisateur_courant)):
    return modifier_profil_etablissement(etablissement_id, utilisateur.id, payload.model_dump(exclude_unset=True))


@router_etablissements.get("/mon-profil")
def mon_profil(utilisateur=Depends(utilisateur_courant)):
    etablissement = obtenir_etablissement_par_profile(utilisateur.id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_PROFIL_INTROUVABLE")
    return etablissement


@router_etablissements.get("")
def lister():
    """Section publique -- n'importe qui peut parcourir les établissements,
    pas d'authentification requise."""
    return lister_etablissements_publics()


@router_etablissements.get("/{etablissement_id}")
def detail(etablissement_id: str):
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")
    return etablissement


# --- Rattachements (suivre / se connecter / accepter) -----------------------

@router_etablissements.post("/{etablissement_id}/suivre")
def suivre(etablissement_id: str, utilisateur=Depends(utilisateur_courant)):
    return suivre_etablissement(etablissement_id, utilisateur.id)


@router_etablissements.post("/{etablissement_id}/connecter")
def connecter(etablissement_id: str, utilisateur=Depends(utilisateur_courant)):
    return demander_connexion(etablissement_id, utilisateur.id)


@router_etablissements.post("/rattachements/{rattachement_id}/accepter")
def accepter(rattachement_id: str, utilisateur=Depends(utilisateur_courant)):
    return accepter_rattachement(rattachement_id, utilisateur.id)


@router_etablissements.get("/mes/rattachements")
def mes_rattachements(utilisateur=Depends(utilisateur_courant)):
    # Doit rester déclaré AVANT /{etablissement_id}/rattachements ci-dessous,
    # sinon FastAPI capture "mes" comme etablissement_id (routes dynamiques
    # à un segment testées dans l'ordre de déclaration).
    return lister_mes_rattachements(utilisateur.id)


@router_etablissements.get("/{etablissement_id}/rattachements")
def rattachements(etablissement_id: str, etat: str | None = None, utilisateur=Depends(utilisateur_courant)):
    return lister_rattachements(etablissement_id, utilisateur.id, etat)


# --- Publications ------------------------------------------------------------

@router_etablissements.post("/{etablissement_id}/publications")
def creer_publication(etablissement_id: str, payload: PublicationPayload, utilisateur=Depends(utilisateur_courant)):
    return publier(etablissement_id, utilisateur.id, payload.contenu, payload.titre, payload.visibilite)


@router_etablissements.patch("/publications/{publication_id}/valider")
def valider(publication_id: str, payload: ValidationPublicationPayload, utilisateur=Depends(utilisateur_courant)):
    return valider_publication(publication_id, utilisateur.id, payload.approuver)


@router_etablissements.get("/{etablissement_id}/publications/en-attente")
def publications_en_attente(etablissement_id: str, utilisateur=Depends(utilisateur_courant)):
    return lister_publications_en_attente(etablissement_id, utilisateur.id)


@router_etablissements.get("/{etablissement_id}/publications")
def publications(etablissement_id: str, utilisateur=Depends(utilisateur_courant)):
    return lister_publications(etablissement_id, utilisateur.id)
