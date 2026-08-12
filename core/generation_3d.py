"""
Génération de modèles 3D via fal.ai (Hunyuan3D v3.1, endpoint "Rapid",
~0,225$/génération -- voir https://fal.ai/hunyuan-3d).

RÉUTILISE FAL_KEY (la même clé que generation_video.py) : si la vidéo
est déjà activée, la 3D l'est aussi automatiquement, et inversement.
Pas de nouvelle clé à créer.

Même flux asynchrone que generation_video.py (soumission + statut
séparés) : la génération prend de l'ordre de la minute, pas instantané
comme les images.

INCERTITUDE À VÉRIFIER EN CONDITIONS RÉELLES : le nom exact du champ
contenant l'URL du fichier .glb dans la réponse de fal.ai n'a pas pu
être confirmé par la documentation publique au moment de l'écriture
(20/07/2026). Le code essaie plusieurs noms probables
(model_mesh/model_glb/glb/mesh) ; si aucun ne correspond, l'erreur
renvoyée inclut la réponse brute pour ajuster rapidement.
"""

import logging
import os
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE = "fal-ai/hunyuan3d-v3/text-to-3d"
BASE_URL = "https://queue.fal.run"


def _get_secret(cle):
    return os.environ.get(cle)


def modele_3d_disponible() -> bool:
    # Reutilise FAL_KEY : voir generation_video.py, meme cle.
    return bool(_get_secret("FAL_KEY"))


def _headers():
    return {"Authorization": f"Key {_get_secret('FAL_KEY')}", "Content-Type": "application/json"}


def lancer_generation_3d(prompt: str) -> dict:
    """
    Soumet une demande de génération de modèle 3D à partir d'une
    description textuelle. Renvoie un request_id à utiliser avec
    statut_modele_3d() un peu plus tard (pas de résultat immédiat).
    """
    if not modele_3d_disponible():
        raise RuntimeError("Génération 3D indisponible : FAL_KEY n'est pas configurée.")

    reponse = requests.post(
        f"{BASE_URL}/{MODELE}",
        headers=_headers(),
        json={"prompt": prompt},
        timeout=30,
    )
    reponse.raise_for_status()
    return {"request_id": reponse.json()["request_id"], "statut": "EN_COURS"}


def statut_modele_3d(request_id: str) -> dict:
    """
    Consulte l'état d'une génération 3D lancée avec
    lancer_generation_3d(). Si terminée, télécharge le fichier .glb,
    l'uploade dans Supabase Storage, et renvoie son URL publique.
    """
    if not modele_3d_disponible():
        raise RuntimeError("Génération 3D indisponible : FAL_KEY n'est pas configurée.")

    reponse_statut = requests.get(
        f"{BASE_URL}/{MODELE}/requests/{request_id}/status",
        headers=_headers(),
        timeout=30,
    )
    reponse_statut.raise_for_status()
    statut = reponse_statut.json().get("status")

    if statut != "COMPLETED":
        return {"statut": statut, "url": None}

    reponse_resultat = requests.get(
        f"{BASE_URL}/{MODELE}/requests/{request_id}",
        headers=_headers(),
        timeout=30,
    )
    reponse_resultat.raise_for_status()
    donnees = reponse_resultat.json()

    # Essaie plusieurs noms de champ probables -- voir avertissement
    # d'incertitude en tête de fichier.
    url_glb_temporaire = None
    for cle_possible in ("model_mesh", "model_glb", "glb", "mesh"):
        if cle_possible in donnees and isinstance(donnees[cle_possible], dict):
            url_glb_temporaire = donnees[cle_possible].get("url")
            if url_glb_temporaire:
                break

    if not url_glb_temporaire:
        raise RuntimeError(
            f"Champ contenant l'URL du .glb introuvable dans la réponse fal.ai : {donnees!r}"
        )

    fichier_bytes = requests.get(url_glb_temporaire, timeout=60).content

    chemin = f"3d/{uuid.uuid4()}.glb"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin, fichier_bytes, {"content-type": "model/gltf-binary"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload 3d {chemin}) : {e}")
        raise

    url_finale = supabase.storage.from_(BUCKET).get_public_url(chemin)
    return {"statut": "COMPLETED", "url": url_finale}
