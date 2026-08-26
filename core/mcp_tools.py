"""
Moteur MCP generique.

Ce fichier ne contient plus la liste des outils : elle vit dans
registre_outils.py (SERVEURS_MCP). Pour ajouter un nouvel outil, va
modifier ce fichier-la, pas celui-ci.

Comment ca marche : chaque serveur MCP sait decrire lui-meme les outils
qu'il expose (list_tools). Ce fichier se contente de demander cette liste
a chaque serveur configure dans le registre ET autorise pour l'agent
courant (agents_serveurs pour les categories 2/3, agents_outils_generation
pour la categorie 1 -- granularite fine), de la transformer au format que
Groq comprend, et de savoir rappeler le bon serveur (avec la bonne URL et
les bons headers) quand Groq demande a executer un outil.

Systeme de droits, 5 categories (voir migration_droits_agents.sql) :
1. generation (interne) -- allow-list PAR OUTIL, table agents_outils_generation
2. serveur externe global sans connexion (wolfram, github...) -- allow-list PAR SERVEUR, table agents_serveurs
3. compte utilisateur final (notion...) -- allow-list PAR SERVEUR + connexion user, table agents_serveurs
4. compte du createur, scope a un agent -- table agents_connexions_createur
5. compte plateforme, partage par tous -- table plateforme_connexions (invisible cote createur/user)

Dans tous les cas : intersection avec registre_outils_plateforme.disponible
a CHAQUE lecture, jamais une copie figee -- un outil retire cote
plateforme disparait automatiquement de tous les agents qui l'avaient
coche, sans rien modifier cote agent.
"""

import os
import time
import asyncio
import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, create_mcp_http_client
from supabase import create_client

from registre_outils import SERVEURS_MCP

logging.basicConfig(level=logging.INFO)


def _get_secret_local(key):
    return os.environ.get(key)


_supabase = create_client(_get_secret_local("SUPABASE_URL"), _get_secret_local("SUPABASE_SECRET"))


# Clovis (12/08, demande Bourama) : une seule IA -- plus de systeme
# multi-agents/multi-createurs pour les outils (l'ancien filtrage par
# agents_serveurs/agents_outils_generation a ete retire le 14/08, ces
# tables n'ont plus d'usage ici). Tout ce qui est disponible cote
# plateforme (registre_outils_plateforme.disponible=True) est actif pour
# Clovis, sans notion d'agent_id.
#
# `agent_id` reste un parametre des fonctions plus bas (transmis a
# certains url_builder/headers_builder qui en ont besoin, ex:
# _url_generation pour planifier_rappel) -- il ne sert plus a filtrer quoi
# que ce soit ici, uniquement a construire l'URL/les headers.

_DUREE_CACHE_SECONDES = 24 * 60 * 60  # 24h (demande Bourama 14/08 : ne plus
# relire ca a chaque message, ca ralentit l'IA pour rien -- le catalogue
# d'outils ne change pas d'un message a l'autre)

_cache_serveurs_disponibles = {"valeur": None, "expire_a": 0}
_cache_outils_generation_disponibles = {"valeur": None, "expire_a": 0}
_cache_catalogue_outils = {}  # nom_serveur -> {"outils": [...], "expire_a": ts}


def forcer_rechargement_catalogue_outils():
    """
    Vide tout le cache (droits ET catalogue d'outils MCP), pour forcer un
    rechargement complet au prochain message. A appeler manuellement apres
    un changement cote registre_outils_plateforme (nouvel outil active/
    desactive) ou un ajout d'outil MCP, sans attendre les 24h.
    """
    _cache_serveurs_disponibles["valeur"] = None
    _cache_serveurs_disponibles["expire_a"] = 0
    _cache_outils_generation_disponibles["valeur"] = None
    _cache_outils_generation_disponibles["expire_a"] = 0
    _cache_catalogue_outils.clear()


def _serveurs_disponibles():
    """Noms des serveurs actives cote plateforme, cache 24h."""
    maintenant = time.time()
    if _cache_serveurs_disponibles["valeur"] is not None and _cache_serveurs_disponibles["expire_a"] > maintenant:
        return _cache_serveurs_disponibles["valeur"]
    try:
        res = _supabase.table("registre_outils_plateforme").select("nom_serveur").eq("disponible", True).execute()
        valeur = list({ligne["nom_serveur"] for ligne in (res.data or []) if ligne.get("nom_serveur")})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture registre_outils_plateforme, serveurs) : {e}")
        valeur = _cache_serveurs_disponibles["valeur"] or []
    _cache_serveurs_disponibles["valeur"] = valeur
    _cache_serveurs_disponibles["expire_a"] = maintenant + _DUREE_CACHE_SECONDES
    return valeur


def _outils_generation_disponibles():
    """Noms des outils de generation actives cote plateforme, cache 24h."""
    maintenant = time.time()
    if _cache_outils_generation_disponibles["valeur"] is not None and _cache_outils_generation_disponibles["expire_a"] > maintenant:
        return _cache_outils_generation_disponibles["valeur"]
    try:
        res = _supabase.table("registre_outils_plateforme").select("nom_outil").eq("disponible", True).execute()
        valeur = [ligne["nom_outil"] for ligne in (res.data or []) if ligne.get("nom_outil")]
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture registre_outils_plateforme, outils génération) : {e}")
        valeur = _cache_outils_generation_disponibles["valeur"] or []
    _cache_outils_generation_disponibles["valeur"] = valeur
    _cache_outils_generation_disponibles["expire_a"] = maintenant + _DUREE_CACHE_SECONDES
    return valeur


def _lister_outils_serveur_avec_cache(nom, url, headers):
    """
    Catalogue d'un serveur MCP (nom/description/schema de chaque outil,
    via list_tools()) mis en cache 24h, cle par nom de serveur seul (pas
    par utilisateur) -- ce catalogue est le SCHEMA des outils, identique
    pour tout le monde, contrairement aux headers d'authentification qui
    eux varient par utilisateur (ex: Notion). Seul l'appel reseau
    list_tools() est evite par ce cache ; l'appel reel de l'outil
    (appeler_outil, plus bas) n'est jamais mis en cache et repart a
    chaque fois avec les bons headers de l'utilisateur courant.
    """
    maintenant = time.time()
    entree = _cache_catalogue_outils.get(nom)
    if entree is not None and entree["expire_a"] > maintenant:
        return entree["outils"]
    outils = asyncio.run(_lister_outils_async(url, headers))
    _cache_catalogue_outils[nom] = {"outils": outils, "expire_a": maintenant + _DUREE_CACHE_SECONDES}
    return outils


async def _lister_outils_async(url, headers=None):
    # CORRECTION (29/07) : mcp 2.0.0 a renomme streamablehttp_client en
    # streamable_http_client ET retire le parametre headers= direct -- les
    # headers (cle API, token...) passent desormais par un httpx2.AsyncClient
    # preconfigure via create_mcp_http_client (voir doc de la lib). La
    # fonction ne renvoie plus que 2 flux (read, write), plus 3.
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                reponse = await session.list_tools()
                return reponse.tools


async def _appeler_outil_async(url, nom_outil, arguments, headers=None):
    async with create_mcp_http_client(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resultat = await session.call_tool(nom_outil, arguments=arguments)
                for bloc in resultat.content:
                    if hasattr(bloc, "text"):
                        return bloc.text
    return ""


def lister_outils_autorises_pour_agent(get_secret, user_id=None, agent_id=None):
    """
    Se connecte a chaque serveur MCP du registre actif cote plateforme et
    retourne :
    - outils_pour_llm : la liste des outils au format attendu par l'API
      Groq (parametre tools=...)
    - table_routage : un dictionnaire {nom_outil: {"url":..., "headers":...}},
      pour pouvoir rappeler le bon serveur plus tard (avec la bonne
      authentification) sans aucun if/else en dur

    Catalogue BRUT, non filtre par outil_force -- c'est
    lister_tous_les_outils() (juste en dessous) qui applique ce filtre
    pour la reponse normale. Cette fonction existe separement depuis le
    28/07 pour que _router_outils() (core/main.py) puisse juger de la
    pertinence des outils reellement disponibles, sans dupliquer toute la
    logique de connexion aux serveurs MCP.

    Simplifie le 14/08 (demande Bourama) : Clovis est une seule IA, plus
    de systeme multi-agents/multi-createurs -- l'ancien filtrage par
    agents_serveurs/agents_outils_generation (par agent) a ete retire.
    Tous les serveurs et outils de generation disponibles cote plateforme
    (registre_outils_plateforme.disponible=True) sont proposes.

    `user_id` et `agent_id` sont transmis a chaque url_builder/
    headers_builder. La plupart les ignorent (cle API globale, ex:
    Tavily, Wolfram) ; certains outils "par utilisateur" (ex: Notion) en
    ont besoin pour aller chercher le bon token -- voir connexions/notion.py.
    Si un outil necessite un utilisateur et qu'aucun n'est connecte, il
    est ignore silencieusement : il n'apparait simplement pas dans la
    liste proposee au modele.

    Le catalogue d'outils (schema/description via list_tools()) est mis
    en cache 24h par serveur -- voir _lister_outils_serveur_avec_cache --
    au lieu d'un appel reseau a chaque message.
    """
    outils_pour_llm = []
    table_routage = {}

    noms_serveurs_actifs = _serveurs_disponibles()
    serveurs_actifs = [
        s for s in SERVEURS_MCP
        if s["nom"] == "generation" or s["nom"] in noms_serveurs_actifs
    ]

    logging.info(
        f"Serveurs MCP actifs : {noms_serveurs_actifs or '(aucun)'} "
        f"({len(serveurs_actifs)}/{len(SERVEURS_MCP)} du registre retenus)"
    )

    for serveur in serveurs_actifs:
        nom = serveur["nom"]
        try:
            if serveur.get("necessite_utilisateur") and not user_id:
                logging.info(f"MCP '{nom}' ignoré : nécessite un utilisateur connecté, aucun user_id fourni.")
                continue

            url = serveur["url_builder"](get_secret, user_id, agent_id)
            headers = serveur["headers_builder"](get_secret, user_id, agent_id) if "headers_builder" in serveur else None

            if serveur.get("necessite_utilisateur") and headers is None:
                logging.info(f"MCP '{nom}' ignoré : utilisateur {user_id} pas connecté à cet outil.")
                continue

            outils = _lister_outils_serveur_avec_cache(nom, url, headers)

            outils_autorises = serveur.get("outils_autorises")
            if nom == "generation":
                outils_autorises = _outils_generation_disponibles()
                # gerer_document_bibliotheque (2026-08-01, étendu 20/08,
                # consolidé 26/08 -- ex consulter_bibliotheque +
                # consulter_bibliotheque_publique + 10 autres outils) :
                # bibliothèque PERSONNELLE et plugins publics de
                # l'utilisateur (voir "Mon espace"), scopés par user_id
                # côté core/bibliotheque_rag.py, pas par la liste
                # registre_outils_plateforme. Toujours proposé dès qu'un
                # utilisateur est connecté.
                if user_id and "gerer_document_bibliotheque" not in outils_autorises:
                    outils_autorises = [*outils_autorises, "gerer_document_bibliotheque"]
            if outils_autorises is not None:
                outils = [o for o in outils if o.name in outils_autorises]

            noms_outils = [o.name for o in outils]
            logging.info(f"MCP '{nom}' -> {len(outils)} outil(s) listé(s) : {noms_outils}")
            for outil in outils:
                outils_pour_llm.append({
                    "type": "function",
                    "function": {
                        "name": outil.name,
                        "description": outil.description or "",
                        # CORRECTION (29/07) : mcp 2.0.0 a renomme inputSchema en
                        # input_schema sur l'objet Tool retourne par list_tools() --
                        # meme famille de bug que streamable_http_client (voir plus
                        # haut). Sans ce fix, chaque appel MCP plantait
                        # silencieusement (AttributeError attrape par le except plus
                        # bas) et la liste d'outils envoyee au LLM restait vide, quel
                        # que soit l'outil selectionne via le bouton Outils.
                        "parameters": outil.input_schema,
                    },
                })
                table_routage[outil.name] = {"url": url, "headers": headers}
        except Exception as e:
            logging.error(f"ERREUR MCP listing ({nom}): {e}")

    return outils_pour_llm, table_routage


def lister_tous_les_outils(get_secret, user_id=None, agent_id=None, outil_force=None):
    """
    Reprend lister_outils_autorises_pour_agent() (catalogue brut pour cet
    agent) puis applique le filtre "bouton Outils" ci-dessous. Signature
    et comportement inchangés pour tous les appelants existants.
    """
    outils_pour_llm, table_routage = lister_outils_autorises_pour_agent(get_secret, user_id, agent_id)

    # Mode "bouton Outils" (2026-07-25, GLOBAL -- décision définitive de
    # Bourama, initialement testé sur l'agent nucleos seul puis étendu à
    # tous). Objectif : réduire la conso token (schéma de ~20 outils
    # envoyé à chaque message, identifié comme le plus gros poste).
    # AUCUN outil envoyé par défaut, sur AUCUN agent, sauf sélection
    # explicite d'UN outil par le frontend (bouton Outils, voir
    # BarreDeSaisie.tsx). Effet de bord assumé et voulu : l'IA perd son
    # autonomie d'appel d'outil implicite partout (ex. chercher_fichier
    # automatique quand on redemande un fichier envoyé, tavily_search
    # automatique sur une question d'actualité) tant que rien n'est
    # sélectionné.
    # Mode "bouton Outils" (2026-07-25, GLOBAL -- décision définitive de
    # Bourama, initialement testé sur l'agent nucleos seul puis étendu à
    # tous, puis passé à la MULTI-sélection le 26/07). Objectif : réduire
    # la conso token (schéma de ~20 outils envoyé à chaque message,
    # identifié comme le plus gros poste). AUCUN outil envoyé par défaut,
    # sur AUCUN agent, sauf sélection explicite d'un ou plusieurs outils
    # par le frontend (bouton Outils, voir BarreDeSaisie.tsx). Effet de
    # bord assumé et voulu : l'IA perd son autonomie d'appel d'outil
    # implicite partout (ex. chercher_fichier automatique quand on
    # redemande un fichier envoyé, tavily_search automatique sur une
    # question d'actualité) tant que rien n'est sélectionné.
    outils_forces = set(outil_force or [])
    if outils_forces:
        outils_pour_llm = [o for o in outils_pour_llm if o["function"]["name"] in outils_forces]
        table_routage = {k: v for k, v in table_routage.items() if k in outils_forces}
    else:
        outils_pour_llm = []
        table_routage = {}

    logging.info(f"Outils envoyés au LLM ce tour-ci : {[o['function']['name'] for o in outils_pour_llm]}")
    return outils_pour_llm, table_routage


def appeler_outil(nom_outil, arguments, table_routage):
    """
    Execute un outil par son nom, quel que soit le serveur MCP qui
    l'expose. Le routage (URL + headers) se fait automatiquement via
    table_routage, construite par lister_tous_les_outils().
    """
    logging.info(f"Appel outil demandé par le LLM : {nom_outil}({arguments})")
    route = table_routage.get(nom_outil)
    if not route:
        logging.error(f"Outil '{nom_outil}' demandé par le LLM mais absent de la table de routage.")
        return f"Erreur : outil '{nom_outil}' inconnu."
    try:
        resultat = asyncio.run(
            _appeler_outil_async(route["url"], nom_outil, arguments, route.get("headers"))
        )
        logging.info(f"Résultat outil '{nom_outil}' : {len(resultat or '')} caractères")
        return resultat
    except Exception as e:
        logging.error(f"ERREUR MCP appel a {nom_outil}: {e}")
        return f"Erreur lors de l'appel a l'outil '{nom_outil}'."
