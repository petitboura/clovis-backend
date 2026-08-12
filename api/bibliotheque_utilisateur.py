"""
Bibliothèque personnelle d'un utilisateur (2026-08-01, demande Bourama :
nouvelle section "Mon espace" -- "tu peux y uploader autant de documents
que tu veux et ton IA les utilise pour te répondre, dans n'importe
quelle conversation, n'importe quel chat").

Mirroir volontaire des routes POST/GET/DELETE
/{agent_id}/bibliotheque de api/agents.py, mais niveau="utilisateur"
(voir core/bibliotheque_fichiers.py), scopé par l'utilisateur connecté
lui-même -- pas d'agent_id, pas de vérification de propriété d'agent
(c'est TOUJOURS "soi-même" ici).

Différence clé avec la bibliothèque niveau "agent" : le PDF est vectorisé
dans une table dédiée (documents_bibliotheque, scopée user_id), séparée
de la table `documents` (RAG agent, scopée agent_id) -- voir
core/bibliotheque_rag.py. L'outil de conversation correspondant,
consulter_bibliotheque, est disponible pour TOUS les agents sans
configuration par le créateur (voir core/mcp_tools.py).
"""

import logging
import os
import sys
import tempfile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
from bibliotheque_fichiers import enregistrer_fichier, enregistrer_lien, lister_fichiers, supprimer_fichier  # noqa: E402
from bibliotheque_rag import indexer_pdf_bibliotheque, indexer_texte_bibliotheque  # noqa: E402

router = APIRouter(prefix="/api/bibliotheque", tags=["bibliotheque-utilisateur"])

TYPES_AUTORISES = {
    "application/pdf",
    "image/jpeg", "image/png", "image/webp",
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4",
    "video/mp4", "video/webm", "video/quicktime",
}
TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo, même limite que la bibliothèque niveau agent


@router.post("", status_code=201)
async def uploader_document(
    request: Request,
    fichier: UploadFile = File(...),
    titre: str = Form(None),
    description: str = Form(None),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Ajoute un fichier à la bibliothèque personnelle de l'utilisateur
    connecté. Comme au niveau agent : un PDF est en plus vectorisé
    (indexer_pdf_bibliotheque) pour que consulter_bibliotheque puisse
    répondre à partir de son contenu -- les autres types restent
    retrouvables par nom/description via chercher_fichier uniquement.
    """
    # CORRECTION du 01/08 (Bourama : "plusieurs upload à la fois") :
    # description/titre ne sont plus obligatoires -- repli sur le nom du
    # fichier tel quel, pour ne pas forcer une saisie manuelle par
    # fichier quand on en envoie plusieurs d'un coup. chercher_fichier
    # (recherche par nom/description) reste utilisable, juste moins
    # fin sans description écrite à la main.
    if fichier.content_type not in TYPES_AUTORISES:
        raise erreur_api(400, "TYPE_DE_FICHIER_NON_SUPPORTE")

    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise erreur_api(400, "FICHIER_TROP_LOURD_50_MO_MAX")

    nom_original = fichier.filename or "fichier"
    description_finale = (
        f"{titre.strip()} — {description.strip()}" if (titre or "").strip() and (description or "").strip()
        else (description or titre or "").strip() or nom_original
    )

    try:
        ligne = enregistrer_fichier(
            contenu=contenu,
            nom_fichier=nom_original,
            type_mime=fichier.content_type,
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=description_finale,
        )
    except Exception:
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    if fichier.content_type == "application/pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenu)
            chemin_temp = tmp.name
        try:
            indexer_pdf_bibliotheque(chemin_temp, fichier_id=ligne["id"], user_id=utilisateur.id)
        except Exception as e:
            # Non bloquant : le fichier est déjà stocké et retrouvable par
            # chercher_fichier, seule la recherche par CONTENU (consulter_
            # bibliotheque) sera indisponible pour celui-ci. On log fort
            # pour pouvoir réindexer manuellement si besoin, mais on ne
            # fait pas échouer l'upload -- déjà réussi à ce stade.
            logging.error(f"ERREUR vectorisation PDF bibliothèque perso (fichier_id={ligne['id']}) : {e}")
        finally:
            try:
                os.remove(chemin_temp)
            except OSError:
                pass

    journaliser(
        action="bibliotheque_perso.ajoute",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"description": description_finale, "type_mime": fichier.content_type},
        request=request,
    )

    return ligne


class AjouterLienPayload(BaseModel):
    url: str
    titre: str = None
    description: str = None


@router.post("/lien", status_code=201)
def ajouter_lien(
    payload: AjouterLienPayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """Pendant de uploader_document ci-dessus pour une entrée "lien" (voir enregistrer_lien)."""
    # Assoupli le 01/08 (Bourama : "pas de filtre au moment de l'upload",
    # ajout groupé fichiers+liens+texte en une seule action) : plus de
    # titre/description obligatoire, repli sur l'URL elle-même -- même
    # logique que uploader_document ci-dessus depuis la correction
    # "plusieurs upload à la fois".
    if not (payload.url or "").strip():
        raise erreur_api(400, "URL_MANQUANTE")

    description_finale = (
        f"{payload.titre.strip()} — {payload.description.strip()}"
        if (payload.titre or "").strip() and (payload.description or "").strip()
        else (payload.description or payload.titre or "").strip() or payload.url.strip()
    )

    try:
        ligne = enregistrer_lien(
            url=payload.url.strip(),
            nom_fichier=(payload.titre or payload.url).strip(),
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=description_finale,
        )
    except Exception:
        raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DU_LIEN")

    journaliser(
        action="bibliotheque_perso.ajoute",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"description": description_finale, "type_mime": "text/uri-list"},
        request=request,
    )

    return ligne


class AjouterTextePayload(BaseModel):
    contenu: str
    titre: str = None


@router.post("/texte", status_code=201)
def ajouter_texte(
    payload: AjouterTextePayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Note de texte tapée/collée directement (2026-08-01, demande Bourama :
    "ajoute le cas des liens et du texte", "pas de filtre au moment de
    l'upload") -- stockée comme un fichier .txt ordinaire (même mécanisme
    que uploader_document, type_mime="text/plain" sert de marqueur côté
    frontend pour le sous-onglet "Texte"), mais indexée DIRECTEMENT
    (pas besoin d'extraction, contrairement à un PDF) : immédiatement
    consultable par consulter_bibliotheque.
    """
    contenu = (payload.contenu or "").strip()
    if not contenu:
        raise erreur_api(400, "TEXTE_VIDE")

    titre = (payload.titre or "").strip()
    nom_fichier = f"{titre or 'Note'}.txt"

    try:
        ligne = enregistrer_fichier(
            contenu=contenu.encode("utf-8"),
            nom_fichier=nom_fichier,
            type_mime="text/plain",
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=titre or (contenu[:80] + ("…" if len(contenu) > 80 else "")),
        )
    except Exception:
        raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DE_LA_NOTE")

    try:
        indexer_texte_bibliotheque(contenu, fichier_id=ligne["id"], user_id=utilisateur.id)
    except Exception as e:
        logging.error(f"ERREUR vectorisation note texte bibliothèque perso (fichier_id={ligne['id']}) : {e}")

    journaliser(
        action="bibliotheque_perso.ajoute",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"description": titre, "type_mime": "text/plain"},
        request=request,
    )

    return ligne


@router.get("")
def lister(utilisateur=Depends(utilisateur_courant)):
    # Corrigé le 01/08 (Bourama) : d'abord une comparaison fragile sur le
    # texte de la description (incomplète, ratait audio/image/vidéo, ne
    # gardait que "Document..."), remplacée par le vrai filtre origine
    # (voir migration fichiers_uploades_origine + enregistrer_fichier) --
    # ne remonte QUE ce qui a été ajouté explicitement ici, jamais un
    # fichier envoyé en pièce jointe de conversation.
    return lister_fichiers("utilisateur", user_id=utilisateur.id, origine="bibliotheque")


@router.delete("/{fichier_id}", status_code=204)
def supprimer(fichier_id: str, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Vérifie que le fichier appartient bien à l'utilisateur connecté avant
    de le supprimer -- contrairement à la bibliothèque niveau agent (où
    la vérification passe par le owner_id de l'agent), il faut ici lire
    la ligne fichiers_uploades elle-même.
    """
    try:
        res = supabase.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture fichier {fichier_id} avant suppression bibliothèque perso) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_SUPPRIMER_CE_FICHIER_POUR")

    if not res or not res.data:
        raise erreur_api(404, "FICHIER_INTROUVABLE")
    if res.data["user_id"] != utilisateur.id:
        raise erreur_api(403, "CE_FICHIER_NE_T_APPARTIENT_PAS")

    # documents_bibliotheque est en ON DELETE CASCADE sur fichier_id (voir
    # migration) : pas besoin de nettoyer les chunks vectorisés ici.
    supprimer_fichier(fichier_id)

    journaliser(
        action="bibliotheque_perso.supprime",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"fichier_id": fichier_id},
        request=request,
    )
