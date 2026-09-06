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

Vidéo (06/09/2026, demande Bourama, chantier vectorisation à la demande)
: transcrire_et_decrire_video_bibliotheque ci-dessous réutilise les
briques ffmpeg déjà existantes pour la vidéo de CHAT (api/uploads.py :
_duree_video/_extraire_audio_video/_extraire_frames_video), dupliquées
ici volontairement -- même convention de duplication que le reste du
projet entre circuits (voir core/bibliotheque_rag.py vs
core/catalogue_public_rag.py) -- pour ne jamais faire dépendre ce
module core de la couche api. Ces briques n'étaient utilisées avant ce
chantier que pour un visionnage ponctuel dans le chat (jamais gardées
pour une recherche future) : ici elles alimentent l'indexation.
"""

import base64
import logging
import os
import subprocess
import tempfile

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


def transcrire_audio_bibliotheque(contenu: bytes, nom_fichier: str) -> list[dict] | None:
    """
    Transcrit un audio via Whisper (Groq), pour indexation texte.

    Renvoie désormais une LISTE DE SEGMENTS horodatés (26/08, citations
    cliquables -- chaque segment porte son "start"/"end" en secondes et
    son "text", format natif renvoyé par Whisper avec
    response_format="verbose_json") plutôt qu'un texte brut unique :
    voir core/bibliotheque_rag.py:indexer_transcription_bibliotheque, qui
    indexe chaque segment comme son propre chunk avec sa position.

    None si la transcription échoue ou si aucun segment exploitable n'en
    ressort (audio silencieux, hallucination Whisper connue sur CHAQUE
    segment).
    """
    from groq import Groq

    try:
        client_groq = Groq(api_key=_get_secret("GROQ_API_KEY"))
        transcription = client_groq.audio.transcriptions.create(
            file=(nom_fichier or "audio", contenu),
            model="whisper-large-v3",
            language="fr",
            response_format="verbose_json",
        )
    except Exception as e:
        logging.error(f"ERREUR TRANSCRIPTION AUDIO (bibliothèque) : {e}")
        return None

    segments_bruts = getattr(transcription, "segments", None) or []
    segments = []
    for segment in segments_bruts:
        # segment peut être un dict ou un objet selon la version du SDK Groq -- gère les deux
        texte = (segment.get("text") if isinstance(segment, dict) else segment.text) or ""
        texte = texte.strip()
        if not texte or texte.lower().rstrip(".") in PHRASES_HALLUCINEES_WHISPER:
            continue
        debut = segment.get("start") if isinstance(segment, dict) else segment.start
        fin = segment.get("end") if isinstance(segment, dict) else segment.end
        segments.append({"text": texte, "start": debut, "end": fin})

    return segments or None


# --- Vidéo (à la demande uniquement, voir docstring du module) ---

NB_FRAMES_VIDEO_BIBLIOTHEQUE = 5


def _duree_video_bibliotheque(chemin_video: str) -> float:
    resultat = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", chemin_video,
        ],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return float(resultat.stdout.strip())


def _extraire_audio_video_bibliotheque(chemin_video: str, chemin_audio_sortie: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", chemin_video, "-vn", "-ac", "1", "-ar", "16000",
            "-f", "wav", chemin_audio_sortie,
        ],
        capture_output=True, timeout=60, check=True,
    )


def _extraire_frames_video_bibliotheque(chemin_video: str, duree: float, nb_frames: int = NB_FRAMES_VIDEO_BIBLIOTHEQUE) -> list[bytes]:
    frames = []
    with tempfile.TemporaryDirectory() as dossier:
        for i in range(nb_frames):
            instant = duree * (i + 1) / (nb_frames + 1)
            chemin_frame = os.path.join(dossier, f"frame_{i}.jpg")
            try:
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-ss", str(instant), "-i", chemin_video,
                        "-frames:v", "1", "-vf", "scale=512:-1", chemin_frame,
                    ],
                    capture_output=True, timeout=20, check=True,
                )
            except Exception as e:
                logging.error(f"ERREUR EXTRACTION FRAME VIDEO BIBLIOTHEQUE {i} (t={instant:.1f}s) : {e}")
                continue
            if os.path.exists(chemin_frame):
                with open(chemin_frame, "rb") as f:
                    frames.append(f.read())
    return frames


def transcrire_et_decrire_video_bibliotheque(contenu: bytes, nom_fichier: str, extension: str) -> dict | None:
    """
    Traite une vidéo À LA DEMANDE (jamais automatiquement, voir docstring
    du module) : transcrit sa piste audio (Whisper/Groq, réutilise
    transcrire_audio_bibliotheque) ET décrit quelques images clés
    (Gemini vision, réutilise decrire_image_bibliotheque) -- mêmes
    briques que la vidéo de chat (api/uploads.py:uploader_video_chat),
    dupliquées ici (voir docstring du module).

    Renvoie {"segments_audio": [...] ou [], "descriptions_frames": [str, ...] ou []}
    -- jamais None sauf échec total (aucun son exploitable ET aucune
    frame décrite), l'appelant décide alors qu'il n'y a rien à indexer.
    """
    with tempfile.NamedTemporaryFile(suffix=f".{extension}", delete=False) as f_video:
        f_video.write(contenu)
        chemin_video = f_video.name

    try:
        try:
            duree = _duree_video_bibliotheque(chemin_video)
        except Exception as e:
            logging.error(f"ERREUR FFPROBE (durée vidéo bibliothèque, {nom_fichier}) : {e}")
            return None

        segments_audio = []
        chemin_audio = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f_audio:
                chemin_audio = f_audio.name
            _extraire_audio_video_bibliotheque(chemin_video, chemin_audio)
            with open(chemin_audio, "rb") as f:
                segments_audio = transcrire_audio_bibliotheque(f.read(), nom_fichier) or []
        except Exception as e:
            logging.error(f"ERREUR EXTRACTION/TRANSCRIPTION AUDIO VIDEO BIBLIOTHEQUE ({nom_fichier}) : {e}")
        finally:
            if chemin_audio and os.path.exists(chemin_audio):
                try:
                    os.remove(chemin_audio)
                except OSError:
                    pass

        descriptions_frames = []
        try:
            for frame in _extraire_frames_video_bibliotheque(chemin_video, duree):
                description = decrire_image_bibliotheque(frame, "image/jpeg")
                if description:
                    descriptions_frames.append(description)
        except Exception as e:
            logging.error(f"ERREUR DESCRIPTION FRAMES VIDEO BIBLIOTHEQUE ({nom_fichier}) : {e}")

        if not segments_audio and not descriptions_frames:
            return None
        return {"segments_audio": segments_audio, "descriptions_frames": descriptions_frames}
    finally:
        if os.path.exists(chemin_video):
            try:
                os.remove(chemin_video)
            except OSError:
                pass
