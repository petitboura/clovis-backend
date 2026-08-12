"""
Script de diagnostic — teste chaque maillon de la chaîne indépendamment
et affiche clairement où ça casse.

Lancement :
    cd core
    python ../diagnostic.py [agent_id]

(agent_id est optionnel, défaut "tutorat-maths" ; les variables
d'environnement / st.secrets doivent être disponibles, donc lance-le
depuis un environnement où GROQ_API_KEY, NOTION_TOKEN, SUPABASE_URL,
SUPABASE_SECRET, GOOGLE_API_KEY, etc. sont déjà exportées, comme pour
l'app elle-même. NOTION_PAGE_ID n'est plus un secret global : depuis le
passage multi-agents, il vit dans agents.notion_page_id, ce script va le
chercher lui-même pour l'agent_id donné)
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "core"))

OK = "✅"
KO = "❌"
WARN = "⚠️ "

AGENT_ID_PAR_DEFAUT = "tutorat-maths"


def check_env_vars():
    print("\n=== 1. Variables d'environnement ===")
    requis = [
        "NOTION_TOKEN",
        "SUPABASE_URL", "SUPABASE_SECRET",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
    ]
    manquants = []
    for var in requis:
        val = os.environ.get(var)
        if val:
            print(f"{OK} {var} défini ({len(val)} caractères)")
        else:
            print(f"{KO} {var} ABSENT")
            manquants.append(var)
    return manquants


def _recuperer_notion_page_id(agent_id):
    """Va chercher agents.notion_page_id pour cet agent (remplace l'ancien secret NOTION_PAGE_ID global)."""
    try:
        from supabase import create_client
    except Exception as e:
        print(f"{KO} Impossible d'importer supabase pour résoudre notion_page_id : {e}")
        return None

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET")
    if not url or not key:
        print(f"{KO} SUPABASE_URL ou SUPABASE_SECRET absent, impossible de résoudre agents.notion_page_id.")
        return None

    try:
        client = create_client(url, key)
        res = client.table("agents").select("notion_page_id").eq("id", agent_id).maybe_single().execute()
        page_id = (res.data or {}).get("notion_page_id")
        if not page_id:
            print(f"{KO} Agent '{agent_id}' trouvé mais notion_page_id est vide/absent en base.")
            return None
        return page_id
    except Exception as e:
        print(f"{KO} Erreur en lisant agents.notion_page_id pour '{agent_id}' : {e}")
        return None


def check_notion(agent_id):
    print(f"\n=== 2. Notion (system prompt, agent '{agent_id}') ===")
    import requests
    token = os.environ.get("NOTION_TOKEN")
    page_id = _recuperer_notion_page_id(agent_id)
    if not token or not page_id:
        print(f"{KO} NOTION_TOKEN absent ou notion_page_id introuvable, test impossible.")
        return

    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        print(f"{KO} Impossible de contacter l'API Notion : {e}")
        return

    if r.status_code == 401:
        print(f"{KO} 401 Unauthorized -> NOTION_TOKEN invalide ou expiré.")
        return
    if r.status_code == 404:
        print(f"{KO} 404 Not Found -> la page n'existe pas OU (cas le plus fréquent) "
              f"l'intégration Notion n'a PAS été partagée avec cette page. "
              f"Va sur la page Notion -> ... -> Connexions -> ajoute ton intégration.")
        return
    if r.status_code != 200:
        print(f"{KO} Statut HTTP {r.status_code} : {r.text[:300]}")
        return

    blocks = r.json().get("results", [])
    print(f"{OK} 200 OK, {len(blocks)} blocs enfants trouvés.")

    texte = ""
    types_rencontres = {}
    for b in blocks:
        t = b.get("type")
        types_rencontres[t] = types_rencontres.get(t, 0) + 1
        if t in ["paragraph", "bulleted_list_item", "numbered_list_item", "heading_1", "heading_2", "heading_3"]:
            for rt in b.get(t, {}).get("rich_text", []):
                texte += rt.get("plain_text", "")

    print(f"    Types de blocs rencontrés : {types_rencontres}")
    if texte.strip():
        print(f"{OK} Texte extrait : {len(texte)} caractères. Aperçu : {texte[:120]!r}")
    else:
        print(f"{WARN} Aucun texte exploitable extrait. Si tu vois des types comme "
              f"'toggle', 'column_list', 'table' ci-dessus : le code actuel ne les gère pas, "
              f"il faut les ajouter dans configuration.py.")


def check_gemini_embeddings():
    print("\n=== 3. Gemini (embeddings) ===")
    try:
        from embeddings import vectoriser, DIMENSION_EMBEDDING
    except Exception as e:
        print(f"{KO} Impossible d'importer embeddings.py : {e}")
        return None
    try:
        vecteur = vectoriser("test de vectorisation", task_type="RETRIEVAL_QUERY")
        print(f"{OK} Embedding généré, dimension = {len(vecteur)}")
        if len(vecteur) != DIMENSION_EMBEDDING:
            print(f"{WARN} Dimension obtenue ({len(vecteur)}) différente de "
                  f"DIMENSION_EMBEDDING ({DIMENSION_EMBEDDING}) déclarée dans embeddings.py "
                  f"-> vérifie que la colonne vector(N) dans Supabase a bien été migrée "
                  f"vers la même dimension, sinon les RPC vont échouer.")
        return vecteur
    except Exception as e:
        print(f"{KO} ERREUR lors de la vectorisation : {e}")
        print(f"    -> Vérifie GOOGLE_API_KEY, et que le modèle "
              f"'gemini-embedding-001' est bien accessible avec ta clé "
              f"(quota gratuit Google AI Studio, ou clé restreinte à d'autres APIs).")
        return None


def check_supabase(vecteur, agent_id):
    print(f"\n=== 4. Supabase (tables + fonctions RPC, agent '{agent_id}') ===")
    try:
        from supabase import create_client
    except Exception as e:
        print(f"{KO} Impossible d'importer supabase : {e}")
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET")
    if not url or not key:
        print(f"{KO} SUPABASE_URL ou SUPABASE_SECRET absent, test impossible.")
        return

    client = create_client(url, key)

    # outils_chunks a été supprimé (obsolète depuis le passage au principe
    # MCP, voir Plan d'action) : ne plus le tester, sinon faux négatif
    # permanent.
    for table in ["prompts_chunks", "documents"]:
        try:
            res = (
                client.table(table)
                .select("*", count="exact")
                .eq("agent_id", agent_id)
                .limit(1)
                .execute()
            )
            print(f"{OK} Table '{table}' accessible, {res.count} ligne(s) pour l'agent '{agent_id}'.")
            if res.count == 0:
                print(f"{WARN} La table '{table}' est VIDE pour cet agent -> les indexeurs "
                      f"(index_notion.py / index_documents.py) n'ont probablement jamais tourné avec succès pour lui, "
                      f"ou attendent un ré-index suite à la migration d'embedding.")
        except Exception as e:
            print(f"{KO} Erreur sur la table '{table}' : {e}")

    if vecteur is None:
        print(f"{WARN} Pas de vecteur disponible (échec étape 3), on ne peut pas tester les fonctions RPC.")
        return

    # recherche_outils a été supprimé en même temps que outils_chunks. Les
    # deux RPC restantes sont appelées avec p_agent_id, pour tester le
    # VRAI chemin utilisé par retriever.py (l'ancienne surcharge à 2
    # arguments existe encore en parallèle en base, mais la tester ne
    # validerait pas le filtrage par agent qu'on vient de brancher).
    for fn, match_count in [("recherche_prompts", 3), ("recherche_documents", 3)]:
        try:
            res = client.rpc(
                fn, {"query_embedding": vecteur, "match_count": match_count, "p_agent_id": agent_id}
            ).execute()
            n = len(res.data or [])
            print(f"{OK} RPC '{fn}' exécutée (agent_id='{agent_id}'), {n} résultat(s).")
            if n == 0:
                print(f"{WARN} 0 résultat -> soit la table liée est vide pour cet agent, soit le seuil de "
                      f"similarité dans la fonction SQL est trop strict, soit la colonne vector(N) "
                      f"n'a pas encore été migrée à la dimension de embeddings.py.")
        except Exception as e:
            print(f"{KO} Erreur RPC '{fn}' : {e}")
            print(f"    -> Vérifie que la surcharge (query_embedding, match_count, p_agent_id) "
                  f"existe bien dans Supabase (SQL Editor), et que sa colonne vector(N) matche "
                  f"la dimension produite par embeddings.py.")


def main():
    agent_id = sys.argv[1] if len(sys.argv) > 1 else AGENT_ID_PAR_DEFAUT

    print(f"Diagnostic djiguigne-backend (agent_id = '{agent_id}')")
    print("=" * 40)
    manquants = check_env_vars()
    check_notion(agent_id)
    vecteur = check_gemini_embeddings()
    check_supabase(vecteur, agent_id)

    print("\n=== Résumé ===")
    if manquants:
        print(f"{KO} Variables manquantes : {', '.join(manquants)}")
    print("Relis les sections ci-dessus marquées ❌ ou ⚠️  : ce sont les points de blocage réels.")


if __name__ == "__main__":
    main()

