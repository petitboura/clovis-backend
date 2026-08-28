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

Reste DISTINCT de fichiers_uploades/consulter_bibliotheque (bibliothèque
perso) : catalogue consultable par les humains dans l'appli, jamais
injecté automatiquement dans une conversation.

MISE À JOUR 28/08/2026 (demande Bourama : "un truc qui permet à l'IA de
trouver un dossier ou fichier dans la bibliothèque publique par RAG") :
chaque ajout est désormais vectorisé (voir _indexer_catalogue_public
ci-dessous et core/catalogue_public_rag.py) pour que l'outil MCP
trouver_catalogue_public puisse le localiser -- mais ce RAG sert
UNIQUEMENT à identifier un document (nom/description/lien), jamais à
injecter son contenu dans une réponse générée automatiquement.
"""

import logging
import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from supabase import create_client

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.catalogue_public_rag import (
    indexer_pdf_catalogue_public,
    indexer_texte_catalogue_public,
    indexer_transcription_catalogue_public,
)
from core.description_multimedia import decrire_image_bibliotheque, transcrire_audio_bibliotheque

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

    entree = ligne.data[0]
    _indexer_catalogue_public(contenu, fichier.content_type, nom_original, entree["id"])
    return entree


def _indexer_catalogue_public(contenu: bytes, type_mime: str | None, nom_fichier: str, fichier_id: str) -> None:
    """
    Vectorise le document ajouté au catalogue public, selon son type
    réel (28/08/2026, demande Bourama : "que tout ce qui est mis dans
    la bibliothèque publique soit vectorisé", même logique que
    _indexer_et_propager dans api/bibliotheque_utilisateur.py pour la
    bibliothèque perso). Best-effort et non bloquant : le document est
    déjà stocké et catalogué à ce stade, seule la recherche par IA
    (trouver_catalogue_public) serait indisponible pour celui-ci en cas
    d'échec -- on log fort pour pouvoir réindexer manuellement si
    besoin, sans jamais faire échouer l'ajout.
    """
    try:
        if type_mime == "application/pdf":
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(contenu)
                chemin_temp = tmp.name
            try:
                indexer_pdf_catalogue_public(chemin_temp, fichier_id=fichier_id)
            finally:
                try:
                    os.remove(chemin_temp)
                except OSError:
                    pass
        elif (type_mime or "").startswith("image/"):
            description_image = decrire_image_bibliotheque(contenu, type_mime)
            if description_image:
                indexer_texte_catalogue_public(description_image, fichier_id=fichier_id)
        elif (type_mime or "").startswith("audio/"):
            segments_audio = transcrire_audio_bibliotheque(contenu, nom_fichier)
            if segments_audio:
                indexer_transcription_catalogue_public(segments_audio, fichier_id=fichier_id)
        elif type_mime == "text/plain":
            indexer_texte_catalogue_public(contenu.decode("utf-8", errors="ignore"), fichier_id=fichier_id)
    except Exception as e:
        logging.error(f"ERREUR vectorisation catalogue public (fichier_id={fichier_id}, type_mime={type_mime}) : {e}")


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
