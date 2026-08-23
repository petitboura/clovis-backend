"""
Ajoute le 23/08/2026, Bourama : Lot 1 Partie 3 (app mobile), socle.

Canal dedie entre l'app mobile Clovis (Android/iOS, depot
clovis-mobile) et ce backend. Reutilise l'auth Supabase standard deja
en place (voir api/auth.py) : l'app mobile se connecte directement a
Supabase avec le SDK natif, puis envoie son access_token en Bearer sur
ces routes, exactement comme le fait clovis-frontend.

Pour l'instant, deux capacites : synchroniser le temps passe par app
(UsageStatsManager cote Android, Lot 1) et connecteurs tiers via
clovis-backend (Lot 5, Notion en premier). Les autres capacites (lots
2 a 4) viendront sur ce meme routeur ou des routeurs freres.
"""

import logging
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.usage_appareil_mobile import enregistrer_usage, lire_usage
from connexions.notion import (
    demarrer_connexion_notion,
    finaliser_connexion_notion,
    obtenir_token_valide as obtenir_token_notion,
    est_connecte as notion_est_connecte,
    REDIRECT_URI_MOBILE,
)

router = APIRouter(prefix="/api/appareils-mobiles", tags=["appareils-mobiles"])


class EntreeUsage(BaseModel):
    nom_app: str
    date: str  # "AAAA-MM-JJ"
    duree_secondes: int


class SynchronisationUsage(BaseModel):
    plateforme: str  # "android" ou "ios"
    entrees: list[EntreeUsage]


@router.post("/usage", status_code=204)
def synchroniser_usage(payload: SynchronisationUsage, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : appeler a chaque ouverture de l'ecran usage (ou
    en tache de fond periodique plus tard), avec le total du jour par
    app tel que calcule cote telephone. Ecrase la valeur precedente
    pour chaque (app, jour) concerne.
    """
    if payload.plateforme not in ("android", "ios"):
        raise erreur_api(400, "PLATEFORME_INCONNUE")

    try:
        enregistrer_usage(
            utilisateur.id,
            payload.plateforme,
            [entree.model_dump() for entree in payload.entrees],
        )
    except Exception as e:
        logging.error(f"ERREUR synchronisation usage mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_SYNCHRONISATION_USAGE")


@router.get("/usage")
def obtenir_usage(jours: int = 7, utilisateur=Depends(utilisateur_courant)):
    """
    Renvoie l'usage des `jours` derniers jours (7 par defaut) pour
    l'ecran minimal du Lot 1. `jours` reste un parametre, jamais une
    valeur figee cote frontend mobile.
    """
    if jours < 1 or jours > 90:
        raise erreur_api(400, "PLAGE_JOURS_INVALIDE")

    aujourdhui = date.today()
    depuis = (aujourdhui - timedelta(days=jours - 1)).isoformat()
    jusqua = aujourdhui.isoformat()

    try:
        lignes = lire_usage(utilisateur.id, depuis, jusqua)
    except Exception as e:
        logging.error(f"ERREUR lecture usage mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_LECTURE_USAGE")

    return {"usage": lignes}


# --- Lot 5 : connecteurs tiers (Notion en premier) ---
#
# CONTRAT APP MOBILE (voir 05-connecteurs-tiers.md) : l'app n'appelle jamais
# Notion directement, elle passe par ce routeur -- clovis-backend pilote
# l'OAuth et stocke les tokens (connexions_notion), reutilisant exactement
# la meme infra que celle deja utilisee par le chat (core/registre_outils.py).
#
# Flow cote app :
# 1. POST .../connecteurs/notion/demarrer -> ouvrir l'url_autorisation
#    renvoyee dans ASWebAuthenticationSession (iOS, callbackURLScheme=
#    "clovismobile") ou Custom Tabs + intent-filter (Android).
# 2. Recuperer `code` et `state` depuis l'URI de redirection interceptee
#    (clovismobile://oauth-callback?code=...&state=...).
# 3. POST .../connecteurs/notion/finaliser avec {code, state}.
# 4. GET .../connecteurs/notion/statut pour verifier l'etat a tout moment.
# 5. GET .../connecteurs/notion/rechercher?q=... pour un appel effectif de
#    bout en bout (critere de fin du Lot 5) -- utilise directement l'API
#    REST Notion (POST https://api.notion.com/v1/search), pas le MCP (le
#    MCP Notion est concu pour l'agent de chat, pas pour un appel simple
#    depuis le mobile ; l'API REST suffit et evite de reimplementer un
#    client MCP complet cote backend pour ce lot).

NOTION_API_VERSION = "2022-06-28"


class FinalisationNotion(BaseModel):
    code: str
    state: str


@router.post("/connecteurs/notion/demarrer")
def demarrer_notion(utilisateur=Depends(utilisateur_courant)):
    url = demarrer_connexion_notion(utilisateur.id, agent_id=None, redirect_uri=REDIRECT_URI_MOBILE)
    if not url:
        raise erreur_api(500, "NOTION_URL_AUTORISATION_INDISPONIBLE")
    return {"url_autorisation": url}


@router.post("/connecteurs/notion/finaliser")
def finaliser_notion(payload: FinalisationNotion, utilisateur=Depends(utilisateur_courant)):
    succes, message = finaliser_connexion_notion(payload.code, payload.state)
    if not succes:
        raise erreur_api(400, "NOTION_CONNEXION_ECHEC")
    return {"connecte": True, "espace": message}


@router.get("/connecteurs/notion/statut")
def statut_notion(utilisateur=Depends(utilisateur_courant)):
    return {"connecte": notion_est_connecte(utilisateur.id)}


@router.get("/connecteurs/notion/rechercher")
def rechercher_notion(q: str = "", utilisateur=Depends(utilisateur_courant)):
    """
    Preuve de bout en bout du Lot 5 (critere de fin) : recherche dans
    l'espace Notion connecte de l'utilisateur. `q` vide renvoie les
    elements les plus recents (comportement standard de l'API Notion).
    """
    token = obtenir_token_notion(utilisateur.id)
    if not token:
        raise erreur_api(400, "NOTION_NON_CONNECTE")

    try:
        reponse = httpx.post(
            "https://api.notion.com/v1/search",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
            },
            json={"query": q, "page_size": 20},
            timeout=10,
        )
        reponse.raise_for_status()
    except Exception as e:
        logging.error(f"ERREUR recherche Notion mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "NOTION_RECHERCHE_ECHEC")

    resultats = reponse.json().get("results", [])
    return {
        "resultats": [
            {
                "id": r.get("id"),
                "type": r.get("object"),
                "url": r.get("url"),
            }
            for r in resultats
        ]
    }
