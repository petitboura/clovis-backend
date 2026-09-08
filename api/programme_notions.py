"""
Endpoints CRUD pour la structure des notions et l'avancement (Partie 1,
06/09/2026) -- voir core/programme_notions.py pour la logique complète
et la philosophie (rattaché à un code de partage, pas à un rôle).

Un seul routeur, imbriqué sous /api/notions/{code_id}/... (le code_id
identifie sans ambiguïté le "programme" concerné, comme déjà fait pour
codes_partage.py). La génération de structure depuis un document est un
routeur séparé dans ce même fichier (endpoints "generer"/"appliquer"),
mais la logique elle-même vit dans core/generation_notions_llm.py --
fichiers séparés comme prévu par le plan de travail.
"""

from fastapi import APIRouter, Depends, UploadFile, File
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.programme_notions import (
    code_appartient_a,
    lister_notions,
    creer_notion,
    renommer_notion,
    changer_statut_notion,
    definir_regle_notion,
    definir_consigne_notion,
    reordonner_notions,
    fusionner_notions,
    supprimer_notion,
)
from core.generation_notions_llm import (
    FORMATS_AUTORISES_GENERATION,
    TAILLE_MAX_DOCUMENT_GENERATION_OCTETS,
    generer_structure_notions_depuis_document,
)

router = APIRouter(prefix="/api/notions", tags=["programme_notions"])


@router.get("/{code_id}")
def lister(code_id: str, utilisateur=Depends(utilisateur_courant)):
    resultat = lister_notions(code_id, utilisateur.id)
    if resultat is None:
        raise erreur_api(404, "NOTION_PROGRAMME_CODE_INTROUVABLE")
    return resultat


class NotionCreationPayload(BaseModel):
    nom: str
    notion_parent_id: str | None = None


@router.post("/{code_id}", status_code=201)
def creer(code_id: str, payload: NotionCreationPayload, utilisateur=Depends(utilisateur_courant)):
    if not (payload.nom or "").strip():
        raise erreur_api(400, "NOTION_PROGRAMME_NOM_MANQUANT")
    resultat = creer_notion(code_id, utilisateur.id, payload.nom, payload.notion_parent_id)
    if resultat is None:
        raise erreur_api(404, "NOTION_PROGRAMME_CODE_INTROUVABLE")
    return resultat


class RenommerPayload(BaseModel):
    nom: str


@router.patch("/{code_id}/{notion_id}/nom")
def renommer(code_id: str, notion_id: str, payload: RenommerPayload, utilisateur=Depends(utilisateur_courant)):
    if not (payload.nom or "").strip():
        raise erreur_api(400, "NOTION_PROGRAMME_NOM_MANQUANT")
    resultat = renommer_notion(notion_id, code_id, utilisateur.id, payload.nom)
    if resultat is None:
        raise erreur_api(404, "NOTION_PROGRAMME_INTROUVABLE")
    return resultat


class StatutPayload(BaseModel):
    statut: str


@router.patch("/{code_id}/{notion_id}/statut")
def changer_statut(code_id: str, notion_id: str, payload: StatutPayload, utilisateur=Depends(utilisateur_courant)):
    resultat = changer_statut_notion(notion_id, code_id, utilisateur.id, payload.statut)
    if resultat is None:
        raise erreur_api(400, "NOTION_PROGRAMME_STATUT_INVALIDE")
    return resultat


class ReglePayload(BaseModel):
    regle: str | None = None


@router.patch("/{code_id}/{notion_id}/regle")
def definir_regle(code_id: str, notion_id: str, payload: ReglePayload, utilisateur=Depends(utilisateur_courant)):
    resultat = definir_regle_notion(notion_id, code_id, utilisateur.id, payload.regle)
    if resultat is None:
        raise erreur_api(400, "NOTION_PROGRAMME_REGLE_INVALIDE")
    return resultat


class ConsignePayload(BaseModel):
    consigne: str | None = None


@router.patch("/{code_id}/{notion_id}/consigne")
def definir_consigne(code_id: str, notion_id: str, payload: ConsignePayload, utilisateur=Depends(utilisateur_courant)):
    resultat = definir_consigne_notion(notion_id, code_id, utilisateur.id, payload.consigne)
    if resultat is None:
        raise erreur_api(404, "NOTION_PROGRAMME_INTROUVABLE")
    return resultat


class ReordonnerPayload(BaseModel):
    notion_parent_id: str | None = None
    notion_ids_ordonnes: list[str]


@router.post("/{code_id}/reordonner")
def reordonner(code_id: str, payload: ReordonnerPayload, utilisateur=Depends(utilisateur_courant)):
    if not reordonner_notions(code_id, utilisateur.id, payload.notion_parent_id, payload.notion_ids_ordonnes):
        raise erreur_api(404, "NOTION_PROGRAMME_CODE_INTROUVABLE")
    return {"ok": True}


class FusionPayload(BaseModel):
    notion_source_id: str
    notion_cible_id: str


@router.post("/{code_id}/fusionner")
def fusionner(code_id: str, payload: FusionPayload, utilisateur=Depends(utilisateur_courant)):
    resultat = fusionner_notions(code_id, utilisateur.id, payload.notion_source_id, payload.notion_cible_id)
    if resultat is None:
        raise erreur_api(400, "NOTION_PROGRAMME_FUSION_INVALIDE")
    return resultat


@router.delete("/{code_id}/{notion_id}", status_code=204)
def supprimer(code_id: str, notion_id: str, utilisateur=Depends(utilisateur_courant)):
    if not supprimer_notion(code_id, utilisateur.id, notion_id):
        raise erreur_api(404, "NOTION_PROGRAMME_INTROUVABLE")


@router.post("/{code_id}/generer")
async def generer(code_id: str, fichier: UploadFile = File(...), utilisateur=Depends(utilisateur_courant)):
    """Propose une structure de notions à partir d'un document -- NE
    TOUCHE PAS la base (voir core/generation_notions_llm.py). Le
    frontend (Partie 2) affiche la structure proposée pour validation
    avant de la POSTer, notion par notion, sur les endpoints ci-dessus
    (pas d'endpoint "appliquer" séparé : le prof peut éditer chaque
    notion proposée avant de la créer, la création reste le même
    contrat d'API que la création manuelle)."""
    if fichier.content_type not in FORMATS_AUTORISES_GENERATION:
        raise erreur_api(400, "FORMAT_NON_SUPPORTE_PDF_WORD_DOCX")
    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_DOCUMENT_GENERATION_OCTETS:
        raise erreur_api(400, "DOCUMENT_TROP_LOURD_15_MO_MAX")
    if not code_appartient_a(code_id, utilisateur.id):
        raise erreur_api(404, "NOTION_PROGRAMME_CODE_INTROUVABLE")
    resultat = generer_structure_notions_depuis_document(contenu, fichier.content_type)
    if resultat is None:
        raise erreur_api(502, "NOTION_PROGRAMME_GENERATION_ECHEC")
    return resultat
