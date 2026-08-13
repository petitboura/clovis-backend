"""
Charge et met en cache le system prompt central depuis Notion.
Rechargement automatique toutes les 5 minutes.

Généralisé multi-agents : le NOTION_PAGE_ID n'est plus une variable globale
codée en dur. Chaque agent (ex: "tutorat-maths", "telecom-ia") a sa propre
page Notion, référencée dans la colonne `notion_page_id` de la table
`agents` de Supabase. Le secret AGENT_ID (un par déploiement Streamlit
Cloud) détermine quel agent est actif dans ce process.
"""

import os
import time
import logging
from supabase import create_client
import requests


def get_secret(key):
    return os.environ.get(key)


NOTION_TOKEN = get_secret("NOTION_TOKEN")

# ÉTAPE 1 (généralisation multi-agent) : depuis que l'API
# résout AGENT_ID dynamiquement (auth utilisateur en priorité, secret en
# repli) et le transmet explicitement à chaque appel de chat()/
# get_system_prompt(), cette variable-ci n'est PLUS la source de vérité
# de l'agent actif. Elle ne sert plus que de filet de sécurité si
# get_system_prompt()/forcer_rechargement() sont appelés sans agent_id
# (ex: script/test lancé isolément). Défaut "tutorat-maths" pour rester
# rétrocompatible avec d'anciens appels sans paramètre.
AGENT_ID = get_secret("AGENT_ID") or "clovis"  # 12/08 : ce depot isole ne sert plus que Clovis

SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)
    return _supabase


# Cache en mémoire, keyé par agent_id (au cas où plusieurs agents seraient
# utilisés dans le même process, ex: tests ou futur usage multi-agent
# dans une seule app).
_cache = {}

CACHE_DUREE = 86400  # 24h (Clovis, 12/08 -- demande Bourama : prompt de base rechargé une fois par jour ou sur forçage explicite via forcer_rechargement(), plus à chaque expiration de 5 min comme avant)
BACKOFF_ECHEC = 30  # secondes : pause avant de retenter après un échec de chargement


def _cache_expire(agent_id):
    entree = _cache.get(agent_id)
    if entree is None:
        return True

    duree = CACHE_DUREE if entree["succes"] else BACKOFF_ECHEC
    return time.time() - entree["timestamp"] > duree


def _recuperer_notion_page_id(agent_id):
    """
    Clovis (12/08) : source unique, Notion uniquement -- plus de repli sur
    la colonne agents.system_prompt, plus de bloc créateur (pas de notion
    de "créateur tiers" pour un produit à un seul agent indépendant).
    """
    try:
        resultat = (
            _get_supabase()
            .table("agents")
            .select("notion_page_id")
            .eq("id", agent_id)
            .single()
            .execute()
        )
        return (resultat.data or {}).get("notion_page_id")
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (récupération notion_page_id pour agent_id={agent_id}) : {e}")
        return None


def _echec(agent_id, garder_ancien_prompt=True):
    """
    Enregistre un échec de chargement dans le cache, avec le timestamp
    courant, pour déclencher le backoff (au lieu de rien écrire, ce qui
    ferait retenter Notion/Supabase à CHAQUE message tant que la panne dure).

    Si un prompt valide existait déjà (chargé avec succès avant la panne),
    on le garde tel quel dans "prompt" -> les étudiants continuent d'avoir
    un system prompt fonctionnel pendant la panne, juste pas rafraîchi.
    """
    ancien = _cache.get(agent_id)
    prompt_a_garder = ancien["prompt"] if (garder_ancien_prompt and ancien) else None
    _cache[agent_id] = {"prompt": prompt_a_garder, "timestamp": time.time(), "succes": False}


def _charger_depuis_notion(agent_id):
    """
    Clovis (12/08) : source unique, notion_page_id. Un seul bloc déjà
    entièrement construit dans la page Notion (comportement, outils
    disponibles, formats enrichis, arbitrage calcul, contexte invisible --
    tout écrit une fois pour toutes) -- plus aucun assemblage ici, juste
    un chargement + mise en cache.
    """
    notion_page_id = _recuperer_notion_page_id(agent_id)

    if not NOTION_TOKEN or not notion_page_id:
        logging.error(
            f"NOTION_TOKEN ou notion_page_id manquant pour agent_id={agent_id} "
            "(vérifie la table `agents`, colonne notion_page_id)."
        )
        _echec(agent_id)
        return

    url = f"https://api.notion.com/v1/blocks/{notion_page_id}/children?page_size=100"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        logging.error(f"ERREUR NOTION (requête impossible) : {e}")
        _echec(agent_id)
        return

    if response.status_code != 200:
        # Cas classique : token invalide (401) ou page non partagée avec l'intégration (404)
        logging.error(
            f"ERREUR NOTION {response.status_code} : {response.text[:500]}"
        )
        _echec(agent_id)
        return

    data = response.json()
    blocks = data.get("results", [])

    texte = ""
    for block in blocks:
        type_block = block.get("type")
        if type_block in ["paragraph", "bulleted_list_item", "numbered_list_item",
                           "heading_1", "heading_2", "heading_3"]:
            rich_text = block.get(type_block, {}).get("rich_text", [])
            for t in rich_text:
                texte += t.get("plain_text", "") + "\n"

    texte = texte.strip()
    if not texte:
        logging.warning(
            f"Le prompt Notion récupéré pour agent_id={agent_id} est VIDE. La page existe et "
            "répond (200 OK) mais ne contient aucun bloc paragraph/liste/heading exploitable "
            "(peut-être du contenu dans des toggles, colonnes, ou sous-pages non gérées ici)."
        )

    _cache[agent_id] = {"prompt": texte, "timestamp": time.time(), "succes": True}


def get_system_prompt(agent_id=None):
    agent_id = agent_id or AGENT_ID
    if _cache_expire(agent_id):
        _charger_depuis_notion(agent_id)
    entree = _cache.get(agent_id)
    return entree["prompt"] if entree else None


def forcer_rechargement(agent_id=None):
    agent_id = agent_id or AGENT_ID
    _cache.pop(agent_id, None)
    return get_system_prompt(agent_id)

