"""
Recherche vectorielle (RAG) sur le catalogue de la bibliothèque
publique (28/08/2026, demande Bourama : "un truc qui permet à l'IA de
trouver un dossier ou fichier dans la bibliothèque publique par RAG...
mais il sert juste à trouver un document, pas à l'utiliser pour
répondre").

Distinct à la fois de core/bibliotheque_rag.py (RAG perso, scopé
user_id, table documents_bibliotheque) ET de la fonction
recherche_bibliotheque_publique existante (RAG des PLUGINS PUBLICS /
contribution_libre, scopée par une liste de fichier_id de
fichiers_uploades -- un système totalement différent, voir
core/bibliotheque_programme.py). Ici on scope par fichier_id de
`bibliotheque_publique` (le catalogue "tout le monde peut y ajouter un
document", voir api/bibliotheque_publique.py) -- volontairement
dupliqué plutôt qu'importé de bibliotheque_rag.py (extraction PDF
comprise), même convention que ce fichier applique déjà lui-même
vis-à-vis de core/retriever.py : pas de dépendance croisée entre les
deux circuits RAG.

Différence de fond avec bibliotheque_rag.chercher_bibliotheque :
cette recherche-ci (chercher_catalogue_public) ne renvoie JAMAIS le
texte du contenu -- seulement de quoi identifier le document (nom,
description, lien). Elle sert à l'IA à LOCALISER un document dans le
catalogue, jamais à citer ou paraphraser son contenu directement dans
une réponse (demande explicite de Bourama : "il sert juste à trouver
un document pas à l'utiliser pour répondre"). Pas de dossiers ici --
chantier séparé, pas encore fait à ce stade.
"""

import logging
import os

from supabase import create_client

from embeddings import vectoriser, decouper_texte

TAILLE_MAX_CHUNKS_PAR_DOCUMENT = 400  # même garde-fou que bibliotheque_rag.py


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def extraire_pages_pdf(chemin_pdf: str) -> list[str]:
    """Identique à bibliotheque_rag.extraire_pages_pdf, dupliquée volontairement (pas de dépendance croisée entre les deux circuits RAG, voir docstring du module)."""
    import PyPDF2
    pages = []
    with open(chemin_pdf, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            pages.append((page.extract_text() or "").replace("\x00", ""))
    return pages


def indexer_texte_catalogue_public(
    texte: str,
    fichier_id: str,
    page_debut: int | None = None,
    page_fin: int | None = None,
    timestamp_debut: float | None = None,
    timestamp_fin: float | None = None,
) -> int:
    """
    Découpe + vectorise un texte déjà extrait et insère ses chunks dans
    documents_catalogue_public, liés à `fichier_id` (une ligne de
    bibliotheque_publique). Pas de user_id ici : contrairement à la
    bibliothèque perso, un document du catalogue public n'appartient à
    personne en particulier pour la recherche (voir `ajoute_par` sur
    bibliotheque_publique pour la seule propriété qui compte : qui a le
    droit de le retirer). Renvoie le nombre de chunks indexés.
    """
    morceaux = decouper_texte(texte)[:TAILLE_MAX_CHUNKS_PAR_DOCUMENT]

    lignes = []
    for morceau in morceaux:
        if not morceau.strip():
            continue
        embedding = vectoriser(morceau)
        lignes.append({
            "fichier_id": fichier_id,
            "contenu": morceau,
            "embedding": embedding,
            "page_debut": page_debut,
            "page_fin": page_fin,
            "timestamp_debut": timestamp_debut,
            "timestamp_fin": timestamp_fin,
        })

    if lignes:
        supabase.table("documents_catalogue_public").insert(lignes).execute()

    logging.info(f"Catalogue public : {len(lignes)} chunk(s) indexé(s) pour fichier_id={fichier_id}")
    return len(lignes)


def indexer_pdf_catalogue_public(chemin_pdf: str, fichier_id: str) -> int:
    """Indexe un PDF du catalogue public, page par page (même logique que bibliotheque_rag.indexer_pdf_bibliotheque)."""
    total = 0
    for numero, texte_page in enumerate(extraire_pages_pdf(chemin_pdf), start=1):
        if not texte_page.strip():
            continue
        total += indexer_texte_catalogue_public(texte_page, fichier_id, page_debut=numero, page_fin=numero)
    return total


def indexer_transcription_catalogue_public(segments: list[dict], fichier_id: str) -> int:
    """Indexe une transcription audio du catalogue public, segment par segment (même logique que bibliotheque_rag.indexer_transcription_bibliotheque)."""
    total = 0
    for segment in segments:
        texte = (segment.get("text") or "").strip()
        if not texte:
            continue
        total += indexer_texte_catalogue_public(
            texte, fichier_id, timestamp_debut=segment.get("start"), timestamp_fin=segment.get("end"),
        )
    return total


def lire_document_catalogue_public(fichier_id: str) -> str | None:
    """
    Reconstruit le texte intégral d'un document du catalogue public
    déjà indexé, en recollant tous ses chunks dans l'ordre d'insertion
    (28/08, demande Bourama : "l'IA doit aussi pouvoir lire le contenu
    intégral si l'utilisateur le demande explicitement" -- à n'appeler
    QUE sur demande explicite, jamais automatiquement depuis
    trouver_catalogue_public). None si aucun chunk indexé pour ce
    fichier_id (vidéo par exemple, ou lien -- pas de contenu textuel).
    """
    try:
        res = (
            supabase.table("documents_catalogue_public")
            .select("contenu")
            .eq("fichier_id", fichier_id)
            .order("id")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture intégrale catalogue public fichier_id={fichier_id}) : {e}")
        return None

    if not res.data:
        return None
    return "\n\n".join(ligne["contenu"] for ligne in res.data)


def lister_catalogue_public(limite: int = 15) -> dict:
    """
    Liste les documents les PLUS RÉCENTS du catalogue public (table
    bibliotheque_publique, statut "publie"), sans recherche par contenu
    -- pour une demande vague ("qu'est-ce qu'il y a dans la bibliothèque
    publique ?") où chercher_catalogue_public (recherche sémantique) ne
    renvoie rien faute de sujet précis à matcher (28/08, bug remonté par
    Bourama : le modèle inventait une requête générique du style
    "contenu bibliothèque publique", qui ne matchait jamais rien).

    Contrairement à l'action "lister" de gerer_document_bibliotheque
    (bibliothèque PERSONNELLE, forcément bornée à un seul utilisateur),
    N'IMPORTE QUI peut ajouter un document au catalogue public : jamais
    de liste exhaustive ici (demande explicite de Bourama : "on sait
    jamais il peut y avoir énormément"), toujours plafonnée à `limite`
    entrées, les plus récentes en premier. Renvoie aussi le compte total
    réel (`total`), pour que l'appelant puisse dire "sur X au total" si
    `total` dépasse `limite`.
    """
    try:
        comptage = (
            supabase.table("bibliotheque_publique")
            .select("id", count="exact")
            .eq("statut", "publie")
            .execute()
        )
        total = comptage.count if comptage.count is not None else len(comptage.data or [])
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comptage catalogue public) : {e}")
        total = None

    res = (
        supabase.table("bibliotheque_publique")
        .select("id, nom, description, url_publique")
        .eq("statut", "publie")
        .order("created_at", desc=True)
        .limit(limite)
        .execute()
    )
    return {"documents": res.data or [], "total": total}


def chercher_catalogue_public(question: str, match_count: int = 5) -> list:
    """
    Recherche sémantique dans TOUT le catalogue public (pas de filtre
    par utilisateur ni par liste de fichiers -- contrairement à
    chercher_bibliotheque et chercher_bibliotheque_publique, tout le
    catalogue est public par définition).

    Renvoie une liste de {fichier_id, nom, description, url_publique,
    type_mime, similarite} -- PAS de champ `contenu` : cette recherche
    sert uniquement à localiser un document, jamais à fournir un texte
    à citer/paraphraser dans une réponse (voir lire_document_
    catalogue_public pour la lecture intégrale, sur demande explicite
    de l'utilisateur uniquement).
    """
    try:
        vecteur = vectoriser(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        logging.error(f"ERREUR VECTORISATION catalogue public (Gemini) : {e}")
        return []

    try:
        return supabase.rpc(
            "recherche_catalogue_public",
            {"query_embedding": vecteur, "match_count": match_count},
        ).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE RPC recherche_catalogue_public : {e}")
        return []
