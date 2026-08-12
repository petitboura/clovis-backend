"""
Conversion de documents (Word/Excel/PowerPoint) en PDF, pour permettre un
aperçu visuel fidèle dans le chat (composant FichierChip.tsx côté
frontend, qui réutilise le visualiseur PDF déjà existant plutôt que de
construire un rendu dédié par format).

Utilise CloudConvert (https://cloudconvert.com), UNIQUEMENT si
CLOUDCONVERT_API_KEY est configurée -- même principe que
generation_images.py (TOGETHER_API_KEY) et generation_video.py (FAL_KEY) :
la fonctionnalité reste invisible/désactivée tant que la clé n'est pas
ajoutée sur Railway, pas d'erreur bruyante pour l'agent ni l'utilisateur.

Décision Bourama du 25/07 : tier gratuit CloudConvert pour démarrer
(10 conversions/jour, tous utilisateurs confondus -- voir échange du
même jour). À surveiller si le volume dépasse cette limite ; CloudConvert
renvoie alors une erreur 422/429 explicite, propagée telle quelle par
`conversion_disponible()`/`convertir_en_pdf()` ci-dessous (pas de retry
automatique pour l'instant, volontairement simple).

NON TESTÉ EN CONDITIONS RÉELLES (clé pas encore configurée au moment de
l'écriture, 25/07/2026). À vérifier au premier vrai test, comme
d'habitude.
"""

import logging
import os
import time

import requests

API_BASE = "https://api.cloudconvert.com/v2"
DELAI_POLL_SECONDES = 2
TIMEOUT_TOTAL_SECONDES = 90


def _get_secret(cle):
    return os.environ.get(cle)


def conversion_disponible() -> bool:
    """
    True si CLOUDCONVERT_API_KEY est configurée sur Railway. Conservée
    comme fonction séparée (plutôt qu'un simple `if` inline) pour rester
    cohérente avec image_generation_disponible() et permettre aux
    appelants (api/uploads.py, generation_documents.py) de savoir à
    l'avance s'il faut proposer un aperçu PDF ou seulement le fichier
    original.
    """
    return bool(_get_secret("CLOUDCONVERT_API_KEY"))


def convertir_en_pdf(contenu_bytes: bytes, nom_fichier: str) -> bytes:
    """
    Envoie un fichier (docx/xlsx/pptx, ou tout format supporté par
    CloudConvert) et renvoie les bytes du PDF résultant.

    Lève une exception si CLOUDCONVERT_API_KEY est absente, si
    CloudConvert échoue (quota dépassé, fichier corrompu, etc.), ou si la
    conversion dépasse TIMEOUT_TOTAL_SECONDES -- à l'appelant de décider
    quoi faire (ex: continuer sans aperçu plutôt que de faire échouer tout
    l'upload/la génération, voir api/uploads.py et generation_documents.py).
    """
    cle = _get_secret("CLOUDCONVERT_API_KEY")
    if not cle:
        raise RuntimeError("CLOUDCONVERT_API_KEY non configurée -- conversion PDF indisponible.")

    entetes = {"Authorization": f"Bearer {cle}", "Content-Type": "application/json"}

    # 1. Créer le job avec 3 étapes : importer le fichier, le convertir,
    #    exposer le résultat via une URL de téléchargement.
    reponse_job = requests.post(
        f"{API_BASE}/jobs",
        headers=entetes,
        json={
            "tasks": {
                "importer": {"operation": "import/upload"},
                "convertir": {
                    "operation": "convert",
                    "input": "importer",
                    "output_format": "pdf",
                },
                "exporter": {"operation": "export/url", "input": "convertir"},
            }
        },
        timeout=20,
    )
    reponse_job.raise_for_status()
    job = reponse_job.json()["data"]

    tache_import = next(t for t in job["tasks"] if t["name"] == "importer")
    upload_form = tache_import["result"]["form"]

    # 2. Uploader le fichier vers l'URL fournie par CloudConvert (upload
    #    direct, pas via notre propre Storage -- CloudConvert ne stocke le
    #    fichier que le temps de la conversion, cf. leur politique de
    #    suppression automatique sous 24h).
    reponse_upload = requests.post(
        upload_form["url"],
        data=upload_form["parameters"],
        files={"file": (nom_fichier, contenu_bytes)},
        timeout=30,
    )
    reponse_upload.raise_for_status()

    # 3. Attendre la fin du job (polling simple -- CloudConvert propose
    #    aussi un endpoint /jobs/{id}/wait bloquant côté serveur, mais le
    #    polling manuel donne un contrôle plus explicite sur notre propre
    #    timeout et évite de dépendre d'un comportement de blocage HTTP
    #    long, moins prévisible derrière certains proxys).
    id_job = job["id"]
    debut = time.monotonic()
    while True:
        if time.monotonic() - debut > TIMEOUT_TOTAL_SECONDES:
            raise TimeoutError(f"Conversion PDF trop longue (> {TIMEOUT_TOTAL_SECONDES}s) pour {nom_fichier}.")

        reponse_statut = requests.get(f"{API_BASE}/jobs/{id_job}", headers=entetes, timeout=15)
        reponse_statut.raise_for_status()
        job_actuel = reponse_statut.json()["data"]

        if job_actuel["status"] == "error":
            tache_en_erreur = next(
                (t for t in job_actuel["tasks"] if t["status"] == "error"), None
            )
            message = tache_en_erreur.get("message", "erreur inconnue") if tache_en_erreur else "erreur inconnue"
            raise RuntimeError(f"Échec conversion CloudConvert pour {nom_fichier} : {message}")

        if job_actuel["status"] == "finished":
            break

        time.sleep(DELAI_POLL_SECONDES)

    tache_export = next(t for t in job_actuel["tasks"] if t["name"] == "exporter")
    url_pdf = tache_export["result"]["files"][0]["url"]

    reponse_pdf = requests.get(url_pdf, timeout=30)
    reponse_pdf.raise_for_status()
    return reponse_pdf.content
