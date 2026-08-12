"""
Recherche vectorielle parallèle dans les tables Supabase, scopée par agent :
prompts_chunks (via recherche_prompts), documents (via recherche_documents).

recherche_outils a été retiré : la table outils_chunks a été supprimée, le
rôle qu'elle visait (savoir quels outils sont pertinents) est déjà assuré
par le mécanisme MCP (le modèle voit la liste des outils et choisit lui-même
via tool calling, voir mcp_tools.py) — un second système de retrieval pour
ça était redondant.

Chaque RPC est appelée avec p_agent_id pour ne remonter que les chunks de
l'agent courant (ex: "tutorat-maths"), afin qu'un futur second agent
(ex: "telecom-ia") ne voie jamais les chunks d'un autre — évite le mélange
RAG entre agents décrit dans la page "Différenciation entre deux IA du
même domaine".
"""

import os
import logging
from concurrent.futures import ThreadPoolExecutor
from supabase import create_client
from embeddings import vectoriser

logging.basicConfig(level=logging.INFO)

# Valeur de repli uniquement : main.py transmet déjà systématiquement un
# agent_id explicite à chercher_candidats() depuis la généralisation
# multi-agent. Ce défaut ne sert que si ce module est appelé isolément
# (ex: script/test), pas dans le chemin normal de chat().
AGENT_ID_PAR_DEFAUT = "tutorat-maths"


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error("SUPABASE_URL ou SUPABASE_SECRET manquant : la recherche RAG sera toujours vide.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def chercher_candidats(question, agent_id=AGENT_ID_PAR_DEFAUT):
    if not agent_id:
        # Un agent_id vide/None filtrerait la RPC sur p_agent_id = NULL et
        # renverrait silencieusement 0 résultat partout : on préfère
        # échouer bruyamment (log clair) plutôt que de laisser le RAG
        # revenir vide sans qu'on comprenne pourquoi.
        logging.error("chercher_candidats appelé sans agent_id : RAG désactivé pour cet appel.")
        return {"prompts": [], "documents": []}

    try:
        vecteur = vectoriser(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        logging.error(f"ERREUR VECTORISATION (Gemini) : {e}")
        return {"prompts": [], "documents": []}

    def get_prompts():
        try:
            return supabase.rpc(
                "recherche_prompts",
                {"query_embedding": vecteur, "match_count": 3, "p_agent_id": agent_id},
            ).execute().data
        except Exception as e:
            logging.error(f"ERREUR SUPABASE RPC recherche_prompts (agent_id={agent_id}) : {e}")
            return []

    def get_documents():
        try:
            return supabase.rpc(
                "recherche_documents",
                {"query_embedding": vecteur, "match_count": 3, "p_agent_id": agent_id},
            ).execute().data
        except Exception as e:
            logging.error(f"ERREUR SUPABASE RPC recherche_documents (agent_id={agent_id}) : {e}")
            return []

    with ThreadPoolExecutor() as executor:
        f_prompts = executor.submit(get_prompts)
        f_documents = executor.submit(get_documents)

    resultat = {
        "prompts": f_prompts.result() or [],
        "documents": f_documents.result() or [],
    }
    logging.info(
        f"RAG (agent_id={agent_id}) -> prompts:{len(resultat['prompts'])} "
        f"documents:{len(resultat['documents'])}"
    )
    return resultat
