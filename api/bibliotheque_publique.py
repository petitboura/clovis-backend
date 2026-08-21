"""
Bibliothèque publique (21/08/2026, demande Bourama : "un bibliothèque
publique dans la section bibliothèque, tout le monde peut y ajouter des
documents, juste en le décrivant et en donnant un nom").

Volontairement DISTINCT de fichiers_uploades/bibliotheque_utilisateur :
pas d'upload de fichier réel ici (nom + description, éventuellement un
lien), et pas branché sur consulter_bibliotheque/le RAG -- catalogue
consultable par les humains dans l'appli, pas une source injectée
automatiquement dans toutes les conversations. Public en lecture (pas
de compte requis pour consulter), gaté par un compte pour ajouter.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/bibliotheque-publique", tags=["bibliotheque_publique"])


class EntreeBibliothequePublique(BaseModel):
    id: str
    nom: str
    description: str
    lien: str | None = None
    created_at: str


class AjouterEntreePayload(BaseModel):
    nom: str
    description: str = ""
    lien: str | None = None


@router.get("", response_model=list[EntreeBibliothequePublique])
def lister_bibliotheque_publique(q: str | None = None):
    requete = supabase.table("bibliotheque_publique").select("id, nom, description, lien, created_at")
    if (q or "").strip():
        requete = requete.or_(f"nom.ilike.%{q.strip()}%,description.ilike.%{q.strip()}%")
    res = requete.order("created_at", desc=True).limit(200).execute()
    return res.data or []


@router.post("", response_model=EntreeBibliothequePublique, status_code=201)
def ajouter_a_bibliotheque_publique(payload: AjouterEntreePayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.nom.strip():
        raise erreur_api(400, "NOM_REQUIS")
    ligne = (
        supabase.table("bibliotheque_publique")
        .insert({
            "ajoute_par": utilisateur.id,
            "nom": payload.nom.strip(),
            "description": (payload.description or "").strip(),
            "lien": (payload.lien or "").strip() or None,
        })
        .execute()
    )
    return ligne.data[0]


@router.delete("/{entree_id}", status_code=204)
def supprimer_de_bibliotheque_publique(entree_id: str, utilisateur=Depends(utilisateur_courant)):
    """Seul le contributeur d'origine peut retirer SA propre entrée --
    même principe que declasser_document sur les plugins publics."""
    res = supabase.table("bibliotheque_publique").select("ajoute_par").eq("id", entree_id).maybe_single().execute()
    if not res or not res.data:
        raise erreur_api(404, "ENTREE_INTROUVABLE")
    if res.data["ajoute_par"] != utilisateur.id:
        raise erreur_api(403, "CETTE_ENTREE_NE_T_APPARTIENT_PAS")
    supabase.table("bibliotheque_publique").delete().eq("id", entree_id).execute()
