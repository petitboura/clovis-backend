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

MISE À JOUR 28/08/2026 bis (demande Bourama : "le bouton + doit être
comme en privé -- texte/lien/fichier/dossier, nom et description
optionnels même pour un dossier") : ajout de "/lien" et "/texte" en
plus de l'upload de fichier, même principe que api/bibliotheque_
utilisateur.py côté perso. `dossier_id` optionnel sur les 3 routes
d'ajout pour classer directement à l'ajout (voir api/dossiers_
catalogue_public.py).
"""

import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from postgrest.exceptions import APIError
from pydantic import BaseModel
from supabase import create_client

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.file_attente_vectorisation import necessite_vectorisation_fichier_publique, necessite_vectorisation_note
from core.dossiers_catalogue_public import ranger_fichier as _ranger_fichier_dossier, peut_ajouter_contenu as _peut_ajouter_contenu_dossier, _dossier as _dossier_catalogue_public
from core.dossiers_publics_attaches import propager_fichier_public_range_dossier as _propager_fichier_public_range_dossier
from core.listes_bibliotheque_publique import lister_valeurs, normaliser_et_enregistrer

router = APIRouter(prefix="/api/bibliotheque-publique", tags=["bibliotheque_publique"])


def _classer_si_autorise(fichier_id: str, dossier_id: str, utilisateur_id: str) -> None:
    if not (dossier_id or "").strip():
        return
    try:
        if _dossier_catalogue_public(dossier_id) and _peut_ajouter_contenu_dossier(dossier_id, utilisateur_id):
            _ranger_fichier_dossier(fichier_id, dossier_id)
            _propager_fichier_public_range_dossier(fichier_id, dossier_id)
    except Exception as e:
        logging.error(f"ERREUR classement dossier catalogue public (fichier_id={fichier_id}, dossier_id={dossier_id}) : {e}")


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
    # 29/08/2026, file d'attente de vectorisation en arrière-plan (voir
    # core/file_attente_vectorisation.py) : "en_attente" / "en_cours" /
    # "pret" / "echec" -- sert au frontend pour le badge par fichier.
    statut_vectorisation: str = "pret"
    # 02/09/2026, demande Bourama : 3 filtres cochables à la publication
    # (voir core/listes_bibliotheque_publique.py), optionnels.
    pays: str | None = None
    classe: str | None = None
    categorie: str | None = None


@router.get("/listes")
def lister_listes_filtres():
    """Valeurs déjà connues pour pays/classe/catégorie, pour peupler les menus du formulaire de publication ET les filtres de recherche côté frontend."""
    return {
        "pays": lister_valeurs("pays"),
        "classes": lister_valeurs("classe"),
        "categories": lister_valeurs("categorie"),
    }


@router.get("", response_model=list[EntreeBibliothequePublique])
def lister_bibliotheque_publique(q: str | None = None, pays: str | None = None, classe: str | None = None, categorie: str | None = None):
    # Filtre statut="publie" (22/08, chantier signalements) : une entrée
    # retirée par un admin suite à un signalement reste en base (trace
    # pour l'audit) mais ne doit plus jamais réapparaître dans le
    # catalogue, voir api/signalements.py.
    requete = (
        supabase.table("bibliotheque_publique")
        .select("id, nom, description, nom_fichier, type_mime, taille_octets, url_publique, created_at, statut_vectorisation, pays, classe, categorie")
        .eq("statut", "publie")
    )
    if (q or "").strip():
        requete = requete.or_(f"nom.ilike.%{q.strip()}%,description.ilike.%{q.strip()}%")
    # 02/09/2026, demande Bourama : filtres pays/classe/catégorie, en
    # plus du filtre par type déjà géré côté frontend.
    if (pays or "").strip():
        requete = requete.eq("pays", pays.strip())
    if (classe or "").strip():
        requete = requete.eq("classe", classe.strip())
    if (categorie or "").strip():
        requete = requete.eq("categorie", categorie.strip())
    res = requete.order("created_at", desc=True).limit(200).execute()
    return res.data or []


@router.post("", response_model=EntreeBibliothequePublique, status_code=201)
async def ajouter_a_bibliotheque_publique(
    fichier: UploadFile = File(...),
    nom: str = Form(""),
    description: str = Form(""),
    dossier_id: str = Form(""),
    pays: str = Form(""),
    classe: str = Form(""),
    categorie: str = Form(""),
    utilisateur=Depends(utilisateur_courant),
):
    # Nom optionnel (28/08, demande Bourama : "nom et description
    # optionnels même pour dossier") -- repli sur le nom du fichier
    # sans extension, même logique que la bibliothèque perso.
    nom_final = (nom or "").strip() or (fichier.filename or "Document").rsplit(".", 1)[0]

    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise erreur_api(400, "FICHIER_TROP_LOURD_50_MO_MAX")

    nom_original = fichier.filename or "fichier"
    extension = nom_original.rsplit(".", 1)[-1] if "." in nom_original else "bin"
    chemin_stockage = f"publique/{uuid.uuid4()}.{extension}"

    def _stocker_et_inserer():
        # Factorisé le 02/09 (bug remonté par Bourama : upload perçu
        # comme lent) pour pouvoir déporter l'ENSEMBLE storage+DB sur un
        # thread via asyncio.to_thread -- ces appels Supabase sont
        # synchrones/bloquants, et appelés tels quels dans cette route
        # async, ils bloquaient tout le serveur (event loop) pendant
        # toute la durée de l'upload.
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, contenu, {"content-type": fichier.content_type or "application/octet-stream"}
        )
        url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)
        return (
            supabase.table("bibliotheque_publique")
            .insert({
                "ajoute_par": utilisateur.id,
                "nom": nom_final,
                "description": (description or "").strip(),
                "nom_fichier": nom_original,
                "chemin_stockage": chemin_stockage,
                "url_publique": url_publique,
                "type_mime": fichier.content_type,
                "taille_octets": len(contenu),
                # 29/08/2026, file d'attente de vectorisation en
                # arrière-plan (voir core/file_attente_vectorisation.py) :
                # avant, la vectorisation (_indexer_catalogue_public,
                # retirée) se faisait ici, de façon synchrone et
                # bloquante -- long sur un gros fichier ou un upload en
                # masse.
                "statut_vectorisation": "en_attente" if necessite_vectorisation_fichier_publique(fichier.content_type) else "pret",
                # 02/09/2026, demande Bourama : 3 filtres optionnels à la
                # publication (voir core/listes_bibliotheque_publique.py).
                "pays": normaliser_et_enregistrer("pays", pays),
                "classe": normaliser_et_enregistrer("classe", classe),
                "categorie": normaliser_et_enregistrer("categorie", categorie),
            })
            .execute()
        )

    try:
        ligne = await asyncio.to_thread(_stocker_et_inserer)
    except APIError as e:
        # CORRECTIF 02/09 (bug remonté par Bourama : aucun traitement
        # d'erreur à l'upload, notamment pour les doublons désormais
        # refusés par un index unique Supabase -- code Postgres 23505).
        if getattr(e, "code", None) == "23505":
            raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PUBLIQUE", nom=nom_original)
        logging.error(f"ERREUR SUPABASE (upload bibliothèque publique {chemin_stockage}) : {e}")
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (upload bibliothèque publique {chemin_stockage}) : {e}")
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    entree = ligne.data[0]
    await asyncio.to_thread(_classer_si_autorise, entree["id"], dossier_id, utilisateur.id)
    return entree


class AjouterLienPayload(BaseModel):
    url: str
    nom: str = ""
    description: str = ""
    dossier_id: str = ""
    pays: str = ""
    classe: str = ""
    categorie: str = ""


@router.post("/lien", response_model=EntreeBibliothequePublique, status_code=201)
def ajouter_lien_bibliotheque_publique(payload: AjouterLienPayload, utilisateur=Depends(utilisateur_courant)):
    """Ajoute un lien au catalogue public (28/08, parité avec le sélecteur du privé). Pas de fichier réel : url_publique EST le lien lui-même."""
    if not (payload.url or "").strip():
        raise erreur_api(400, "URL_MANQUANTE")
    nom_final = (payload.nom or "").strip() or payload.url.strip()

    try:
        ligne = (
            supabase.table("bibliotheque_publique")
            .insert({
                "ajoute_par": utilisateur.id,
                "nom": nom_final,
                "description": (payload.description or "").strip(),
                "nom_fichier": nom_final,
                "url_publique": payload.url.strip(),
                "type_mime": "text/uri-list",
                "statut_vectorisation": "pret",  # un lien n'est jamais vectorisé
                "pays": normaliser_et_enregistrer("pays", payload.pays),
                "classe": normaliser_et_enregistrer("classe", payload.classe),
                "categorie": normaliser_et_enregistrer("categorie", payload.categorie),
            })
            .execute()
        )
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PUBLIQUE", nom=nom_final)
        logging.error(f"ERREUR ECRITURE bibliotheque_publique (lien) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    except Exception as e:
        logging.error(f"ERREUR ECRITURE bibliotheque_publique (lien) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    entree = ligne.data[0]
    _classer_si_autorise(entree["id"], payload.dossier_id, utilisateur.id)
    return entree


class AjouterTextePayload(BaseModel):
    contenu: str
    nom: str = ""
    dossier_id: str = ""
    pays: str = ""
    classe: str = ""
    categorie: str = ""


@router.post("/texte", response_model=EntreeBibliothequePublique, status_code=201)
def ajouter_texte_bibliotheque_publique(payload: AjouterTextePayload, utilisateur=Depends(utilisateur_courant)):
    """Ajoute une note de texte libre au catalogue public (28/08, parité avec le sélecteur du privé) -- stockée comme un .txt ordinaire, indexée directement."""
    contenu_texte = (payload.contenu or "").strip()
    if not contenu_texte:
        raise erreur_api(400, "TEXTE_VIDE")
    nom_final = (payload.nom or "").strip() or (contenu_texte[:80] + ("…" if len(contenu_texte) > 80 else ""))
    contenu_octets = contenu_texte.encode("utf-8")
    nom_fichier = f"{nom_final}.txt"
    chemin_stockage = f"publique/{uuid.uuid4()}.txt"

    try:
        supabase.storage.from_(BUCKET).upload(chemin_stockage, contenu_octets, {"content-type": "text/plain"})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (note texte bibliothèque publique {chemin_stockage}) : {e}")
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    try:
        ligne = (
            supabase.table("bibliotheque_publique")
            .insert({
                "ajoute_par": utilisateur.id,
                "nom": nom_final,
                "description": "",
                "nom_fichier": nom_fichier,
                "chemin_stockage": chemin_stockage,
                "url_publique": url_publique,
                "type_mime": "text/plain",
                "taille_octets": len(contenu_octets),
                "statut_vectorisation": "en_attente" if necessite_vectorisation_note() else "pret",
                "pays": normaliser_et_enregistrer("pays", payload.pays),
                "classe": normaliser_et_enregistrer("classe", payload.classe),
                "categorie": normaliser_et_enregistrer("categorie", payload.categorie),
            })
            .execute()
        )
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PUBLIQUE", nom=nom_fichier)
        logging.error(f"ERREUR ECRITURE bibliotheque_publique (texte) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    except Exception as e:
        logging.error(f"ERREUR ECRITURE bibliotheque_publique (texte) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    entree = ligne.data[0]
    _classer_si_autorise(entree["id"], payload.dossier_id, utilisateur.id)
    # Vectorisation en arrière-plan (29/08, voir core/file_attente_vectorisation.py) --
    # avant, indexer_texte_catalogue_public était appelé directement ici.
    return entree


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
