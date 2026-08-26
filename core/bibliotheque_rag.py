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


def extraire_pages_pdf(chemin_pdf: str) -> list[str]:
    """
    Comme extraire_texte_pdf, mais renvoie le texte PAGE PAR PAGE (26/08,
    besoin de conserver le numéro de page pour les citations cliquables
    -- voir indexer_pdf_bibliotheque). Une page vide (aucun texte extrait)
    est renvoyée comme chaîne vide, pas omise, pour que l'index de la
    liste reste le numéro de page réel (0 = page 1).
    """
    pages = []
    with open(chemin_pdf, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            pages.append((page.extract_text() or "").replace("\x00", ""))
    return pages


def extraire_texte_pdf(chemin_pdf: str) -> str:
    """Même logique que indexers/index_documents.py:extraire_texte_pdf, dupliquée volontairement pour ne pas créer de dépendance croisée entre les deux circuits RAG. Conservée telle quelle (texte concaténé) pour lire_document_bibliotheque_en_entier et tout appelant qui n'a pas besoin des pages."""
    return "\n".join(extraire_pages_pdf(chemin_pdf))


def indexer_texte_bibliotheque(
    texte: str,
    fichier_id: str,
    user_id: str,
    page_debut: int | None = None,
    page_fin: int | None = None,
    timestamp_debut: float | None = None,
    timestamp_fin: float | None = None,
) -> int:
    """
    Découpe + vectorise un texte DÉJÀ EXTRAIT (peu importe la source
    d'origine -- PDF, Word, Excel...) et insère ses chunks dans
    documents_bibliotheque, liés à `fichier_id` (pour le nettoyage en
    cascade à la suppression, voir la migration). Renvoie le nombre de
    chunks indexés.

    `page_debut`/`page_fin` et `timestamp_debut`/`timestamp_fin` (26/08,
    citations cliquables) : quand l'appelant sait d'où vient précisément
    ce texte (une page de PDF, un segment audio horodaté), il les passe
    ici -- appliqués tels quels à TOUS les chunks produits par cet appel
    (un appel = une seule page ou un seul segment, voir
    indexer_pdf_bibliotheque et indexer_transcription_bibliotheque, pour
    rester précis). None sinon (note texte, image, lien -- pas de notion
    de position).
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
            "page_debut": page_debut,
            "page_fin": page_fin,
            "timestamp_debut": timestamp_debut,
            "timestamp_fin": timestamp_fin,
        })

    if lignes:
        supabase.table("documents_bibliotheque").insert(lignes).execute()

    logging.info(f"Bibliothèque perso : {len(lignes)} chunk(s) indexé(s) pour fichier_id={fichier_id}")
    return len(lignes)


def indexer_pdf_bibliotheque(chemin_pdf: str, fichier_id: str, user_id: str) -> int:
    """
    Indexe un PDF reçu en chemin de fichier (voir
    api/bibliotheque_utilisateur.py), PAGE PAR PAGE (26/08) plutôt qu'en
    un seul bloc -- chaque chunk produit hérite ainsi du numéro de la
    page dont il vient (page_debut = page_fin = numéro de page, en base
    1). Une page sans texte est simplement ignorée.
    """
    total = 0
    for numero, texte_page in enumerate(extraire_pages_pdf(chemin_pdf), start=1):
        if not texte_page.strip():
            continue
        total += indexer_texte_bibliotheque(
            texte_page, fichier_id, user_id, page_debut=numero, page_fin=numero,
        )
    return total


def indexer_transcription_bibliotheque(segments: list[dict], fichier_id: str, user_id: str) -> int:
    """
    Indexe une transcription audio SEGMENT PAR SEGMENT (26/08, voir
    description_multimedia.transcrire_audio_bibliotheque qui renvoie
    désormais ces segments horodatés au lieu d'un texte brut) -- chaque
    segment devient son propre chunk, avec son timestamp_debut/fin
    d'origine (en secondes), pour permettre de rouvrir le lecteur audio
    directement au bon endroit.
    """
    total = 0
    for segment in segments:
        texte = (segment.get("text") or "").strip()
        if not texte:
            continue
        total += indexer_texte_bibliotheque(
            texte, fichier_id, user_id,
            timestamp_debut=segment.get("start"), timestamp_fin=segment.get("end"),
        )
    return total


def lire_document_bibliotheque_en_entier(fichier_id: str, user_id: str) -> str | None:
    """
    Reconstruit le texte intégral d'un document déjà indexé (PDF/texte),
    en recollant tous ses chunks dans l'ordre d'insertion (id croissant
    == ordre d'origine, voir indexer_texte_bibliotheque qui insère les
    morceaux dans l'ordre). None si le document n'appartient pas à
    user_id ou n'a aucun chunk indexé (vidéo par exemple -- pas encore
    vectorisée aujourd'hui, contrairement à PDF/texte/image/audio).
    """
    try:
        res = (
            supabase.table("documents_bibliotheque")
            .select("contenu")
            .eq("fichier_id", fichier_id)
            .eq("user_id", user_id)
            .order("id")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture intégrale fichier_id={fichier_id}) : {e}")
        return None

    if not res.data:
        return None
    return "\n\n".join(ligne["contenu"] for ligne in res.data)


def formater_source_bibliotheque(r: dict) -> str | None:
    """
    Construit la ligne "(Source : ...)" à afficher après un extrait
    renvoyé par chercher_bibliotheque/chercher_bibliotheque_publique
    (26/08, factorisé pour rester identique partout où c'est utilisé --
    core/serveur_mcp_espace.py et core/serveur_mcp_generation.py).

    Ajoute la page (PDF) ou le timestamp mm:ss (audio) quand ce chunk en
    a un, pour que l'appelant (l'IA, puis le frontend) puisse distinguer
    la SOURCE (le document entier, `url_publique`) du PARAGRAPHE exact
    (cette page/cet instant précis) -- les deux popups cliquables
    demandés par Bourama. None si le résultat n'a même pas de quoi
    identifier une source (jamais censé arriver si nom_fichier/
    url_publique sont bien remontés par la fonction SQL).
    """
    if not (r.get("nom_fichier") and r.get("url_publique")):
        return None
    reperage = ""
    if r.get("page_debut") is not None:
        if r.get("page_fin") and r["page_fin"] != r["page_debut"]:
            reperage = f", page {r['page_debut']}-{r['page_fin']}"
        else:
            reperage = f", page {r['page_debut']}"
    elif r.get("timestamp_debut") is not None:
        debut = int(r["timestamp_debut"])
        reperage = f", à {debut // 60:02d}:{debut % 60:02d}"
    return f"(Source : {r['nom_fichier']}{reperage}, {r['url_publique']})"


def chercher_bibliotheque(question: str, user_id: str, match_count: int = 5) -> list:
    """
    Recherche sémantique dans la bibliothèque personnelle de `user_id`.
    Renvoie une liste de {contenu, similarite, fichier_id, nom_fichier,
    url_publique, type_mime, page_debut, page_fin, timestamp_debut,
    timestamp_fin} (ces 4 derniers champs à None quand le chunk n'a pas
    de position connue -- note texte, image, lien), triée par pertinence
    -- la fonction SQL
    recherche_bibliotheque (17/08) joint désormais fichiers_uploades pour
    que chaque extrait porte la référence de son document d'origine :
    avant ça, un extrait pertinent trouvé ici ne permettait jamais de
    remonter jusqu'au fichier complet (ni son nom, ni son lien), donc
    consulter_bibliotheque ne pouvait renvoyer que du texte brut, jamais
    de quoi montrer le document lui-même (voir consulter_bibliotheque
    dans core/serveur_mcp_generation.py, qui construit le lien à partir
    de url_publique désormais présent ici).
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


def chercher_bibliotheque_publique(question: str, fichier_ids: list[str], match_count: int = 5) -> list:
    """
    Recherche sémantique à travers un ENSEMBLE de fichiers précis, pas un
    seul user_id (20/08/2026, plugin public "contribution libre" -- les
    documents d'un plugin public appartiennent potentiellement à
    plusieurs utilisateurs différents, chercher_bibliotheque ci-dessus
    ne peut donc pas s'appliquer, voir recherche_bibliotheque_publique
    dans migrations/2026_08_20_plugin_bibliotheque_publique.sql).
    `fichier_ids` doit déjà être filtré côté appelant (documents
    réellement classés dans ce plugin) -- cette fonction ne vérifie
    aucune propriété, elle fait confiance à la liste fournie.
    """
    if not fichier_ids:
        return []

    try:
        vecteur = vectoriser(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        logging.error(f"ERREUR VECTORISATION bibliothèque publique (Gemini) : {e}")
        return []

    try:
        return supabase.rpc(
            "recherche_bibliotheque_publique",
            {"query_embedding": vecteur, "match_count": match_count, "p_fichier_ids": fichier_ids},
        ).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE RPC recherche_bibliotheque_publique : {e}")
        return []
