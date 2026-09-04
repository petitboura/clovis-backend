"""
Cree le 04/09/2026, Bourama : "apres avoir choisi un dossier, tout ce
qu'il contient hormis video est vectorise" -- l'app transfere ici chaque
fichier du dossier designe le plus vite possible (upload brut, aucun
traitement), la vectorisation elle-meme part ensuite en arriere-plan cote
serveur (core/vectorisation_dossiers_designes.py), independamment de
l'etat du telephone (ferme, hors ligne, eteint -- voir demande explicite
de Bourama).

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
from core.vectorisation_dossiers_designes import BUCKET_DOSSIERS_DESIGNES, necessite_vectorisation

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
    Stocke le fichier immediatement et renvoie -- la vectorisation part
    en file d'attente (core/vectorisation_dossiers_designes.py), traitee
    en arriere-plan par le process serveur, jamais par cette requete.
    Video explicitement refusee (jamais vectorisee, cout trop eleve --
    voir echange avec Bourama du 04/09) : a filtrer cote app avant meme
    d'envoyer, mais revalide ici par securite.
    """
    if (fichier.content_type or "").startswith("video/"):
        raise erreur_api(400, "VIDEO_NON_ACCEPTEE_ICI")

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
    statut_vectorisation = "en_attente" if necessite_vectorisation(type_mime, nom_fichier) else "pret"

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
    Avancement de la vectorisation d'un dossier designe -- destine a la
    barre de progression cote app (a brancher, voir echange avec Bourama
    du 04/09, "etape 5"). Compte simplement les statuts en base : rien a
    calculer cote serveur au fil de l'eau, la file d'attente met deja
    chaque ligne a jour en continu.
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
    prets = sum(1 for l in lignes if l["statut_vectorisation"] == "pret")
    echecs = sum(1 for l in lignes if l["statut_vectorisation"] == "echec")
    en_cours = total - prets - echecs

    return {
        "total": total,
        "prets": prets,
        "en_cours": en_cours,
        "echecs": echecs,
        "termine": total > 0 and prets + echecs == total,
    }
