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


def _outils_actives_pour_agent(agent_id):
    """
    Retourne la liste des noms de serveurs (ex: ["wolfram", "notion"])
    autorises pour cet agent -- categories 2 et 3 (droit par serveur
    entier), lues depuis agents_serveurs (remplace l'ancienne colonne
    agents.tools_enabled).

    Allow-list stricte, intersectee avec la plateforme (voir
    registre_outils_plateforme.disponible) : un serveur coche par le
    createur mais retire/indisponible cote plateforme n'apparait pas.
    Si la requete echoue, on retourne une liste vide plutot que "tous
    les outils" -> un agent mal configure n'a AUCUN outil.
    """
    if not agent_id:
        logging.error("_outils_actives_pour_agent appelé sans agent_id : aucun outil activé.")
        return []
    try:
        res = (
            _supabase.table("agents_serveurs")
            .select("nom_serveur")
            .eq("agent_id", agent_id)
            .execute()
        )
        noms_coches = [ligne["nom_serveur"] for ligne in (res.data or [])]
        if not noms_coches:
            return []

        dispo_res = (
            _supabase.table("registre_outils_plateforme")
            .select("nom_serveur")
            .in_("nom_serveur", noms_coches)
            .eq("disponible", True)
            .execute()
        )
        return list({ligne["nom_serveur"] for ligne in (dispo_res.data or [])})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents_serveurs, agent_id={agent_id}) : {e}")
        return []


def _outils_generation_actifs_pour_agent(agent_id):
    """
    Categorie 1 (generation) : granularite par outil individuel, pas par
    serveur entier. Meme logique allow-list intersectee avec la
    plateforme (registre_outils_plateforme.disponible) -- un outil
    coche par le createur mais retire cote plateforme (ex: plus de cle
    FAL) n'apparait pas non plus.
    """
    if not agent_id:
        return []
    try:
        coches_res = (
            _supabase.table("agents_outils_generation")
            .select("nom_outil")
            .eq("agent_id", agent_id)
            .execute()
        )
        noms_coches = [ligne["nom_outil"] for ligne in (coches_res.data or [])]
        if not noms_coches:
            return []

        dispo_res = (
            _supabase.table("registre_outils_plateforme")
            .select("nom_outil")
            .in_("nom_outil", noms_coches)
            .eq("disponible", True)
            .execute()
        )
        return [ligne["nom_outil"] for ligne in (dispo_res.data or [])]
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents_outils_generation, agent_id={agent_id}) : {e}")
        return []


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
    Se connecte a chaque serveur MCP du registre AUTORISE POUR CET AGENT
    (voir agents.tools_enabled) et retourne :
    - outils_pour_llm : la liste des outils au format attendu par l'API
      Groq (parametre tools=...)
    - table_routage : un dictionnaire {nom_outil: {"url":..., "headers":...}},
      pour pouvoir rappeler le bon serveur plus tard (avec la bonne
      authentification) sans aucun if/else en dur

    Catalogue BRUT, non filtre par outil_force -- c'est
    lister_tous_les_outils() (juste en dessous) qui applique ce filtre
    pour la reponse normale. Cette fonction existe separement depuis le
    28/07 pour que _router_outils() (core/main.py) puisse juger de la
    pertinence des outils REELLEMENT autorises pour cet agent, sans
    dupliquer toute la logique de connexion aux serveurs MCP.

    `agent_id` determine quels serveurs de SERVEURS_MCP sont meme
    interroges (filtre AVANT tout appel reseau, categories 2/3) : un
    agent sans rien coche dans agents_serveurs n'a acces a AUCUN outil de
    ces categories, par defaut restrictif (voir _outils_actives_pour_agent).
    Le serveur "generation" (categorie 1) est toujours interroge, mais
    ses outils sont filtres un par un via agents_outils_generation
    (voir _outils_generation_actifs_pour_agent). Ajouter/retirer un
    outil pour un agent = modifier ces tables en base, jamais ce fichier.

    `user_id` et `agent_id` sont transmis a chaque url_builder/
    headers_builder. La plupart les ignorent (cle API globale, ex:
    Tavily, Wolfram) ; certains outils "par utilisateur" (ex: Notion) en
    ont besoin pour aller chercher le bon token. Notion specifiquement
    scope sa connexion par (user_id, agent_id) et non user_id seul
    (Option A, juillet 2026) : un utilisateur connecte a Notion pour un
    agent n'est PAS automatiquement connecte pour un autre -> voir
    connexions/notion.py. Si un outil necessite un utilisateur et qu'aucun
    n'est connecte (ou pas encore connecte a CET outil POUR CET AGENT), il
    est ignore silencieusement : il n'apparait simplement pas dans la
    liste proposee au modele.
    """
    outils_pour_llm = []
    table_routage = {}

    noms_serveurs_actives = _outils_actives_pour_agent(agent_id)
    serveurs_pour_cet_agent = [
        s for s in SERVEURS_MCP
        if s["nom"] == "generation" or s["nom"] in noms_serveurs_actives
    ]
    # "generation" est toujours interroge : sa restriction se fait APRES,
    # outil par outil, via _outils_generation_actifs_pour_agent -- pas au
    # niveau serveur comme wolfram/github/notion (categories 2/3).
    # Si l'agent n'a coche aucun outil de generation, la fonction
    # renverra une liste vide et le filtre plus bas videra la liste
    # d'outils de toute facon (comportement identique a "serveur absent").

    logging.info(
        f"Agent '{agent_id}' -> serveurs MCP activés : {noms_serveurs_actives or '(aucun)'} "
        f"({len(serveurs_pour_cet_agent)}/{len(SERVEURS_MCP)} du registre retenus)"
    )

    for serveur in serveurs_pour_cet_agent:
        nom = serveur["nom"]
        try:
            if serveur.get("necessite_utilisateur") and not user_id:
                logging.info(f"MCP '{nom}' ignoré : nécessite un utilisateur connecté, aucun user_id fourni.")
                continue

            url = serveur["url_builder"](get_secret, user_id, agent_id)
            headers = serveur["headers_builder"](get_secret, user_id, agent_id) if "headers_builder" in serveur else None

            if serveur.get("necessite_utilisateur") and headers is None:
                logging.info(f"MCP '{nom}' ignoré : utilisateur {user_id} pas connecté à cet outil POUR L'AGENT '{agent_id}' (headers=None).")
                continue

            outils = asyncio.run(_lister_outils_async(url, headers))

            outils_autorises = serveur.get("outils_autorises")
            # Categorie 1 (generation) : granularite fine par outil,
            # recalculee par agent a chaque appel (pas une liste fixe
            # ecrite dans registre_outils.py comme pour Notion).
            if nom == "generation":
                outils_autorises = _outils_generation_actifs_pour_agent(agent_id)
                # consulter_bibliotheque (2026-08-01) : bibliothèque
                # PERSONNELLE de l'utilisateur (voir "Mon espace"),
                # n'appartient à aucun agent en particulier -- contrairement
                # au reste de la catégorie 1, elle ne passe donc PAS par
                # l'allow-list agents_outils_generation (le créateur ne la
                # configure pas outil par outil, elle est déjà scopée par
                # user_id côté core/bibliotheque_rag.py). Toujours proposée
                # dès qu'un utilisateur est connecté, sur n'importe quel
                # agent -- cf. demande Bourama "n'importe quelle
                # conversation, n'importe quel chat".
                if user_id and "consulter_bibliotheque" not in outils_autorises:
                    outils_autorises = [*outils_autorises, "consulter_bibliotheque"]
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
