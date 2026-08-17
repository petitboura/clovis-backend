"""
Description automatique d'image (vision) et transcription automatique
d'audio, pour que ces fichiers deviennent vectorisables comme un texte
normal (17/08/2026, demande Bourama : "images/audio/vidéo retrouvables
par leur contenu réel, pas juste par le nom tapé à la main").

Réutilise les mêmes fournisseurs que le reste du projet (Gemini pour la
vision, Groq Whisper pour l'audio -- voir api/uploads.py:extraire_formule
et api/uploads.py:uploader_audio_chat) mais appliqués ici à l'ajout
EXPLICITE dans la bibliothèque (api/bibliotheque_utilisateur.py), pas au
chat -- cette distinction est volontaire, voir le REVERT du 01/08 déjà
documenté dans api/uploads.py (un fichier de CHAT ne doit PAS rejoindre
consulter_bibliotheque, seul un ajout explicite depuis "Mon espace" ou
"Mon programme" en fait partie).

Vidéo non couverte ici (nécessiterait extraction de frames + ffmpeg,
chantier à part) -- reste seulement retrouvable par nom/description.
"""

import base64
import logging

from google import genai

logging.basicConfig(level=logging.INFO)

PROMPT_DESCRIPTION_IMAGE = (
    "Décris cette image en français, de façon factuelle et complète, "
    "pour qu'un moteur de recherche par mots-clés puisse la retrouver à "
    "partir de son contenu. Si c'est une feuille d'exercice, un manuel "
    "ou une capture d'écran de cours, retranscris intégralement le texte "
    "et les formules visibles (en LaTeX si c'est une formule "
    "mathématique). Sinon, décris ce qui est visible (objets, scène, "
    "texte lisible s'il y en a). Réponds uniquement avec la description, "
    "sans préambule ni commentaire."
)

# Mêmes hallucinations Whisper connues que api/uploads.py -- dupliqué
# volontairement ici (petite constante statique) plutôt qu'importé
# depuis api/uploads.py, pour ne pas faire dépendre ce module core de la
# couche api.
PHRASES_HALLUCINEES_WHISPER = {
    "sous-titrage société radio-canada",
    "sous-titrage societe radio-canada",
    "sous-titres réalisés par la communauté d'amara.org",
    "sous-titres realises par la communaute d'amara.org",
    "merci d'avoir regardé cette vidéo",
    "merci d'avoir regardé la vidéo",
    "abonnez-vous à la chaîne",
    "www.tvsubtitles.net",
    "merci.",
    "sous-titres",
}


def _get_secret(cle):
    import os

    return os.environ.get(cle)


def decrire_image_bibliotheque(contenu: bytes, type_mime: str) -> str | None:
    """
    Décrit une image via Gemini vision, pour indexation texte (voir
    indexer_texte_bibliotheque). None si Gemini échoue ou ne détecte
    rien -- l'appelant doit alors se rabattre sur nom/description
    tapés à la main (comportement inchangé par rapport à avant).
    """
    try:
        client_google = genai.Client(api_key=_get_secret("GOOGLE_API_KEY"))
        reponse = client_google.models.generate_content(
            model="gemini-2.5-flash",
            contents=[{
                "role": "user",
                "parts": [
                    {"text": PROMPT_DESCRIPTION_IMAGE},
                    {"inline_data": {"mime_type": type_mime, "data": base64.b64encode(contenu).decode("utf-8")}},
                ],
            }],
        )
    except Exception as e:
        logging.error(f"ERREUR GEMINI (description image bibliothèque) : {e}")
        return None

    texte = (reponse.text or "").strip()
    return texte or None


def transcrire_audio_bibliotheque(contenu: bytes, nom_fichier: str) -> str | None:
    """
    Transcrit un audio via Whisper (Groq), pour indexation texte. None
    si la transcription échoue, est vide, ou correspond à une
    hallucination Whisper connue sur audio silencieux.
    """
    from groq import Groq

    try:
        client_groq = Groq(api_key=_get_secret("GROQ_API_KEY"))
        transcription = client_groq.audio.transcriptions.create(
            file=(nom_fichier or "audio", contenu),
            model="whisper-large-v3",
            language="fr",
        )
    except Exception as e:
        logging.error(f"ERREUR TRANSCRIPTION AUDIO (bibliothèque) : {e}")
        return None

    texte = (transcription.text or "").strip()
    if not texte or texte.lower().rstrip(".") in PHRASES_HALLUCINEES_WHISPER:
        return None
    return texte
