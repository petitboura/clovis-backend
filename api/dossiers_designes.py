"""
Cree le 04/09/2026, Bourama : "apres avoir choisi un dossier, tout ce
qu'il contient hormis video est vectorise" -- l'app transfere ici chaque
fichier du dossier designe le plus vite possible (upload brut, aucun
traitement), la vectorisation elle-meme part ensuite en arriere-plan cote
serveur (core/vectorisation_dossiers_designes.py), independamment de
l'etat du telephone (ferme, hors ligne, eteint -- voir demande explicite
de Bourama).

MIS A JOUR le 06/09/2026 (demande Bourama) : la video est desormais
ACCEPTEE ici -- l'interdiction du 04/09 n'existait que parce que TOUT
etait vectorise automatiquement (cout juge trop eleve pour la video).
Depuis ce chantier, seule l'image garde une vraie vectorisation
automatique ; pdf/word/excel/texte recoivent une extraction de texte
GRATUITE automatique ; audio ET video ne recoivent plus RIEN
d'automatique -- tout (y compris la vraie vectorisation de la video,
transcription + description de frames) se fait desormais A LA DEMANDE
(core/vectorisation_dossiers_designes.py::vectoriser_maintenant, appele
depuis core/outils_mobile.py::explorer_dossier). La raison du refus
disparait donc pour la video comme pour l'audio.

Distinct de api/bibliotheque_utilisateur.py (ajout manuel a "Mon espace")
: ici c'est TOUT le contenu d'un dossier designe (core/dossiers_designes_
mobile.py), envoye automatiquement par le plugin natif (DossiersPlugin),
jamais un ajout explicite fichier par fichier.
"""

import hashlib
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from postgrest.exceptions import APIError

from api.auth import utilisateur_courant, supabase
from core.erreurs import erreur_api
from core.vectorisation_dossiers_designes import BUCKET_DOSSIERS_DESIGNES, necessite_extraction_texte, necessite_vectorisation

router = APIRouter(prefix="/api/dossiers-designes", tags=["dossiers-designes"])

TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo, meme limite que la bibliotheque perso (api/bibliotheque_utilisateur.py)


@router.post("/upload", status_code=201)
async def uploader_fichier_dossier_designe(
    fichier: UploadFile = File(...),
    dossier_nom: str = Form(...),
    plateforme: str = Form(...),
    chemin: str = Form("[]"),  # JSON -- liste ordonnee de noms de sous-dossiers depuis la racine designee
    utilisateur=Depends(utilisateur_courant),
):
    """
    Stocke le fichier immediatement et renvoie -- tout traitement
    (extraction de texte gratuite ou vraie vectorisation) part en file
    d'attente ou attend une demande explicite (voir docstring du
    module), jamais traite par cette requete.
    """
    try:
        chemin_liste = json.loads(chemin)
        if not isinstance(chemin_liste, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise erreur_api(400, "CHEMIN_INVALIDE_LISTE_JSON_ATTENDUE")

    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise erreur_api(400, "FICHIER_TROP_LOURD_50_MO_MAX")

    nom_fichier = fichier.filename or "fichier"
    type_mime = fichier.content_type or "application/octet-stream"
    hash_contenu = hashlib.sha256(contenu).hexdigest()
    extension = nom_fichier.rsplit(".", 1)[-1] if "." in nom_fichier else "bin"
    chemin_stockage = f"dossiers_designes/{utilisateur.id}/{uuid.uuid4()}.{extension}"

    try:
        supabase.storage.from_(BUCKET_DOSSIERS_DESIGNES).upload(
            chemin_stockage, contenu, {"content-type": type_mime}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload dossier designe {chemin_stockage}) : {e}")
        raise erreur_api(500, "ECHEC_DU_TRANSFERT")

    url_publique = supabase.storage.from_(BUCKET_DOSSIERS_DESIGNES).get_public_url(chemin_stockage)

    # Categorisation (06/09) : image = vraie vectorisation auto ;
    # pdf/word/excel/texte = extraction gratuite auto + vraie
    # vectorisation a la demande ; audio/video/inconnu = tout a la
    # demande, rien d'automatique.
    if necessite_vectorisation(type_mime):
        statut_vectorisation = "en_attente"
        statut_extraction_texte = "non_applicable"
    elif necessite_extraction_texte(type_mime, nom_fichier):
        statut_vectorisation = "a_la_demande"
        statut_extraction_texte = "en_attente"
    else:
        statut_vectorisation = "a_la_demande"
        statut_extraction_texte = "non_applicable"

    ligne = {
        "user_id": utilisateur.id,
        "plateforme": plateforme,
        "dossier_nom": dossier_nom,
        "chemin": chemin_liste,
        "nom_fichier": nom_fichier,
        "type_mime": type_mime,
        "taille_octets": len(contenu),
        "chemin_stockage": chemin_stockage,
        "url_publique": url_publique,
        "hash_contenu": hash_contenu,
        "statut_vectorisation": statut_vectorisation,
        "statut_extraction_texte": statut_extraction_texte,
        "tentatives_vectorisation": 0,
        "erreur_vectorisation": None,
        "derniere_tentative_vectorisation_a": None,
    }

    try:
        # upsert sur la contrainte unique (user_id, plateforme, dossier_nom,
        # chemin, nom_fichier) -- un meme fichier renvoye (retry app, relance
        # apres coupure) remplace la ligne existante plutot que d'en creer
        # une en double, et repart proprement en vectorisation.
        insertion = (
            supabase.table("fichiers_dossier_designe")
            .upsert(ligne, on_conflict="user_id,plateforme,dossier_nom,chemin,nom_fichier")
            .execute()
        )
    except APIError as e:
        logging.error(f"ERREUR ECRITURE fichiers_dossier_designe ({chemin_stockage}) : {e}")
        raise erreur_api(500, "ECHEC_ENREGISTREMENT")

    return insertion.data[0]


@router.get("/progression")
async def progression_dossier(
    dossier_nom: str,
    plateforme: str,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Avancement du TRAITEMENT AUTOMATIQUE d'un dossier designe (image
    vectorisee, pdf/word/excel/texte extraits) -- destine a la barre de
    progression cote app (voir echange avec Bourama du 04/09, "etape 5").
    "a_la_demande" (06/09 : audio/video/pdf-word-excel-texte en attente
    d'une recherche en direct) compte comme "pret" ici : aucun traitement
    automatique supplementaire ne les concerne, la barre ne doit pas
    rester bloquee dessus.
    """
    try:
        lignes = (
            supabase.table("fichiers_dossier_designe")
            .select("statut_vectorisation")
            .eq("user_id", utilisateur.id)
            .eq("dossier_nom", dossier_nom)
            .eq("plateforme", plateforme)
            .execute()
        ).data or []
    except APIError as e:
        logging.error(f"ERREUR LECTURE progression dossier designe ({dossier_nom}) : {e}")
        raise erreur_api(500, "ECHEC_LECTURE_PROGRESSION")

    total = len(lignes)
    prets = sum(1 for l in lignes if l["statut_vectorisation"] in ("pret", "a_la_demande"))
    echecs = sum(1 for l in lignes if l["statut_vectorisation"] == "echec")
    en_cours = total - prets - echecs

    return {
        "total": total,
        "prets": prets,
        "en_cours": en_cours,
        "echecs": echecs,
        "termine": total > 0 and prets + echecs == total,
    }
