"""
Indexation périodique du contenu Notion (page système prompt de chaque agent
et ses sous-pages) vers la table prompts_chunks dans Supabase.

Ne tourne pas à chaque question d'étudiant — lancé périodiquement (GitHub Action)
ou manuellement pour tester.

Multi-agent : la page à indexer n'est plus un secret global unique, mais lue
depuis agents.notion_page_id pour CHAQUE agent de la table `agents`. Chaque
chunk est tagué avec l'agent_id correspondant, pour rester cohérent avec le
filtrage déjà en place côté retriever.py / configuration.py.

Logique de mise à jour incrémentale :
- Pour chaque page rencontrée, on compare son last_edited_time à ce qui est stocké
- Si identique -> on ne touche à rien (économie d'appels API d'embedding)
- Si différent ou absent -> on supprime les anciens chunks de CETTE page (pour CET
  agent), puis on réindexe
"""

import os
import sys
import logging
import requests
from supabase import create_client

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
from embeddings import vectoriser, decouper_texte  # noqa: E402

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    return os.environ.get(key)


NOTION_TOKEN = get_secret("NOTION_TOKEN")
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28"
}

TABLE = "prompts_chunks"


class ErreurNotion(Exception):
    """
    Levee quand l'API Notion repond une erreur (status != 200) ou est
    injoignable. Distincte d'un simple "page vide" : sans ca, un vrai
    probleme (token expire, 429, reseau) etait avant confondu avec une
    page qui n'a juste pas de contenu, et le script continuait comme si
    de rien n'etait -> panne silencieuse, exactement ce que le critere
    "robustesse d'orchestration" (page Differenciation) demande d'eviter.
    """
    pass


def _requete_notion(methode, url, **kwargs):
    try:
        response = requests.request(methode, url, headers=HEADERS, timeout=15, **kwargs)
    except requests.RequestException as e:
        raise ErreurNotion(f"réseau injoignable sur {url} : {e}")

    if response.status_code != 200:
        raise ErreurNotion(f"HTTP {response.status_code} sur {url} : {response.text[:200]}")

    return response.json()


def get_page_metadata(page_id):
    """Récupère le titre et la date de dernière modification d'une page Notion."""
    data = _requete_notion("GET", f"https://api.notion.com/v1/pages/{page_id}")
    last_edited = data.get("last_edited_time")

    titre = "Sans titre"
    for prop in data.get("properties", {}).values():
        if prop.get("type") == "title":
            morceaux = prop.get("title", [])
            if morceaux:
                titre = morceaux[0].get("plain_text", "Sans titre")

    return titre, last_edited


def get_texte_et_sous_pages(block_id):
    """
    Parcourt les blocs d'une page. Retourne (texte_de_la_page, liste_des_ids_sous_pages).
    Gère les pages classiques ET les bases de données (chaque ligne = une sous-page).
    """
    data = _requete_notion("GET", f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100")
    blocks = data.get("results", [])

    texte = ""
    sous_pages = []

    for block in blocks:
        type_block = block.get("type")

        if type_block in ["paragraph", "bulleted_list_item", "numbered_list_item", "heading_1", "heading_2", "heading_3"]:
            rich_text = block[type_block].get("rich_text", [])
            for t in rich_text:
                texte += t.get("plain_text", "") + "\n"

        elif type_block == "child_page":
            sous_pages.append(block["id"])

        elif type_block == "child_database":
            sous_pages.extend(get_lignes_database(block["id"]))

    return texte.strip(), sous_pages


def get_lignes_database(database_id):
    """Retourne les IDs de chaque ligne (entrée) d'une base de données Notion."""
    data = _requete_notion("POST", f"https://api.notion.com/v1/databases/{database_id}/query", json={"page_size": 100})
    return [r["id"] for r in data.get("results", [])]


def get_last_edited_stocke(page_id, agent_id):
    """Vérifie si cette page est déjà indexée pour cet agent, et avec quelle date."""
    result = (
        supabase.table(TABLE)
        .select("last_edited_time")
        .eq("page_id", page_id)
        .eq("agent_id", agent_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["last_edited_time"]
    return None


def indexer_page(page_id, nom_page, last_edited_time, agent_id):
    """Supprime les anciens chunks de cette page (pour cet agent), découpe + vectorise + insère les nouveaux."""
    supabase.table(TABLE).delete().eq("page_id", page_id).eq("agent_id", agent_id).execute()

    texte, _ = get_texte_et_sous_pages(page_id)
    if not texte:
        print(f"  -> '{nom_page}' est vide, rien à indexer.")
        return

    morceaux = decouper_texte(texte)
    for morceau in morceaux:
        embedding = vectoriser(morceau)
        supabase.table(TABLE).insert({
            "page_id": page_id,
            "nom_page": nom_page,
            "contenu": morceau,
            "embedding": embedding,
            "last_edited_time": last_edited_time,
            "agent_id": agent_id,
        }).execute()

    print(f"  -> '{nom_page}' indexée pour l'agent '{agent_id}' ({len(morceaux)} morceaux).")


def parcourir_et_indexer(page_id, agent_id, profondeur=0, erreurs=None):
    """
    Parcourt récursivement une page et ses sous-pages, à n'importe quelle profondeur.

    `erreurs` (ajouté 2026-08-03, pour l'indexation déclenchée depuis
    api/agents.py à la sauvegarde d'un lien Notion) : liste optionnelle dans
    laquelle chaque erreur Notion rencontrée est ajoutée (en plus du log),
    pour que l'appelant puisse savoir que ça a échoué et donner un message
    clair au créateur. `None` par défaut = comportement inchangé pour le
    cron (indexer.yml) et l'appel manuel en __main__, qui ne lisent que les
    logs.
    """
    prefixe = "  " * profondeur

    try:
        nom_page, last_edited_actuel = get_page_metadata(page_id)
    except ErreurNotion as e:
        # On ne traite JAMAIS une erreur Notion comme "page vide" : on
        # l'annonce clairement et on arrête cette branche, plutôt que de
        # supprimer/réindexer sur la base d'une info fausse.
        logging.error(f"{prefixe}ERREUR NOTION (page {page_id}, agent {agent_id}) : {e}")
        if erreurs is not None:
            erreurs.append(str(e))
        return

    last_edited_stocke = get_last_edited_stocke(page_id, agent_id)

    if last_edited_stocke == last_edited_actuel:
        print(f"{prefixe}'{nom_page}' inchangée, ignorée.")
    else:
        print(f"{prefixe}'{nom_page}' modifiée ou nouvelle, indexation...")
        try:
            indexer_page(page_id, nom_page, last_edited_actuel, agent_id)
        except ErreurNotion as e:
            logging.error(f"{prefixe}ERREUR NOTION (contenu de '{nom_page}', agent {agent_id}) : {e}")
            if erreurs is not None:
                erreurs.append(str(e))
            return

    try:
        _, sous_pages = get_texte_et_sous_pages(page_id)
    except ErreurNotion as e:
        logging.error(f"{prefixe}ERREUR NOTION (sous-pages de '{nom_page}', agent {agent_id}) : {e}")
        if erreurs is not None:
            erreurs.append(str(e))
        return

    for sous_page_id in sous_pages:
        parcourir_et_indexer(sous_page_id, agent_id, profondeur + 1, erreurs)


def lister_agents():
    """Retourne [(agent_id, notion_page_id), ...] pour tous les agents configurés."""
    result = supabase.table("agents").select("id, notion_page_id").execute()
    return [(row["id"], row.get("notion_page_id")) for row in (result.data or [])]


if __name__ == "__main__":
    print("Démarrage de l'indexation Notion -> Supabase (tous les agents)...")
    agents = lister_agents()

    if not agents:
        print("Aucun agent trouvé dans la table 'agents'. Rien à indexer.")

    for agent_id, notion_page_id in agents:
        if not notion_page_id:
            # Un agent mal configuré ne doit pas bloquer l'indexation des
            # autres : on log clairement et on passe au suivant.
            logging.error(f"Agent '{agent_id}' sans notion_page_id configuré, ignoré.")
            continue

        print(f"\n--- Agent '{agent_id}' (page racine {notion_page_id}) ---")
        parcourir_et_indexer(notion_page_id, agent_id)

    print("\nTerminé.")

