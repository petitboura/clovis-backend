"""
Connexion "par utilisateur" générique, pour des outils externes standards
(OAuth 2.0 + PKCE classique, client fixe créé une fois pour toutes chez le
fournisseur -- Slack, GitHub, Trello, etc.).

NE COUVRE PAS NOTION : Notion s'auto-enregistre dynamiquement (DCR, RFC
7591) au lieu d'avoir un client fixe -- particularité propre à Notion,
qui reste dans connexions/notion.py, séparé.

Un seul fichier pour tous les services de ce type, au lieu d'un fichier
par service : ce qui change d'un service à l'autre (adresses, client
id/secret, scopes demandés) est décrit dans SERVICES ci-dessous, pas
dans du code dupliqué.

POUR AJOUTER UN NOUVEAU SERVICE (ex. Slack) :
1. Créer une app OAuth chez le fournisseur (une fois, manuellement,
   comme pour Google) -> obtenir client_id/client_secret.
2. Mettre ces deux valeurs dans Railway (ex. SLACK_CLIENT_ID,
   SLACK_CLIENT_SECRET).
3. Ajouter une entrée dans SERVICES ci-dessous.
4. Ajouter l'outil dans core/registre_outils.py comme pour Notion
   ("necessite_utilisateur": True, headers_builder -> obtenir_token_valide
   de CE fichier, avec le nom du service).
Aucun autre fichier à toucher.

FLOW (identique dans l'esprit à connexions/notion.py) :
1. demarrer_connexion(service, user_id, agent_id) -> URL à ouvrir.
2. finaliser_connexion(service, code, state) -> échange le code, stocke
   les tokens dans connexions_oauth (scopé par user_id + service).
3. obtenir_token_valide(service, user_id) -> access_token prêt à
   l'emploi, rafraîchi automatiquement si besoin.

Un access_token expiré peu importe -- c'est le comportement du
refresh_token qui varie le plus d'un fournisseur à l'autre (certains le
font tourner comme Notion, d'autres gardent le même indéfiniment) ; ce
module suit le cas le plus courant (refresh_token stable, remplacé
seulement s'il est effectivement renvoyé) plutôt que de forcer une
rotation qui ne concerne pas tous les services.
"""

import os
import secrets
import string
import hashlib
import base64
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from supabase import create_client

MARGE_RAFRAICHISSEMENT = timedelta(minutes=5)


def _calculer_expiration(tokens):
    """
    Corrigé le 2026-07-24 après un vrai test GitHub en production : avant
    ce fix, l'absence de `expires_in` dans la réponse (ex. GitHub sans
    "Enable token expiration" activé sur l'app OAuth -- comportement par
    défaut) faisait supposer une expiration factice de 3600s. Le token
    GitHub réel restait valide indéfiniment, mais après 1h le système
    croyait devoir le rafraîchir, échouait (pas de refresh_token émis
    dans ce cas), et la connexion semblait "morte" alors qu'elle ne
    l'était pas -- symptôme observé : "Compte GitHub non connecté" pour
    un compte pourtant bien connecté une heure plus tôt.

    Si `expires_in` est présent (Notion, Google, et GitHub SI l'option
    d'expiration est activée), comportement inchangé : expiration réelle
    + rafraîchissement normal. Absent -> on traite le token comme valable
    très longtemps (100 ans), au lieu de deviner une durée fausse.
    """
    if "expires_in" in tokens:
        return datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
    return datetime.now(timezone.utc) + timedelta(days=365 * 100)


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
URL_RETOUR = get_secret("URL_RETOUR_APP")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


# Config par service : rien de plus à écrire en code pour un nouveau
# service qui suit le standard OAuth 2.0 + PKCE avec client fixe.
# client_id/client_secret sont lus depuis Railway au nom indiqué, pas
# codés en dur ici.
#
# "token_headers" (optionnel, ajouté 2026-07-22 pour GitHub) : en-têtes
# additionnels envoyés SEULEMENT lors de l'échange/rafraîchissement de
# token (pas sur l'URL d'autorisation). Nécessaire pour GitHub, dont le
# endpoint de token répond en `application/x-www-form-urlencoded` par
# défaut au lieu de JSON -- `reponse.json()` plus bas planterait sans
# `Accept: application/json`. Absent (comme pour les autres services) ->
# aucun en-tête supplémentaire, comportement inchangé.
SERVICES = {
    # Exemple à dupliquer/adapter pour un vrai service, ex. Slack :
    # "slack": {
    #     "authorization_endpoint": "https://slack.com/oauth/v2/authorize",
    #     "token_endpoint": "https://slack.com/api/oauth.v2.access",
    #     "client_id_env": "SLACK_CLIENT_ID",
    #     "client_secret_env": "SLACK_CLIENT_SECRET",
    #     "scopes": "channels:read chat:write",
    # },
    # GitHub (2026-07-22) -- nécessite une GitHub OAuth App créée
    # manuellement (https://github.com/settings/developers), avec comme
    # callback URL la valeur de URL_RETOUR_APP. Scope "repo" pour lire
    # aussi les dépôts privés (lecture seule côté usage -- le scope
    # GitHub "repo" est plus large que la lecture seule à proprement
    # parler, GitHub n'a pas de scope "repo:read" séparé pour les dépôts
    # privés classiques).
    #
    # PIÈGE CONNU (à surveiller, pas corrigé ici) : une GitHub OAuth App
    # SANS l'option "Enable token expiration" activée dans ses paramètres
    # émet des tokens qui n'expirent JAMAIS, sans `expires_in` ni
    # `refresh_token` dans la réponse. `finaliser_connexion` ci-dessus
    # suppose alors `expires_in=3600` (valeur par défaut du .get()) --
    # la connexion GitHub semblerait donc "expirée" au bout d'1h alors
    # que le token réel reste valide indéfiniment, et `_rafraichir`
    # échouerait (pas de refresh_token stocké), forçant une reconnexion
    # inutile. Pour un comportement cohérent avec le reste de ce système
    # (tokens courts + refresh), activer "Enable token expiration" dans
    # les paramètres de l'app GitHub -- sinon prévoir ce comportement.
    "github": {
        "authorization_endpoint": "https://github.com/login/oauth/authorize",
        "token_endpoint": "https://github.com/login/oauth/access_token",
        "client_id_env": "GITHUB_CLIENT_ID",
        "client_secret_env": "GITHUB_CLIENT_SECRET",
        "scopes": "repo",
        "token_headers": {"Accept": "application/json"},
    },
}


def _generer_pkce():
    code_verifier = "".join(
        secrets.choice(string.ascii_letters + string.digits + "-._~") for _ in range(128)
    )
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return code_verifier, code_challenge


def demarrer_connexion(service, user_id, agent_id):
    """
    Première étape : génère l'URL d'autorisation à ouvrir pour
    l'utilisateur. Retourne None si le service est inconnu ou mal
    configuré (client_id/secret absents de Railway).
    """
    config = SERVICES.get(service)
    if not config:
        logging.error(f"Connexion impossible : service '{service}' inconnu dans SERVICES.")
        return None

    client_id = get_secret(config["client_id_env"])
    if not client_id or not URL_RETOUR:
        logging.error(f"Connexion {service} impossible : client_id ou URL_RETOUR_APP manquant.")
        return None

    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = _generer_pkce()

    try:
        supabase.table("oauth_temp").insert({
            "state": state,
            "service": service,
            "user_id": user_id,
            "agent_id": agent_id,
            "code_verifier": code_verifier,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE oauth_temp ({service}) : {e}")
        return None

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": URL_RETOUR,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    if config.get("scopes"):
        params["scope"] = config["scopes"]

    return f"{config['authorization_endpoint']}?{urlencode(params)}"


def etat_en_attente(state):
    """
    Vérifie si un `state` correspond à une tentative de connexion en
    cours, et renvoie le service concerné (ou None). Utile pour
    distinguer un retour de CE système d'un retour Notion/Google sur la
    même URL de callback.
    """
    ligne = supabase.table("oauth_temp").select("service").eq("state", state).execute()
    return ligne.data[0]["service"] if ligne.data else None


def finaliser_connexion(service, code, state):
    """
    Deuxième étape, appelée au retour du fournisseur. Retourne
    (succes: bool, message: str).
    """
    config = SERVICES.get(service)
    if not config:
        return False, f"Service '{service}' inconnu."

    ligne = supabase.table("oauth_temp").select("*").eq("state", state).execute()
    if not ligne.data:
        return False, "Session de connexion expirée ou déjà utilisée."

    tentative = ligne.data[0]
    user_id = tentative["user_id"]
    code_verifier = tentative["code_verifier"]
    supabase.table("oauth_temp").delete().eq("state", state).execute()

    client_id = get_secret(config["client_id_env"])
    client_secret = get_secret(config.get("client_secret_env", ""))

    try:
        corps = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": URL_RETOUR,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if client_secret:
            corps["client_secret"] = client_secret

        reponse = httpx.post(
            config["token_endpoint"], data=corps, timeout=10, headers=config.get("token_headers")
        )
        reponse.raise_for_status()
        tokens = reponse.json()
    except Exception as e:
        logging.error(f"ERREUR ECHANGE CODE {service.upper()} : {e}")
        return False, f"Connexion {service} impossible (code invalide ou expiré)."

    expires_at = _calculer_expiration(tokens)

    try:
        supabase.table("connexions_oauth").upsert({
            "user_id": user_id,
            "service": service,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": expires_at.isoformat(),
            "meta": {k: v for k, v in tokens.items() if k not in ("access_token", "refresh_token", "expires_in")},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id,service").execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE connexions_oauth ({service}) : {e}")
        return False, f"Connexion {service} impossible (erreur interne au stockage)."

    return True, f"Connecté à {service}."


def _rafraichir(service, config, connexion):
    if not connexion.get("refresh_token"):
        return None

    client_id = get_secret(config["client_id_env"])
    client_secret = get_secret(config.get("client_secret_env", ""))

    try:
        corps = {
            "grant_type": "refresh_token",
            "refresh_token": connexion["refresh_token"],
            "client_id": client_id,
        }
        if client_secret:
            corps["client_secret"] = client_secret

        reponse = httpx.post(
            config["token_endpoint"], data=corps, timeout=10, headers=config.get("token_headers")
        )
        reponse.raise_for_status()
        tokens = reponse.json()
    except Exception as e:
        logging.warning(f"Rafraîchissement {service} échoué pour user {connexion['user_id']} : {e}")
        return None

    expires_at = _calculer_expiration(tokens)

    supabase.table("connexions_oauth").update({
        "access_token": tokens["access_token"],
        # Beaucoup de fournisseurs ne renvoient PAS de nouveau
        # refresh_token à chaque rafraîchissement (contrairement à
        # Notion) -> on garde l'ancien si absent, plutôt que de le
        # perdre.
        "refresh_token": tokens.get("refresh_token", connexion["refresh_token"]),
        "expires_at": expires_at.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", connexion["user_id"]).eq("service", service).execute()

    return tokens["access_token"]


def obtenir_token_valide(service, user_id):
    """
    Retourne un access_token utilisable pour ce service et cet
    utilisateur, en le rafraîchissant si besoin. Retourne None si
    jamais connecté, service inconnu, ou connexion morte.
    """
    config = SERVICES.get(service)
    if not config or not user_id:
        return None

    ligne = (
        supabase.table("connexions_oauth")
        .select("*")
        .eq("user_id", user_id)
        .eq("service", service)
        .execute()
    )
    if not ligne.data:
        return None

    connexion = ligne.data[0]
    expire_le = datetime.fromisoformat(connexion["expires_at"])
    if datetime.now(timezone.utc) + MARGE_RAFRAICHISSEMENT < expire_le:
        return connexion["access_token"]

    return _rafraichir(service, config, connexion)


def est_connecte(service, user_id):
    if not user_id:
        return False
    ligne = (
        supabase.table("connexions_oauth")
        .select("user_id")
        .eq("user_id", user_id)
        .eq("service", service)
        .execute()
    )
    return bool(ligne.data)
