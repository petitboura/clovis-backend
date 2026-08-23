"""
Bibliothèque publique (21/08/2026, demande Bourama : "un bibliothèque
publique dans la section bibliothèque, tout le monde peut y ajouter des
documents, juste en le décrivant et en donnant un nom").

CORRECTION du même jour (Bourama, après malentendu de ma part sur cette
phrase) : "nom" et "description" accompagnent un VRAI fichier uploadé,
ce ne sont pas des entrées texte à la place d'un fichier. Upload réel
dans Supabase Storage, même pattern que enregistrer_fichier (voir
core/bibliotheque_fichiers.py) -- bucket "bibliotheque", sous-dossier
"publique/" pour rester distinct des niveaux plateforme/agent/utilisateur.

Reste volontairement DISTINCT de fichiers_uploades/consulter_bibliotheque
(pas branché sur le RAG) : catalogue consultable par les humains dans
l'appli, pas une source injectée automatiquement dans les conversations
de tout le monde.
"""

import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from supabase import create_client

from api.auth import utilisateur_courant
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/bibliotheque-publique", tags=["bibliotheque_publique"])

BUCKET = "bibliotheque"
TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo, même limite que la bibliothèque personnelle


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


class EntreeBibliothequePublique(BaseModel):
    id: str
    nom: str
    description: str
    nom_fichier: str | None = None
    type_mime: str | None = None
    taille_octets: int | None = None
    url_publique: str | None = None
    created_at: str


@router.get("", response_model=list[EntreeBibliothequePublique])
def lister_bibliotheque_publique(q: str | None = None):
    # Filtre statut="publie" (22/08, chantier signalements) : une entrée
    # retirée par un admin suite à un signalement reste en base (trace
    # pour l'audit) mais ne doit plus jamais réapparaître dans le
    # catalogue, voir api/signalements.py.
    requete = (
        supabase.table("bibliotheque_publique")
        .select("id, nom, description, nom_fichier, type_mime, taille_octets, url_publique, created_at")
        .eq("statut", "publie")
    )
    if (q or "").strip():
        requete = requete.or_(f"nom.ilike.%{q.strip()}%,description.ilike.%{q.strip()}%")
    res = requete.order("created_at", desc=True).limit(200).execute()
    return res.data or []


@router.post("", response_model=EntreeBibliothequePublique, status_code=201)
async def ajouter_a_bibliotheque_publique(
    fichier: UploadFile = File(...),
    nom: str = Form(...),
    description: str = Form(""),
    utilisateur=Depends(utilisateur_courant),
):
    if not nom.strip():
        raise erreur_api(400, "NOM_REQUIS")

    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise erreur_api(400, "FICHIER_TROP_LOURD_50_MO_MAX")

    nom_original = fichier.filename or "fichier"
    extension = nom_original.rsplit(".", 1)[-1] if "." in nom_original else "bin"
    chemin_stockage = f"publique/{uuid.uuid4()}.{extension}"

    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, contenu, {"content-type": fichier.content_type or "application/octet-stream"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload bibliothèque publique {chemin_stockage}) : {e}")
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    try:
        ligne = (
            supabase.table("bibliotheque_publique")
            .insert({
                "ajoute_par": utilisateur.id,
                "nom": nom.strip(),
                "description": (description or "").strip(),
                "nom_fichier": nom_original,
                "chemin_stockage": chemin_stockage,
                "url_publique": url_publique,
                "type_mime": fichier.content_type,
                "taille_octets": len(contenu),
            })
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR ECRITURE bibliotheque_publique ({chemin_stockage}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    return ligne.data[0]


@router.delete("/{entree_id}", status_code=204)
def supprimer_de_bibliotheque_publique(entree_id: str, utilisateur=Depends(utilisateur_courant)):
    """Seul le contributeur d'origine peut retirer SA propre entrée --
    même principe que declasser_document sur les plugins publics. Le
    fichier dans Supabase Storage n'est pas explicitement retiré ici
    (même choix que le reste de la bibliothèque -- voir
    core/bibliotheque_fichiers.py, aucune suppression de storage n'y est
    faite non plus au retrait d'une ligne)."""
    res = (
        supabase.table("bibliotheque_publique")
        .select("ajoute_par")
        .eq("id", entree_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise erreur_api(404, "ENTREE_INTROUVABLE")
    if res.data["ajoute_par"] != utilisateur.id:
        raise erreur_api(403, "CETTE_ENTREE_NE_T_APPARTIENT_PAS")
    supabase.table("bibliotheque_publique").delete().eq("id", entree_id).execute()
