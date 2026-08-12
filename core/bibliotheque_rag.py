"""
Recherche vectorielle (RAG) sur la bibliothèque personnelle d'un
utilisateur (2026-08-01, demande Bourama : "tu uploades des documents et
ton IA les utilise pour te répondre, dans n'importe quelle conversation,
n'importe quel chat").

Distinct de core/retriever.py (RAG scopé par agent_id, table `documents`)
: ici tout est scopé par user_id, table séparée `documents_bibliotheque`
(voir migration `bibliotheque_personnelle_rag`) -- volontairement isolé
du circuit agent existant pour ne rien risquer dessus, et parce que la
bibliothèque personnelle doit être visible depuis N'IMPORTE QUEL agent,
pas un seul (voir core/mcp_tools.py:lister_outils_autorises_pour_agent,
consulter_bibliotheque y est ajouté sans passer par le filtre habituel
agents_outils_generation).

Correction du 01/08 (Bourama : "chaque upload y reste", "à quoi bon le
upload sinon") -- au départ SEUL un PDF ajouté via la page Mon espace
était vectorisé. Un PDF/Word/Excel envoyé en pièce jointe dans N'IMPORTE
QUEL chat (api/uploads.py:uploader_document_chat) était bien stocké
(niveau="utilisateur" depuis le 22/07) mais jamais indexé ici -- donc
invisible pour consulter_bibliotheque. indexer_texte_bibliotheque()
ci-dessous corrige ça : réutilisable partout où un texte est DÉJÀ
extrait (uploader_document_chat extrait déjà le texte du PDF/Word/Excel
pour l'injecter dans le message, plus besoin de le refaire ici).
indexer_pdf_bibliotheque() (utilisée par la page Mon espace, qui reçoit
directement des bytes PDF) n'est plus qu'un raccourci qui extrait le
texte puis appelle indexer_texte_bibliotheque().
"""

import logging
import os

import PyPDF2
from supabase import create_client

from embeddings import vectoriser, decouper_texte

TAILLE_MAX_CHUNKS_PAR_DOCUMENT = 400  # ~garde-fou, un PDF énorme ne doit pas exploser le coût de vectorisation


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def extraire_texte_pdf(chemin_pdf: str) -> str:
    """Même logique que indexers/index_documents.py:extraire_texte_pdf, dupliquée volontairement pour ne pas créer de dépendance croisée entre les deux circuits RAG."""
    texte = ""
    with open(chemin_pdf, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            texte += (page.extract_text() or "") + "\n"
    return texte.replace("\x00", "")


def indexer_texte_bibliotheque(texte: str, fichier_id: str, user_id: str) -> int:
    """
    Découpe + vectorise un texte DÉJÀ EXTRAIT (peu importe la source
    d'origine -- PDF, Word, Excel...) et insère ses chunks dans
    documents_bibliotheque, liés à `fichier_id` (pour le nettoyage en
    cascade à la suppression, voir la migration). Renvoie le nombre de
    chunks indexés.
    """
    morceaux = decouper_texte(texte)[:TAILLE_MAX_CHUNKS_PAR_DOCUMENT]

    lignes = []
    for morceau in morceaux:
        if not morceau.strip():
            continue
        embedding = vectoriser(morceau)
        lignes.append({
            "fichier_id": fichier_id,
            "user_id": user_id,
            "contenu": morceau,
            "embedding": embedding,
        })

    if lignes:
        supabase.table("documents_bibliotheque").insert(lignes).execute()

    logging.info(f"Bibliothèque perso : {len(lignes)} chunk(s) indexé(s) pour fichier_id={fichier_id}")
    return len(lignes)


def indexer_pdf_bibliotheque(chemin_pdf: str, fichier_id: str, user_id: str) -> int:
    """Raccourci pour un PDF reçu en bytes (voir api/bibliotheque_utilisateur.py) : extrait le texte puis délègue à indexer_texte_bibliotheque."""
    return indexer_texte_bibliotheque(extraire_texte_pdf(chemin_pdf), fichier_id, user_id)


def chercher_bibliotheque(question: str, user_id: str, match_count: int = 5) -> list:
    """
    Recherche sémantique dans la bibliothèque personnelle de `user_id`.
    Renvoie une liste de {contenu, similarite}, triée par pertinence.
    """
    if not user_id:
        logging.error("chercher_bibliotheque appelé sans user_id : renvoie vide.")
        return []

    try:
        vecteur = vectoriser(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        logging.error(f"ERREUR VECTORISATION bibliothèque (Gemini) : {e}")
        return []

    try:
        return supabase.rpc(
            "recherche_bibliotheque",
            {"query_embedding": vecteur, "match_count": match_count, "p_user_id": user_id},
        ).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE RPC recherche_bibliotheque (user_id={user_id}) : {e}")
        return []
