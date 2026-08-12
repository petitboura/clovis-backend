"""
Synthèse vocale (TTS) -- DEUX fournisseurs, même logique que
generation_images.py (Pollinations/Together) :

1. Google Cloud Text-to-Speech, GRATUIT dans la limite de 4 millions de
   caractères/mois (voix "Standard") -- un vrai palier gratuit permanent,
   pas un essai limité dans le temps. Nécessite un projet Google Cloud
   avec facturation activée (une carte bancaire à ajouter, mais jamais
   débitée tant que le quota n'est pas dépassé) et une clé API.
   Utilisé PAR DÉFAUT si GOOGLE_TTS_API_KEY est configurée.

   HISTORIQUE (22/07/2026) : deux tentatives précédentes via Hugging
   Face (Kokoro-82M puis microsoft/speecht5_tts) ont toutes les deux
   échoué en test réel -- "hf-inference" (l'infra gratuite de HF) a
   réduit sa portée mi-2025 et ne sert plus fiablement de modèles TTS,
   confirmé par erreur "Model not supported by provider hf-inference".
   Abandonné au profit de Google Cloud TTS, qui a un vrai palier
   gratuit documenté et une API stable.

2. Groq / Orpheus, payant (~22$/million de caractères) : utilisé EN
   PRIORITÉ si AUDIO_TTS_ACTIF="true" ET GROQ_API_KEY présente (déjà là
   pour le chat, mais gatée par un interrupteur dédié). Meilleure
   latence/qualité pour un usage à volume.

NON TESTÉ EN CONDITIONS RÉELLES pour le chemin Google Cloud (à
confirmer au premier vrai test, comme d'habitude -- mais cette fois le
format de requête est vérifié contre la documentation officielle
Google, pas déduit d'un exemple possiblement obsolète).
"""

import base64
import logging
import os
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE_GROQ = "canopylabs/orpheus-v1-english"
VOIX_PAR_DEFAUT = "austin"


def _get_secret(cle):
    return os.environ.get(cle)


def _groq_actif() -> bool:
    interrupteur = (_get_secret("AUDIO_TTS_ACTIF") or "").strip().lower() == "true"
    return interrupteur and bool(_get_secret("GROQ_API_KEY"))


def audio_disponible() -> bool:
    return bool(_get_secret("GOOGLE_TTS_API_KEY")) or _groq_actif()


def _generer_via_google(texte: str) -> bytes:
    cle = _get_secret("GOOGLE_TTS_API_KEY")
    reponse = requests.post(
        f"https://texttospeech.googleapis.com/v1/text:synthesize?key={cle}",
        json={
            "input": {"text": texte},
            # Voix "Standard" (pas WaveNet/Neural2) : c'est précisément
            # la catégorie couverte par le plus gros palier gratuit
            # (4M caractères/mois) -- voir docstring en tête de fichier.
            "voice": {"languageCode": "fr-FR", "name": "fr-FR-Standard-A"},
            "audioConfig": {"audioEncoding": "MP3"},
        },
        timeout=30,
    )
    if reponse.status_code >= 400:
        raise RuntimeError(f"Google Cloud TTS a renvoyé {reponse.status_code} : {reponse.text[:500]}")
    audio_base64 = reponse.json()["audioContent"]
    return base64.b64decode(audio_base64)


def _generer_via_groq(texte: str, voix: str) -> bytes:
    cle = _get_secret("GROQ_API_KEY")
    reponse = requests.post(
        "https://api.groq.com/openai/v1/audio/speech",
        headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
        json={"model": MODELE_GROQ, "input": texte, "voice": voix, "response_format": "wav"},
        timeout=60,
    )
    if reponse.status_code >= 400:
        raise RuntimeError(f"Groq a renvoyé {reponse.status_code} : {reponse.text[:500]}")
    return reponse.content


def generer_audio(texte: str, voix: str = VOIX_PAR_DEFAUT) -> str:
    """
    Utilise Groq/Orpheus si explicitement activé (AUDIO_TTS_ACTIF=true,
    payant, meilleure latence/qualité), sinon Google Cloud TTS (gratuit
    jusqu'à 4M caractères/mois, nécessite GOOGLE_TTS_API_KEY). Uploade
    dans Supabase Storage, renvoie l'URL publique.

    `voix` n'est utilisé que par le chemin Groq -- Google Cloud utilise
    une voix française fixe côté code (voir _generer_via_google).
    Le format de sortie diffère aussi (MP3 pour Google, WAV pour Groq) :
    l'extension du fichier stocké s'adapte en conséquence.
    """
    if _groq_actif():
        audio_bytes = _generer_via_groq(texte, voix)
        extension, content_type = "wav", "audio/wav"
    elif _get_secret("GOOGLE_TTS_API_KEY"):
        audio_bytes = _generer_via_google(texte)
        extension, content_type = "mp3", "audio/mpeg"
    else:
        raise RuntimeError(
            "Génération audio indisponible : ni GOOGLE_TTS_API_KEY (gratuit) ni "
            "AUDIO_TTS_ACTIF+GROQ_API_KEY (payant) ne sont configurés."
        )

    chemin = f"audio/{uuid.uuid4()}.{extension}"
    try:
        supabase.storage.from_(BUCKET).upload(chemin, audio_bytes, {"content-type": content_type})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload audio {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)
