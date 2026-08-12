"""
Connexion Notion par utilisateur (OAuth 2.1 + PKCE + Dynamic Client Registration).

POURQUOI CE FICHIER EST DIFFERENT DE auth.py (Google) :
Google a un client OAuth fixe (cree une fois pour toutes sur la console
Google). Notion, elle, ne permet pas de pre-enregistrer une app : c'est le
client (nous) qui s'enregistre lui-meme aupres de Notion au premier besoin
(RFC 7591, "Dynamic Client Registration"). Cet enregistrement peut expirer
un jour cote Notion -> on garde tout l'historique des clients enregistres
dans la table notion_oauth_clients plutot que d'ecraser le dernier, pour
pouvoir continuer a rafraichir les tokens deja emis avec un ancien client_id
meme si on doit en enregistrer un nouveau pour les futures connexions.

FLOW (identique dans l'esprit a demarrer_connexion_google /
finaliser_connexion_google) :
1. demarrer_connexion_notion(user_id, agent_id) -> URL a ouvrir. Range
   code_verifier + state + agent_id dans notion_oauth_temp (l'utilisateur
   quitte l'app, la session Streamlit redemarre a zero au retour).
   agent_id sert uniquement a savoir vers quel agent rediriger apres coup
   (notion_oauth_temp n'a pas ete modifie par le pivot), pas a scoper
   l'acces obtenu.
2. finaliser_connexion_notion(code, state) -> echange le code, stocke les
   tokens dans connexions_notion, scopes par user_id seul.
3. obtenir_token_valide(user_id) -> access_token pret a l'emploi pour ce
   compte, valable pour n'importe quel agent de la plateforme, rafraichi
   automatiquement si proche de l'expiration (appele a chaque message
   dans registre_outils.py, pas seulement a la connexion).

COMPTE UNIFIE (revirement du 2026-07-11) : une
connexion Notion vaut pour TOUS les agents de la plateforme, une fois
etablie par un compte. Ce fichier scopait avant par (user_id, agent_id)
("Option A") ; c'est desormais scope par user_id seul sur
connexions_notion, coherent avec la lecture "ce sont les donnees du
visiteur qui le suivent partout" plutot que "l'agent porte sa propre
connexion".

Notion emet des access_token valables ~1h et des refresh_token valables
jusqu'a 180 jours (ou 30 jours d'inactivite, selon ce qui arrive en
premier). Le refresh_token est TOURNANT : chaque rafraichissement en
renvoie un nouveau et invalide l'ancien -> on ecrase systematiquement.
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

MCP_NOTION_URL = "https://mcp.notion.com/mcp"
DECOUVERTE_URL = "https://mcp.notion.com/.well-known/oauth-authorization-server"

# Marge de securite : on rafraichit un peu avant l'expiration reelle plutot
# que d'attendre un 401, comme recommande par Notion.
MARGE_RAFRAICHISSEMENT = timedelta(minutes=5)


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
URL_RETOUR = get_secret("URL_RETOUR_APP")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def _generer_pkce():
    code_verifier = "".join(
        secrets.choice(string.ascii_letters + string.digits + "-._~") for _ in range(128)
    )
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return code_verifier, code_challenge


def _decouvrir_metadata():
    """
    Recupere les endpoints OAuth de Notion (authorization_endpoint,
    token_endpoint, registration_endpoint...). On ne les hardcode pas :
    Notion peut les faire evoluer, la decouverte est la partie du protocole
    prevue pour ca (RFC 8414).
    """
    reponse = httpx.get(DECOUVERTE_URL, timeout=10)
    reponse.raise_for_status()
    return reponse.json()


def _client_dcr_actif(metadata):
    """
    Retourne (client_ref, client_id, client_secret) pour le client DCR le
    plus recent en base. En enregistre un nouveau aupres de Notion si aucun
    n'existe encore.
    """
    ligne = (
        supabase.table("notion_oauth_clients")
        .select("*")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if ligne.data:
        c = ligne.data[0]
        return c["id"], c["client_id"], c.get("client_secret")

    if not metadata.get("registration_endpoint"):
        raise RuntimeError("Notion ne propose pas de registration_endpoint : DCR impossible.")

    reponse = httpx.post(
        metadata["registration_endpoint"],
        json={
            "client_name": "Djiguigne",
            "redirect_uris": [URL_RETOUR],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        timeout=10,
    )
    reponse.raise_for_status()
    creds = reponse.json()

    insertion = (
        supabase.table("notion_oauth_clients")
        .insert({
            "client_id": creds["client_id"],
            "client_secret": creds.get("client_secret"),
            "redirect_uri": URL_RETOUR,
        })
        .execute()
    )
    ligne_creee = insertion.data[0]
    return ligne_creee["id"], creds["client_id"], creds.get("client_secret")


def demarrer_connexion_notion(user_id, agent_id):
    """
    Premiere etape : genere l'URL d'autorisation Notion a ouvrir pour
    l'utilisateur. Retourne None si la config manque.

    agent_id est enregistre avec la tentative dans notion_oauth_temp
    uniquement pour savoir vers quel agent rediriger l'utilisateur une fois
    revenu (voir chat.py) -> le token obtenu, lui, vaut pour tous les
    agents de la plateforme (compte unifie, voir finaliser_connexion_notion).
    """
    if not URL_RETOUR:
        logging.error("Connexion Notion impossible : URL_RETOUR_APP manquant.")
        return None

    try:
        metadata = _decouvrir_metadata()
        client_ref, client_id, _ = _client_dcr_actif(metadata)
    except Exception as e:
        logging.error(f"ERREUR DECOUVERTE/DCR NOTION : {e}")
        return None

    state = secrets.token_urlsafe(24)
    code_verifier, code_challenge = _generer_pkce()

    try:
        supabase.table("notion_oauth_temp").insert({
            "state": state,
            "user_id": user_id,
            "agent_id": agent_id,
            "code_verifier": code_verifier,
            "client_ref": client_ref,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE notion_oauth_temp : {e}")
        return None

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": URL_RETOUR,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{metadata['authorization_endpoint']}?{urlencode(params)}"


def etat_notion_en_attente(state):
    """
    Verifie si un `state` correspond a une tentative de connexion Notion en
    cours (utile pour distinguer un retour Notion
    d'un retour Google sur la meme URL de callback).
    """
    ligne = supabase.table("notion_oauth_temp").select("state").eq("state", state).execute()
    return bool(ligne.data)


def finaliser_connexion_notion(code, state):
    """
    Deuxieme etape, appelee au retour de Notion. Retrouve le code_verifier
    et le client DCR utilise via `state`, echange le code contre des
    tokens, les stocke dans connexions_notion. Retourne (succes, message).
    """
    ligne = (
        supabase.table("notion_oauth_temp")
        .select("*")
        .eq("state", state)
        .execute()
    )
    if not ligne.data:
        return False, "Session de connexion Notion expiree ou deja utilisee."

    tentative = ligne.data[0]
    user_id = tentative["user_id"]
    # agent_id de la tentative n'est plus utilise ici : il ne servait qu'a
    # renseigner notion_oauth_temp pour la redirection (voir chat.py), la
    # connexion Notion elle-meme est desormais scopee par user_id seul.
    code_verifier = tentative["code_verifier"]
    client_ref = tentative["client_ref"]

    supabase.table("notion_oauth_temp").delete().eq("state", state).execute()

    client_ligne = supabase.table("notion_oauth_clients").select("*").eq("id", client_ref).execute()
    if not client_ligne.data:
        return False, "Client Notion introuvable (erreur interne)."
    client_id = client_ligne.data[0]["client_id"]
    client_secret = client_ligne.data[0].get("client_secret")

    try:
        metadata = _decouvrir_metadata()
        corps = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": URL_RETOUR,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if client_secret:
            corps["client_secret"] = client_secret

        reponse = httpx.post(metadata["token_endpoint"], data=corps, timeout=10)
        reponse.raise_for_status()
        tokens = reponse.json()
    except Exception as e:
        logging.error(f"ERREUR ECHANGE CODE NOTION : {e}")
        return False, "Connexion Notion impossible (code invalide ou expire)."

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))

    try:
        supabase.table("connexions_notion").upsert({
            "user_id": user_id,
            "client_ref": client_ref,
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "expires_at": expires_at.isoformat(),
            "workspace_nom": tokens.get("workspace_name"),
            "workspace_id": tokens.get("workspace_id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id").execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE connexions_notion : {e}")
        return False, "Connexion Notion impossible (erreur interne au stockage)."

    return True, tokens.get("workspace_name") or "ton espace Notion"


def _rafraichir(connexion):
    """
    Rafraichit un token expire ou proche de l'expiration. Le refresh_token
    de Notion est tournant (rotation) : chaque appel en renvoie un nouveau
    qu'il faut imperativement sauvegarder, l'ancien devenant invalide.
    """
    client_ligne = (
        supabase.table("notion_oauth_clients")
        .select("*")
        .eq("id", connexion["client_ref"])
        .execute()
    )
    if not client_ligne.data:
        return None
    client_id = client_ligne.data[0]["client_id"]
    client_secret = client_ligne.data[0].get("client_secret")

    try:
        metadata = _decouvrir_metadata()
        corps = {
            "grant_type": "refresh_token",
            "refresh_token": connexion["refresh_token"],
            "client_id": client_id,
        }
        if client_secret:
            corps["client_secret"] = client_secret

        reponse = httpx.post(metadata["token_endpoint"], data=corps, timeout=10)
        reponse.raise_for_status()
        tokens = reponse.json()
    except Exception as e:
        logging.warning(f"Rafraichissement Notion echoue pour user {connexion['user_id']} : {e}")
        # invalid_grant ou similaire : la connexion est morte, l'utilisateur
        # devra se reconnecter. On ne supprime pas la ligne automatiquement
        # pour garder une trace ; obtenir_token_valide renverra juste None.
        return None

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens.get("expires_in", 3600))

    supabase.table("connexions_notion").update({
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", connexion["refresh_token"]),
        "expires_at": expires_at.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", connexion["user_id"]).execute()

    return tokens["access_token"]


def obtenir_token_valide(user_id):
    """
    Retourne un access_token Notion utilisable pour ce compte, valable
    pour n'importe quel agent de la plateforme (compte unifie), en le
    rafraichissant si besoin. Retourne None si l'utilisateur n'a jamais
    connecte son Notion, ou si la connexion est morte.
    Appelee a chaque message (voir registre_outils.py), pas seulement a la
    connexion, pour ne jamais utiliser un token perime.
    """
    if not user_id:
        return None

    ligne = (
        supabase.table("connexions_notion")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )
    if not ligne.data:
        return None

    connexion = ligne.data[0]
    expire_le = datetime.fromisoformat(connexion["expires_at"])
    if datetime.now(timezone.utc) + MARGE_RAFRAICHISSEMENT < expire_le:
        return connexion["access_token"]

    return _rafraichir(connexion)


def est_connecte(user_id):
    if not user_id:
        return False
    ligne = (
        supabase.table("connexions_notion")
        .select("user_id")
        .eq("user_id", user_id)
        .execute()
    )
    return bool(ligne.data)
