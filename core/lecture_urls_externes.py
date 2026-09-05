# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Lecture de contenu externe cite dans un message utilisateur
# (GitHub, page web generique, image telechargee pour Gemini) et
# construction des "parts" Gemini pour les messages multimodaux.
import logging
import base64
import re
import requests
from constantes_agent import get_secret
from securite_url import valider_url_externe, UrlNonAutorisee

def _construire_parts_gemini(texte, images=None):
    """
    Construit la liste `parts` d'un message Gemini. Le texte est toujours
    présent ; `images` (si fourni) est une liste de tuples
    (bytes, mime_type), ajoutés en inline_data base64 -- format REST
    attendu par google-genai pour du contenu multimodal, voir
    https://ai.google.dev/gemini-api/docs/vision. Une image (cas simple)
    ou plusieurs (frames vidéo, voir _extraire_frames_video) sont traitées
    de la même façon.
    """
    parts = [{"text": texte}]
    for image_bytes, image_mime in (images or []):
        parts.append({
            "inline_data": {
                "mime_type": image_mime or "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            }
        })
    return parts


def _telecharger_image(image_url):
    """
    Télécharge l'image pointée par `image_url` (URL publique Supabase
    Storage, voir api/uploads.py:uploader_image_chat) pour l'envoyer en
    base64 à Gemini. On ne passe jamais l'URL telle quelle à Gemini : les
    URLs Supabase ne sont pas des URI Google Cloud Storage, `Part.from_uri`
    ne les accepterait pas.
    """
    valider_url_externe(image_url)  # anti-SSRF : voir core/securite_url.py
    reponse = requests.get(image_url, timeout=15)
    reponse.raise_for_status()
    return reponse.content, reponse.headers.get("content-type", "image/jpeg")


REGEX_URL = re.compile(r"https?://[^\s<>\"']+")
LONGUEUR_MAX_TEXTE_URL = 8_000  # caracteres, par lien, pour ne pas saturer le prompt


def _extraire_id_youtube(url):
    match = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/shorts/)([\w-]{11})", url)
    return match.group(1) if match else None


# Connecteur GitHub -- lien public collé dans le message par l'utilisateur
# (ou dépôt privé si connecté via OAuth, voir connexions/oauth_generique.py).
# Quatre formes de lien reconnues :
# - fichier précis : github.com/user/repo/blob/branche/chemin/fichier.py
# - dossier : github.com/user/repo/tree/branche/chemin -> liste NON
#   récursive du contenu à ce niveau (noms + type fichier/dossier), pas le
#   contenu des fichiers -- lire un dossier entier en profondeur est un
#   chantier à part (stratégie de sélection/troncature des fichiers).
# - branche seule : github.com/user/repo/tree/branche -> README de CETTE
#   branche précise (pas forcément la branche par défaut du dépôt).
# - dépôt entier : github.com/user/repo (sans /tree/ ni /blob/) -> README
#   de la branche par défaut.
REGEX_GITHUB_FICHIER = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/blob/([\w.\-/%]+?)/([^/\s]+\.\w+)"
)
REGEX_GITHUB_ARBORESCENCE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/tree/([\w.\-/%]+)")
REGEX_GITHUB_DEPOT = re.compile(r"github\.com/([\w.-]+)/([\w.-]+?)/?(?:\s|$)")


def _lire_github(url, user_id=None):
    """
    Récupère le contenu d'un lien GitHub collé dans le message. Deux
    formats reconnus (fichier précis ou dépôt entier), et TROIS niveaux
    d'authentification possibles pour chacun, du plus au moins privilégié :
    1. Token OAuth de LA PERSONNE connectée (voir
       connexions/oauth_generique.py, obtenir_token_valide("github", ...))
       -- seul niveau donnant accès aux dépôts PRIVÉS. Ajouté le
       2026-07-22 : nécessite que la personne ait connecté son compte
       GitHub ET qu'une GitHub OAuth App existe (GITHUB_CLIENT_ID/SECRET
       sur Railway) -- voir connexions/oauth_generique.py pour la config.
    2. GITHUB_TOKEN de la plateforme (voir plus bas) -- dépôts publics
       uniquement, mais lève la limite de 60 à 5000 requêtes/heure.
    3. Non authentifié -- dépôts publics, 60 requêtes/heure PARTAGÉES
       entre tous les utilisateurs (confirmé limitant en test réel).
    """
    token_utilisateur = None
    if user_id:
        try:
            from connexions.oauth_generique import obtenir_token_valide
            token_utilisateur = obtenir_token_valide("github", user_id)
        except Exception as e:
            logging.error(f"ERREUR LECTURE TOKEN GITHUB (user {user_id}) : {e}")

    m_fichier = REGEX_GITHUB_FICHIER.search(url)
    if m_fichier:
        utilisateur, depot, branche_et_chemin_partiel, nom_fichier = m_fichier.groups()
        chemin_complet = f"{branche_et_chemin_partiel}/{nom_fichier}"
        # Le premier segment de chemin_complet est la branche (main,
        # master...), le reste est le chemin réel dans le dépôt.
        segments = chemin_complet.split("/", 1)
        if len(segments) != 2:
            return None
        branche, chemin_fichier = segments
        raw_url = f"https://raw.githubusercontent.com/{utilisateur}/{depot}/{branche}/{chemin_fichier}"
        # raw.githubusercontent.com accepte un Authorization: Bearer pour
        # les dépôts privés (comportement GitHub, pas garanti stable dans
        # le temps -- à revalider si ce point casse un jour).
        headers = {"Authorization": f"Bearer {token_utilisateur}"} if token_utilisateur else {}
        try:
            reponse = requests.get(raw_url, timeout=10, headers=headers)
            if reponse.status_code != 200:
                logging.warning(f"LECTURE GITHUB ECHOUEE (fichier, statut {reponse.status_code}) : {raw_url}")
                return None
            return reponse.text[:LONGUEUR_MAX_TEXTE_URL]
        except Exception as e:
            logging.error(f"ERREUR LECTURE GITHUB (fichier) {raw_url} : {e}")
            return None

    m_arbo = REGEX_GITHUB_ARBORESCENCE.search(url)
    if m_arbo:
        utilisateur, depot, reste = m_arbo.groups()
        segments = reste.split("/", 1)
        branche = segments[0]
        chemin_dossier = segments[1] if len(segments) > 1 else None
        headers_auth = {"Authorization": f"Bearer {token_utilisateur}"} if token_utilisateur else {}

        if chemin_dossier:
            # Lien de dossier : liste NON récursive du contenu à ce
            # niveau (noms + type), pas le contenu des fichiers -- lire un
            # dossier entier en profondeur nécessiterait une stratégie de
            # sélection/troncature, hors de portée ici.
            api_url = f"https://api.github.com/repos/{utilisateur}/{depot}/contents/{chemin_dossier}?ref={branche}"
            try:
                reponse = requests.get(api_url, timeout=10, headers=headers_auth)
                if reponse.status_code != 200:
                    logging.warning(f"LECTURE GITHUB ECHOUEE (dossier, statut {reponse.status_code}) : {api_url}")
                    return None
                elements = reponse.json()
                if not isinstance(elements, list):
                    # L'API renvoie un objet (pas une liste) si le chemin
                    # pointe en fait vers un fichier, pas un dossier.
                    return None
                lignes = [
                    f"- {e['name']} ({'dossier' if e['type'] == 'dir' else 'fichier'})"
                    for e in elements
                ]
                return f"Contenu du dossier {chemin_dossier} (branche {branche}) :\n" + "\n".join(lignes)
            except Exception as e:
                logging.error(f"ERREUR LECTURE GITHUB (dossier) {api_url} : {e}")
                return None
        else:
            # Lien de branche seule : README de CETTE branche précise,
            # pas forcément la branche par défaut du dépôt.
            api_url = f"https://api.github.com/repos/{utilisateur}/{depot}/readme?ref={branche}"
            headers = {"Accept": "application/vnd.github.raw+json", **headers_auth}
            try:
                reponse = requests.get(api_url, timeout=10, headers=headers)
                if reponse.status_code != 200:
                    logging.warning(f"LECTURE GITHUB ECHOUEE (branche, statut {reponse.status_code}) : {api_url}")
                    return None
                return reponse.text[:LONGUEUR_MAX_TEXTE_URL]
            except Exception as e:
                logging.error(f"ERREUR LECTURE GITHUB (branche) {api_url} : {e}")
                return None

    m_depot = REGEX_GITHUB_DEPOT.search(url)
    if m_depot:
        utilisateur, depot = m_depot.groups()
        api_url = f"https://api.github.com/repos/{utilisateur}/{depot}/readme"
        headers = {"Accept": "application/vnd.github.raw+json"}
        # Priorité : token de la personne connectée (dépôts privés) >
        # GITHUB_TOKEN de la plateforme (dépôts publics, quota levé) >
        # non authentifié (dépôts publics, quota serré). Voir docstring.
        token_github = token_utilisateur or get_secret("GITHUB_TOKEN")
        if token_github:
            headers["Authorization"] = f"Bearer {token_github}"
        try:
            reponse = requests.get(api_url, timeout=10, headers=headers)
            if reponse.status_code != 200:
                logging.warning(f"LECTURE GITHUB ECHOUEE (dépôt, statut {reponse.status_code}) : {api_url}")
                return None
            return reponse.text[:LONGUEUR_MAX_TEXTE_URL]
        except Exception as e:
            logging.error(f"ERREUR LECTURE GITHUB (dépôt) {api_url} : {e}")
            return None

    return None


def _lire_url(url, user_id=None):
    """
    Récupère le contenu textuel d'un lien collé dans le message. Trois cas :
    - YouTube (vidéo) : transcript via youtube-transcript-api, pas de
      scraping HTML -- c'est notre seule "entrée vidéo" pour l'instant,
      limitée aux vidéos YouTube sous-titrées (voir note plus bas, pas de
      vrai traitement vidéo/image par frame).
    - GitHub (fichier ou dépôt) : contenu brut du fichier ou README du
      dépôt, voir _lire_github. `user_id` permet d'utiliser le token
      OAuth de la personne connectée si elle a lié son compte GitHub
      (voir connexions/oauth_generique.py) -- seul moyen de lire un dépôt
      PRIVÉ ; sans connexion, uniquement les dépôts publics (voir
      _lire_github pour le détail des 3 niveaux d'authentification).
    - Page web générique : extraction via trafilatura (garde le texte
      utile, jette nav/pubs/footer).
    Retourne None si l'extraction échoue (lien mort, page protégée, vidéo
    sans sous-titres...) -- on ne bloque jamais le message pour ça, on
    l'envoie tel quel au modèle.
    """
    id_youtube = _extraire_id_youtube(url)
    if id_youtube:
        try:
            # BUG corrigé le 2026-07-20 : même famille de bug que
            # trafilatura.fetch_url(timeout=...) -- youtube-transcript-api
            # 1.x a totalement changé son API par rapport à l'ancienne
            # version que j'avais en tête. `YouTubeTranscriptApi.get_transcript`
            # (méthode statique, résultat = liste de dicts) n'existe plus :
            # il faut instancier la classe et appeler `.fetch()` (méthode
            # d'instance), qui renvoie un objet FetchedTranscript itérable
            # de FetchedTranscriptSnippet (dataclasses avec un attribut
            # `.text`, pas une clé de dict `["text"]`). Confirmé cassé en
            # test réel le 2026-07-20 (lien YouTube collé, aucun contenu
            # récupéré, le modèle répondait qu'il ne pouvait pas voir de
            # vidéos -- comme pour trafilatura, l'exception était avalée
            # silencieusement par le except plus bas).
            from youtube_transcript_api import YouTubeTranscriptApi
            api = YouTubeTranscriptApi()
            transcript = api.fetch(id_youtube, languages=["fr", "en"])
            texte = " ".join(morceau.text for morceau in transcript)
            return texte[:LONGUEUR_MAX_TEXTE_URL]
        except Exception as e:
            logging.error(f"ERREUR TRANSCRIPT YOUTUBE ({url}): {e}")
            return None

    if "github.com" in url:
        # Avant le fallback trafilatura générique : un lien GitHub scrapé
        # comme une page HTML normale donnerait la navigation/sidebar de
        # l'interface GitHub, pas le vrai contenu du fichier/README.
        contenu_github = _lire_github(url, user_id)
        if contenu_github:
            return contenu_github
        # Si _lire_github échoue (dépôt privé, format de lien non
        # reconnu...), on retombe sur trafilatura plutôt que d'abandonner
        # -- au moins la page HTML publique GitHub reste lisible.

    try:
        import trafilatura
        valider_url_externe(url)  # anti-SSRF : voir core/securite_url.py
        # BUG corrigé le 2026-07-20 : trafilatura 2.1.0 n'a pas de paramètre
        # `timeout` sur fetch_url() (TypeError à CHAQUE appel, silencieux
        # car avalé par le except plus bas -- résultat : cette fonction ne
        # récupérait jamais aucun lien depuis le déploiement initial,
        # confirmé en testant en conditions réelles contre Wikipedia et
        # ia-info.fr, qui échouaient identiquement). Le timeout par défaut
        # de trafilatura reste raisonnable, pas besoin de le personnaliser.
        telechargement = trafilatura.fetch_url(url)
        if not telechargement:
            # Échec SILENCIEUX auparavant (aucun log) -- cas exact vécu le
            # 2026-07-20 : impossible de distinguer depuis les logs si le
            # lien a été bloqué (ex: 429, comme YouTube l'a fait à Claude
            # directement lors du diagnostic), jamais tenté, ou un autre
            # souci. trafilatura n'expose pas le code HTTP ici (fetch_url
            # avale l'erreur en interne), donc on log au moins le fait
            # qu'un téléchargement a été tenté et a échoué.
            logging.warning(f"LECTURE URL ECHOUEE (telechargement vide, ex: bloqué/429/timeout) : {url}")
            return None
        texte = trafilatura.extract(telechargement)
        if not texte:
            logging.warning(f"LECTURE URL ECHOUEE (page téléchargée mais aucun texte extrait, ex: page vide/JS-only) : {url}")
            return None
        return texte[:LONGUEUR_MAX_TEXTE_URL]
    except UrlNonAutorisee as e:
        # Distinct du except générique plus bas : ceci est un blocage
        # VOLONTAIRE (SSRF), pas un échec de récupération -- utile pour
        # repérer un pattern d'abus (voir core/securite_url.py).
        logging.warning(f"URL BLOQUEE (SSRF) : {url} -- {e}")
        return None
    except Exception as e:
        logging.error(f"ERREUR LECTURE URL ({url}): {e}")
        return None


def _enrichir_message_avec_urls(message, user_id=None):
    """
    Détecte les liens collés dans le message utilisateur, récupère leur
    contenu, et l'ajoute en contexte APRÈS le message original (jamais à la
    place) -- le modèle voit toujours la question telle que posée, plus le
    contenu des liens en pièce jointe textuelle. Le message ORIGINAL (sans
    enrichissement) reste ce qui est sauvegardé dans l'historique -- voir
    l'appel à _sauvegarder_echange dans chat(), qui reçoit toujours
    message_utilisateur brut, jamais message_pour_modele.

    `user_id` (2026-07-22) : transmis à _lire_url -> _lire_github pour
    utiliser le token GitHub de la personne si elle a connecté son compte
    (accès aux dépôts privés) -- voir connexions/oauth_generique.py.
    """
    urls = REGEX_URL.findall(message)
    if not urls:
        return message

    logging.info(f"LIEN(S) DETECTE(S) DANS LE MESSAGE : {urls[:3]}")

    blocs = []
    for url in urls[:3]:  # au plus 3 liens par message, pour le temps de réponse
        contenu = _lire_url(url, user_id)
        if contenu:
            blocs.append(f"[Contenu de {url}]\n{contenu}")

    if not blocs:
        logging.warning(f"AUCUN LIEN EXPLOITE sur {len(urls[:3])} détecté(s) -- message envoyé sans enrichissement : {urls[:3]}")
        return message

    return message + "\n\n" + "\n\n".join(blocs)


