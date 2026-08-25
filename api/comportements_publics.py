"""
Catalogue public de comportements (21/08/2026, demande Bourama :
"les comportements aussi, je veux un onglet public... quelqu'un peut
l'uploader et l'activer"). Même principe que api/plugins_programme.py :
publier (voir api/comportements_etudiants.py::publier_mon_comportement)
crée une copie figée ici ; ce router expose la recherche (publique, pas
besoin de compte pour consulter -- même philosophie que
rechercher_plugins) et l'activation (gatée par un compte, comme
telecharger_plugin).
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.comportements_etudiants import (
    activer_comportement_public,
    lister_comportements_publics,
    uploader_comportement_public,
)
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/comportements-publics", tags=["comportements_publics"])


class ComportementPublic(BaseModel):
    id: str
    nom: str
    description: str
    texte: str
    activations_count: int


class ComportementActive(BaseModel):
    id: str
    texte: str
    description: str
    nom: str
    actif: bool


@router.get("", response_model=list[ComportementPublic])
def rechercher_comportements_publics(q: str | None = None):
    return lister_comportements_publics(mot_cle=q)


@router.post("/uploader", response_model=ComportementPublic, status_code=201)
async def uploader_skill_public(
    fichier: UploadFile = File(...),
    nom: str = Form(...),
    description: str = Form(""),
    utilisateur=Depends(utilisateur_courant),
):
    """25/08/2026, demande Bourama : uploader un fichier .md directement
    dans le catalogue PUBLIC, publié immédiatement pour tout le monde --
    voir uploader_comportement_public. Distinct de la création dans "Mes
    comportements" (privé par défaut, publication séparée via le bouton
    "Publier")."""
    if not nom.strip():
        raise erreur_api(400, "NOM_REQUIS")
    if not (fichier.filename or "").lower().endswith(".md"):
        raise erreur_api(400, "FICHIER_MD_REQUIS")

    contenu = (await fichier.read()).decode("utf-8", errors="replace").strip()
    if not contenu:
        raise erreur_api(400, "FICHIER_VIDE")

    return uploader_comportement_public(utilisateur.id, nom, description, contenu)


@router.post("/{comportement_public_id}/activer", response_model=ComportementActive, status_code=201)
def activer_mon_comportement_public(comportement_public_id: str, utilisateur=Depends(utilisateur_courant)):
    """Crée (ou renvoie, si déjà fait) la copie de cet utilisateur pour
    "Mon espace" -- voir activer_comportement_public. Requiert un
    compte : c'est la seule action gatée de ce router, la recherche
    au-dessus reste publique."""
    resultat = activer_comportement_public(comportement_public_id, utilisateur.id)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_PUBLIC_INTROUVABLE")
    return resultat
