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
AGENT_ID = get_secret("AGENT_ID") or "tutorat-maths"

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

CACHE_DUREE = 300  # 5 minutes
BACKOFF_ECHEC = 30  # secondes : pause avant de retenter après un échec de chargement


def _cache_expire(agent_id):
    entree = _cache.get(agent_id)
    if entree is None:
        return True

    duree = CACHE_DUREE if entree["succes"] else BACKOFF_ECHEC
    return time.time() - entree["timestamp"] > duree


def _recuperer_config_prompt(agent_id):
    """
    Récupère notion_page_id ET system_prompt en un seul appel Supabase.

    Deux sources possibles, cette fonction ne choisit pas laquelle utiliser
    (voir _charger_depuis_notion) :
    - notion_page_id : agents historiques gérés à la main par Boumi
      (tutorat-maths, telecom-ia), prompt édité dans une page Notion.
    - system_prompt : agents créés via le formulaire de la plateforme
      (étape 2), saisi directement par le créateur, pas de compte Notion
      nécessaire.

    owner_id (2026-07-15, Bourama : "le nom et la bio du créateur doivent
    être dits à l'agent, dynamiquement") : récupéré ici pour pouvoir
    résoudre le profil du créateur dans _charger_depuis_notion, sans un
    second aller-retour Supabase séparé.
    """
    try:
        resultat = (
            _get_supabase()
            .table("agents")
            .select("notion_page_id, system_prompt, owner_id")
            .eq("id", agent_id)
            .single()
            .execute()
        )
        return resultat.data or {}
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (récupération config prompt pour agent_id={agent_id}) : {e}")
        return {}


def _recuperer_bloc_createur(owner_id):
    """
    Nom affiché + bio du profil créateur (table `profiles`), formatés en
    un bloc de texte à ajouter au system prompt -- pour que l'agent sache
    qui l'a créé et connaisse quelque chose sur cette personne (demande de
    Bourama, 2026-07-15 : "que cela s'applique aussi aux IA existantes").

    Appelé à chaque rechargement de cache (voir CACHE_DUREE, 5 minutes) :
    si le créateur modifie son nom ou sa bio, l'agent le reflète dès le
    prochain rechargement, sans qu'aucune valeur ne soit figée en dur dans
    `agents.system_prompt` lui-même. Chaîne vide (rien à ajouter) si le
    créateur n'a pas encore de ligne `profiles`, ou si nom_affiche et bio
    sont tous les deux vides -- best-effort, ne doit jamais faire échouer
    le chargement du system prompt.
    """
    if not owner_id:
        return ""
    try:
        resultat = (
            _get_supabase()
            .table("profiles")
            .select("nom_affiche, bio")
            .eq("user_id", owner_id)
            .maybe_single()
            .execute()
        )
        profil = resultat.data if resultat else None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (récupération profil créateur owner_id={owner_id}) : {e}")
        return ""

    if not profil:
        return ""

    nom_affiche = (profil.get("nom_affiche") or "").strip()
    bio = (profil.get("bio") or "").strip()
    if not nom_affiche and not bio:
        return ""

    lignes = []
    if nom_affiche:
        lignes.append(f"Tu as été créé par {nom_affiche}.")
    if bio:
        lignes.append(f"Ce que tu sais de cette personne : {bio}")
    return "## Ton créateur\n" + "\n".join(lignes)


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
    Malgré son nom (conservé pour ne pas casser les imports existants dans
    main.py), cette fonction ne charge plus systématiquement depuis Notion :
    elle choisit la source selon ce qui est renseigné pour CET agent.

    Priorité : system_prompt (saisie directe, agents créés via la
    plateforme) d'abord si présent, sinon notion_page_id (agents
    historiques). Un agent créé via le formulaire n'a pas de
    notion_page_id -> on ne tente même pas d'appel Notion pour lui, ce qui
    évite une erreur inutile et un backoff de 30s à chaque rechargement.
    """
    config = _recuperer_config_prompt(agent_id)
    system_prompt = (config.get("system_prompt") or "").strip()
    bloc_createur = _recuperer_bloc_createur(config.get("owner_id"))

    if system_prompt:
        if bloc_createur:
            system_prompt = f"{system_prompt}\n\n{bloc_createur}"
        _cache[agent_id] = {"prompt": system_prompt, "timestamp": time.time(), "succes": True}
        return

    notion_page_id = config.get("notion_page_id")

    if not NOTION_TOKEN or not notion_page_id:
        logging.error(
            f"Ni system_prompt ni (NOTION_TOKEN + notion_page_id) disponibles pour agent_id={agent_id} "
            "(vérifie la table `agents` : au moins l'une des deux sources doit être renseignée)."
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

    if bloc_createur:
        texte = f"{texte}\n\n{bloc_createur}" if texte else bloc_createur

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

