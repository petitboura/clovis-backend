"""
Outils MCP de génération média (image, 3D, vidéo, audio, signature
électronique) -- la plupart enregistrés conditionnellement selon la
disponibilité d'une clé API (voir chaque `if ..._disponible():`).

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code.
"""

import logging

from core.generation_signature import (
    envoyer_pour_signature as _envoyer_pour_signature,
    statut_signature as _statut_signature,
    signature_disponible,
)
from core.generation_audio import generer_audio as _generer_audio, audio_disponible
from core.generation_video import (
    lancer_generation_video as _lancer_generation_video,
    statut_video as _statut_video,
    video_disponible,
)
from core.generation_3d import (
    lancer_generation_3d as _lancer_generation_3d,
    statut_modele_3d as _statut_modele_3d,
    modele_3d_disponible,
)
from core.generation_images import generer_image as _generer_image, image_generation_disponible

from core.outils_generation_commun import mcp_generation, Context, _sauvegarder_generation_bibliotheque



# Enregistré conditionnellement, gate par FAL_KEY (MEME cle que la
# video, voir generation_3d.py). Meme flux en 2 outils que la video,
# pour la meme raison (generation pas instantanee).
if modele_3d_disponible():
    @mcp_generation.tool()
    def lancer_generation_3d(prompt: str) -> str:
        """
        Lance une génération de modèle 3D (.glb) à partir d'une
        description textuelle. NE renvoie PAS le modèle immédiatement :
        renvoie un identifiant à donner à consulter_statut_generation
        (type "3d") un peu plus tard. Préviens l'étudiant que ça prend
        un peu de temps.
        """
        try:
            resultat = _lancer_generation_3d(prompt)
            return (
                f"Génération 3D lancée (id: {resultat['request_id']}). "
                f"Redemande le statut avec cet identifiant dans une minute ou deux."
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : le lancement de la génération 3D a échoué, réessaie."


# Enregistré conditionnellement, gate par FAL_KEY (voir
# generation_video.py). IMPORTANT : la génération vidéo prend 1-3
# minutes, donc en 2 outils separes (lancer + consulter), jamais un
# seul outil bloquant -- l'agent doit dire a l'utilisateur de revenir
# verifier un peu plus tard, pas rester bloque a attendre.
if video_disponible():
    @mcp_generation.tool()
    def lancer_generation_video(prompt: str, duree_secondes: int = 5) -> str:
        """
        Lance une génération vidéo à partir d'une description
        textuelle. NE renvoie PAS la vidéo (elle prend 1 à 3 minutes à
        générer) : renvoie un identifiant à donner à
        consulter_statut_generation (type "video") un peu plus tard.
        Préviens l'étudiant que ça prend du temps et qu'il doit
        redemander le statut dans quelques minutes.
        """
        try:
            resultat = _lancer_generation_video(prompt, duree_secondes)
            return (
                f"Génération lancée (id: {resultat['request_id']}). "
                f"Ça prend 1 à 3 minutes, redemande le statut avec cet identifiant un peu plus tard."
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : le lancement de la génération vidéo a échoué, réessaie."


# Enregistré conditionnellement, gate par interrupteur dédié (voir
# generation_audio.py : GROQ_API_KEY existe déjà pour le chat, donc ne
# peut pas servir de gate ici, il faut qu'AUDIO_TTS_ACTIF="true" soit
# mis explicitement par Bourama).
if audio_disponible():
    @mcp_generation.tool()
    def generer_audio(texte: str, voix: str = "austin", ctx: Context = None) -> str:
        """
        Convertit du texte en audio parlé (voix naturelle). Le texte
        peut inclure des indications vocales entre crochets, ex.
        "[cheerful] Bienvenue !". Renvoie l'URL publique du fichier
        audio généré.
        """
        try:
            url = _generer_audio(texte, voix)
            extension = url.rsplit(".", 1)[-1].split("?", 1)[0] if "." in url.rsplit("/", 1)[-1] else "mp3"
            nom = (texte[:40].strip() or "Audio") + f".{extension}"
            _sauvegarder_generation_bibliotheque(ctx, url, nom, f"audio/{extension}")
            return url
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : la génération audio a échoué, réessaie."


# Enregistré conditionnellement, même logique que generer_image ci-dessous :
# LUMIN_API_KEY absente -> l'agent ne voit tout simplement pas ces outils.
if signature_disponible():
    @mcp_generation.tool()
    def envoyer_pour_signature(titre: str, contenu_markdown: str, signataires: list) -> str:
        """
        Génère un document PDF à partir d'un contenu markdown et
        l'envoie pour signature électronique (via Lumin) à un ou
        plusieurs signataires. `signataires` : liste de
        {"nom": ..., "email": ...}. Chaque signataire reçoit un email
        avec un lien pour signer. Renvoie l'identifiant de la demande
        de signature et son statut.
        """
        try:
            resultat = _envoyer_pour_signature(titre, contenu_markdown, signataires)
            return (
                f"Demande de signature envoyée (id: {resultat['signature_request_id']}, "
                f"statut: {resultat['statut']}). Document : {resultat['url_document']}"
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : l'envoi pour signature a échoué, réessaie."


if modele_3d_disponible() or signature_disponible():
    @mcp_generation.tool()
    def consulter_statut_generation(type: str, request_id: str, ctx: Context = None) -> str:
        """
        Consulte l'état d'une génération asynchrone déjà lancée
        (modèle 3D, vidéo ou demande de signature), consolidé le 26/08,
        un seul outil, plusieurs types au lieu de 3 outils séparés.
        Même forme pour les trois : un identifiant en entrée, un texte
        de statut en sortie.

        `type` doit être l'un de :
        - "3d" : état d'une génération 3D lancée avec
          lancer_generation_3d. Si terminée, renvoie l'URL publique du
          fichier .glb.
        - "video" : état d'une génération vidéo lancée avec
          lancer_generation_video. Si terminée, renvoie l'URL publique
          de la vidéo. Sinon, indique qu'elle est toujours en cours.
        - "signature" : état d'une demande de signature déjà envoyée
          avec envoyer_pour_signature (en attente, signé, expiré...).

        `request_id` : l'identifiant renvoyé par l'outil de lancement
        correspondant (`request_id` pour "3d"/"video",
        `signature_request_id` pour "signature").
        """
        if type == "3d":
            if not modele_3d_disponible():
                return "Erreur : la génération 3D n'est pas disponible actuellement."
            try:
                resultat = _statut_modele_3d(request_id)
                if resultat["statut"] == "COMPLETED":
                    _sauvegarder_generation_bibliotheque(ctx, resultat["url"], f"Modele_3D_{request_id}.glb", "model/gltf-binary")
                    return f"Modèle 3D prêt : {resultat['url']}"
                return f"Toujours en cours (statut : {resultat['statut']}), redemande un peu plus tard."
            except Exception as e:
                logging.error(f"ERREUR consulter_statut_generation (3d) : {e}")
                return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."

        if type == "video":
            if not video_disponible():
                return "Erreur : la génération vidéo n'est pas disponible actuellement."
            try:
                resultat = _statut_video(request_id)
                if resultat["statut"] == "COMPLETED":
                    _sauvegarder_generation_bibliotheque(ctx, resultat["url"], f"Video_{request_id}.mp4", "video/mp4")
                    return f"Vidéo prête : {resultat['url']}"
                return f"Toujours en cours (statut : {resultat['statut']}), redemande dans une minute."
            except Exception as e:
                logging.error(f"ERREUR consulter_statut_generation (video) : {e}")
                return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."

        if type == "signature":
            if not signature_disponible():
                return "Erreur : la signature électronique n'est pas disponible actuellement."
            try:
                return str(_statut_signature(request_id))
            except Exception as e:
                logging.error(f"ERREUR consulter_statut_generation (signature) : {e}")
                return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."

        return (
            f"Erreur : type '{type}' inconnu. Types valides : 3d, video, signature."
        )


# Toujours actif : Pollinations (gratuit, sans clé) par défaut, bascule
# automatique vers Together AI (payant, meilleure qualité) si
# TOGETHER_API_KEY est configurée -- voir generation_images.py. Plus de
# condition ici, contrairement à la signature/audio/vidéo/3D qui, eux,
# n'ont pas d'équivalent gratuit connu.
@mcp_generation.tool()
def generer_image(prompt: str, ctx: Context = None) -> str:
    """
    Génère une image à partir d'une description textuelle. Renvoie
    l'URL publique de l'image générée.
    """
    try:
        url = _generer_image(prompt)
        extension = url.rsplit(".", 1)[-1].split("?", 1)[0] if "." in url.rsplit("/", 1)[-1] else "png"
        nom = (prompt[:40].strip() or "Image") + f".{extension}"
        _sauvegarder_generation_bibliotheque(ctx, url, nom, f"image/{extension}")
        return url
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération de l'image a échoué, réessaie."
