"""
Indexation de contenus (PDF stockés dans Supabase Storage, ou texte brut)
vers la table `documents`, utilisée pour la recherche RAG côté
core/retriever.py.

Généralisé multi-agent (ÉTAPE 4 du plan djiguigne) : chaque appel prend un
`agent_id` explicite, écrit dans `documents.agent_id`, pour que
core/retriever.py (déjà scopé par agent) retrouve bien le bon contenu.
`AGENT_ID_PAR_DEFAUT` n'existe que pour ne pas casser l'usage en ligne de
commande historique (tutorat-maths) :
    python index_documents.py livre-algebre-1.pdf
    python index_documents.py livre-algebre-1.pdf mon-agent-id
"""

import os
import sys
import PyPDF2
from supabase import create_client

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
from embeddings import vectoriser, decouper_texte  # noqa: E402
from storage import BUCKET, supabase as storage_client  # noqa: E402

AGENT_ID_PAR_DEFAUT = "tutorat-maths"


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def extraire_texte_pdf(chemin_pdf):
    texte = ""
    with open(chemin_pdf, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            texte += page.extract_text() + "\n"
    return texte.replace("\x00", "")


def supprimer_chunks_existants(agent_id, nom_fichier):
    """
    Supprime les chunks déjà indexés pour CE nom_fichier et CET agent avant
    réindexation. Nécessaire pour le texte libre (l'ancien formulaire Streamlit et
    l'ancienne interface Streamlit, section "Base de connaissance") qui peut être
    modifié et réenregistré plusieurs fois par le créateur : sans ça,
    chaque sauvegarde empilerait de nouveaux chunks au lieu de remplacer
    les anciens.
    """
    supabase.table("documents").delete().eq("agent_id", agent_id).eq("nom", nom_fichier).execute()


def indexer_texte(agent_id, nom_fichier, texte):
    """
    Découpe + vectorise + insère un texte brut (déjà extrait, ou tapé
    directement par le créateur) dans `documents`, pour un agent donné.
    Remplace toujours l'indexation précédente de ce même nom_fichier pour
    cet agent (voir supprimer_chunks_existants).
    """
    supprimer_chunks_existants(agent_id, nom_fichier)

    morceaux = decouper_texte(texte)
    print(f"Indexation de {len(morceaux)} morceaux pour l'agent '{agent_id}'...")
    for morceau in morceaux:
        embedding = vectoriser(morceau)
        supabase.table("documents").insert({
            "nom": nom_fichier,
            "contenu": morceau,
            "embedding": embedding,
            "agent_id": agent_id,
        }).execute()

    print(f"'{nom_fichier}' indexé avec succès pour l'agent '{agent_id}' !")


def indexer_document(chemin_pdf, nom_fichier, agent_id=AGENT_ID_PAR_DEFAUT):
    print(f"Lecture de {nom_fichier}...")
    texte = extraire_texte_pdf(chemin_pdf)
    indexer_texte(agent_id, nom_fichier, texte)


def indexer_depuis_supabase(nom_fichier, agent_id=AGENT_ID_PAR_DEFAUT):
    print(f"Téléchargement de {nom_fichier} depuis Supabase...")
    response = storage_client.storage.from_(BUCKET).download(nom_fichier)

    chemin_temp = f"temp_{nom_fichier}"
    with open(chemin_temp, "wb") as f:
        f.write(response)

    indexer_document(chemin_temp, nom_fichier, agent_id)

    os.remove(chemin_temp)
    print("Fichier temporaire supprimé.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python index_documents.py <nom_fichier.pdf> [agent_id]")
    else:
        nom_fichier_cli = sys.argv[1]
        agent_id_cli = sys.argv[2] if len(sys.argv) > 2 else AGENT_ID_PAR_DEFAUT
        indexer_depuis_supabase(nom_fichier_cli, agent_id_cli)
