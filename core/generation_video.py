"""
Génération vidéo via fal.ai (modèle Wan 2.6, ~0,05-0,07$/seconde -- le
moins cher du marché en juillet 2026, mais À COMPARER : ~15-25x le coût
d'une image ou de l'audio pour un même "message". Voir échange avec
Bourama du 2026-07-21 : construit quand même, mais gaté comme les
images, budget à surveiller de près si jamais activé.

PARTICULARITÉ IMPORTANTE PAR RAPPORT À TOUS LES AUTRES MODULES
generation_*.py : la génération vidéo prend 1 à 3 minutes chez fal.ai,
impossible d'attendre ça en plein milieu d'une réponse de chat (l'agent
resterait bloqué). Le flux est donc OBLIGATOIREMENT en 2 temps, comme
pour generation_signature.py :
1. lancer_generation_video() : soumet la demande, renvoie un
   request_id IMMÉDIATEMENT, la vidéo n'est pas encore prête.
2. statut_video() : à rappeler plus tard (l'agent devra soit
   redemander à l'étudiant de revenir, soit ce sera automatisé plus
   tard via un mécanisme de rappel -- pas construit ici, voir la
   fonctionnalité "notifications" mise de côté).
"""

import logging
import os
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE = "wan/v2.6/text-to-video"
BASE_URL = "https://queue.fal.run"


def _get_secret(cle):
    return os.environ.get(cle)


def video_disponible() -> bool:
    return bool(_get_secret("FAL_KEY"))


def _headers():
    return {"Authorization": f"Key {_get_secret('FAL_KEY')}", "Content-Type": "application/json"}


def lancer_generation_video(prompt: str, duree_secondes: int = 5) -> dict:
    """
    Soumet une demande de génération vidéo. Ne renvoie PAS la vidéo
    elle-même (elle n'existe pas encore) : renvoie un request_id à
    utiliser avec statut_video() dans 1 à 3 minutes.
    """
    if not video_disponible():
        raise RuntimeError("Génération vidéo indisponible : FAL_KEY n'est pas configurée.")

    reponse = requests.post(
        f"{BASE_URL}/{MODELE}",
        headers=_headers(),
        json={"prompt": prompt, "duration": duree_secondes},
        timeout=30,
    )
    reponse.raise_for_status()
    resultat = reponse.json()
    return {
        "request_id": resultat["request_id"],
        "statut": "EN_COURS",
    }


def statut_video(request_id: str) -> dict:
    """
    Consulte l'état d'une génération vidéo lancée avec
    lancer_generation_video(). Si terminée, télécharge la vidéo,
    l'uploade dans Supabase Storage, et renvoie son URL publique. Sinon,
    renvoie juste le statut d'avancement.
    """
    if not video_disponible():
        raise RuntimeError("Génération vidéo indisponible : FAL_KEY n'est pas configurée.")

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
    url_video_temporaire = reponse_resultat.json()["video"]["url"]

    video_bytes = requests.get(url_video_temporaire, timeout=120).content

    chemin = f"videos/{uuid.uuid4()}.mp4"
    try:
        supabase.storage.from_(BUCKET).upload(chemin, video_bytes, {"content-type": "video/mp4"})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload video {chemin}) : {e}")
        raise

    url_finale = supabase.storage.from_(BUCKET).get_public_url(chemin)
    return {"statut": "COMPLETED", "url": url_finale}
