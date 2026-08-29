"""
File d'attente de vectorisation en arrière-plan (29/08/2026, demande
Bourama : l'ajout d'un fichier à la bibliothèque privée ou publique
attendait la vectorisation complète -- extraction + embeddings Gemini
chunk par chunk -- avant de répondre, ce qui bloquait longtemps sur un
gros fichier ou un upload en masse plusieurs fichiers).

Nouveau flux : le fichier est stocké et renvoyé immédiatement, avec
statut_vectorisation="en_attente" s'il doit être vectorisé (voir
necessite_vectorisation_fichier_privee/publique et
necessite_vectorisation_note ci-dessous), "pret" sinon (lien, vidéo,
type non vectorisé -- rien à attendre). Ce module tourne en arrière-plan
(boucle asyncio démarrée dans api/main.py, voir _boucle_vectorisation) et
traite les fichiers "en_attente" un par un, pour chacune des deux
bibliothèques : privée (fichiers_uploades + documents_bibliotheque) et
publique (bibliotheque_publique + documents_catalogue_public).
Volontairement dans le MÊME module malgré la duplication déjà présente
entre bibliotheque_rag.py et catalogue_public_rag.py, pour ne pas
complexifier davantage ces deux fichiers déjà volumineux -- seule la
DISPATCH par type de fichier vit ici, la logique d'extraction/découpage/
embedding elle-même reste dans ces deux modules, inchangée.

Scope délibérément limité à niveau="utilisateur" (bibliothèque perso) et
à bibliotheque_publique (catalogue public) -- PAS la bibliothèque niveau
"agent" (route Diffuser un document, api/roles.py) qui utilise un
troisième système de RAG totalement différent (table `documents`,
indexers/index_documents.py), resté inchangé (confirmé avec Bourama le
29/08 : chantier séparé, pas traité ici).

Robustesse au redémarrage (Railway redéploie à chaque push) :
- remettre_en_attente_bloques(), appelée une fois au démarrage du
  process (voir _lifespan dans api/main.py), repasse tout fichier resté
  "en_cours" (process coupé en plein traitement) à "en_attente" -- repart
  tout seul, jamais besoin d'intervention manuelle.
- avant de (re)vectoriser, les chunks déjà présents pour ce fichier sont
  supprimés d'abord (voir _nettoyer_chunks_existants) -- un traitement
  interrompu à moitié ne laisse jamais de morceaux dupliqués ou
  incomplets qui fausseraient la recherche.
- un compteur tentatives_vectorisation (max MAX_TENTATIVES) évite qu'un
  fichier cassé (PDF corrompu, etc.) ne reparte indéfiniment à chaque
  redémarrage -- passe en statut "echec" au-delà, erreur_vectorisation
  garde le dernier message pour diagnostic.
"""

import logging
import os
import sys
import tempfile

from supabase import create_client

sys.path.append(os.path.dirname(__file__))
from bibliotheque_rag import (  # noqa: E402
    indexer_pdf_bibliotheque,
    indexer_texte_bibliotheque,
    indexer_transcription_bibliotheque,
)
from catalogue_public_rag import (  # noqa: E402
    indexer_pdf_catalogue_public,
    indexer_texte_catalogue_public,
    indexer_transcription_catalogue_public,
)
from description_multimedia import decrire_image_bibliotheque, transcrire_audio_bibliotheque  # noqa: E402

BUCKET_BIBLIOTHEQUE = "bibliotheque"
MAX_TENTATIVES = 3
TAILLE_LOT = 5  # fichiers traités par passage, par bibliothèque -- garde-fou pour qu'un passage ne tourne jamais indéfiniment avant de rendre la main à la boucle appelante.


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def necessite_vectorisation_fichier_privee(type_mime: str | None) -> bool:
    """
    Types vectorisés à l'ajout d'un FICHIER dans la bibliothèque PRIVÉE
    (route POST /api/bibliotheque) -- EXACTEMENT comme l'ancien
    _indexer_et_propager : pdf/image/audio uniquement. Un texte/plain
    envoyé comme fichier via cette route n'a jamais été vectorisé ici
    (seule la note tapée directement, route /texte, l'est -- voir
    necessite_vectorisation_note) : comportement inchangé.
    """
    if not type_mime:
        return False
    return type_mime == "application/pdf" or type_mime.startswith("image/") or type_mime.startswith("audio/")


def necessite_vectorisation_fichier_publique(type_mime: str | None) -> bool:
    """
    Types vectorisés à l'ajout d'un FICHIER dans la bibliothèque
    PUBLIQUE (route POST /api/bibliotheque-publique) -- EXACTEMENT comme
    l'ancien _indexer_catalogue_public : pdf/image/audio, ET text/plain
    (contrairement à la privée -- asymétrie déjà présente avant ce
    chantier, volontairement conservée telle quelle).
    """
    if not type_mime:
        return False
    return (
        type_mime == "application/pdf"
        or type_mime.startswith("image/")
        or type_mime.startswith("audio/")
        or type_mime == "text/plain"
    )


def necessite_vectorisation_note() -> bool:
    """Une note de texte tapée directement (routes /texte, privée ET publique) est toujours vectorisée -- comportement inchangé."""
    return True


def _telecharger(chemin_stockage: str) -> bytes:
    return supabase.storage.from_(BUCKET_BIBLIOTHEQUE).download(chemin_stockage)


def _nettoyer_chunks_existants(table_chunks: str, colonne_scope: str | None, valeur_scope, fichier_id: str) -> None:
    """
    Supprime les chunks déjà indexés pour ce fichier avant de le
    (re)traiter -- un traitement interrompu à moitié (redémarrage,
    crash) ne doit jamais laisser de morceaux dupliqués ou incomplets en
    base (voir docstring du module).
    """
    requete = supabase.table(table_chunks).delete().eq("fichier_id", fichier_id)
    if colonne_scope:
        requete = requete.eq(colonne_scope, valeur_scope)
    requete.execute()


def _vectoriser_privee(ligne: dict) -> None:
    fichier_id = ligne["id"]
    user_id = ligne["user_id"]
    type_mime = ligne["type_mime"] or ""
    contenu = _telecharger(ligne["chemin_stockage"])

    _nettoyer_chunks_existants("documents_bibliotheque", "user_id", user_id, fichier_id)

    if type_mime == "application/pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenu)
            chemin_temp = tmp.name
        try:
            indexer_pdf_bibliotheque(chemin_temp, fichier_id=fichier_id, user_id=user_id)
        finally:
            try:
                os.remove(chemin_temp)
            except OSError:
                pass
    elif type_mime.startswith("image/"):
        description_image = decrire_image_bibliotheque(contenu, type_mime)
        if description_image:
            indexer_texte_bibliotheque(description_image, fichier_id=fichier_id, user_id=user_id)
    elif type_mime.startswith("audio/"):
        segments_audio = transcrire_audio_bibliotheque(contenu, ligne["nom_fichier"])
        if segments_audio:
            indexer_transcription_bibliotheque(segments_audio, fichier_id=fichier_id, user_id=user_id)
    elif type_mime == "text/plain":
        # Note tapée directement (route /texte) -- déjà du texte, pas
        # besoin d'extraction.
        indexer_texte_bibliotheque(contenu.decode("utf-8", errors="ignore"), fichier_id=fichier_id, user_id=user_id)


def _vectoriser_publique(ligne: dict) -> None:
    fichier_id = ligne["id"]
    type_mime = ligne["type_mime"] or ""
    contenu = _telecharger(ligne["chemin_stockage"])

    _nettoyer_chunks_existants("documents_catalogue_public", None, None, fichier_id)

    if type_mime == "application/pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenu)
            chemin_temp = tmp.name
        try:
            indexer_pdf_catalogue_public(chemin_temp, fichier_id=fichier_id)
        finally:
            try:
                os.remove(chemin_temp)
            except OSError:
                pass
    elif type_mime.startswith("image/"):
        description_image = decrire_image_bibliotheque(contenu, type_mime)
        if description_image:
            indexer_texte_catalogue_public(description_image, fichier_id=fichier_id)
    elif type_mime.startswith("audio/"):
        segments_audio = transcrire_audio_bibliotheque(contenu, ligne["nom_fichier"])
        if segments_audio:
            indexer_transcription_catalogue_public(segments_audio, fichier_id=fichier_id)
    elif type_mime == "text/plain":
        indexer_texte_catalogue_public(contenu.decode("utf-8", errors="ignore"), fichier_id=fichier_id)


def remettre_en_attente_bloques() -> None:
    """Appelée une fois au démarrage du process (voir api/main.py:_lifespan) -- voir docstring du module."""
    for table in ("fichiers_uploades", "bibliotheque_publique"):
        try:
            supabase.table(table).update({"statut_vectorisation": "en_attente"}).eq(
                "statut_vectorisation", "en_cours"
            ).execute()
        except Exception as e:
            logging.error(f"ERREUR remise en attente au démarrage ({table}) : {e}")


COLONNES_PRIVEE = "id, user_id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation"
COLONNES_PUBLIQUE = "id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation"


def _traiter_lot(table: str, colonnes: str, fonction_vectorisation, filtre_niveau: bool) -> int:
    """
    Traite jusqu'à TAILLE_LOT fichiers "en_attente" de `table`, du plus
    ancien au plus récent (pour qu'une longue file ne fasse jamais
    indéfiniment attendre les premiers fichiers ajoutés). `filtre_niveau`
    (True pour fichiers_uploades) restreint à niveau="utilisateur" --
    par sécurité supplémentaire, même si aucun autre niveau n'est censé
    passer à "en_attente" (voir docstring du module : agent/plateforme
    non concernés par ce chantier). Renvoie le nombre de fichiers traités
    (succès + échecs confondus).
    """
    try:
        requete = supabase.table(table).select(colonnes).eq("statut_vectorisation", "en_attente")
        if filtre_niveau:
            requete = requete.eq("niveau", "utilisateur")
        lignes = requete.order("created_at").limit(TAILLE_LOT).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR lecture file d'attente ({table}) : {e}")
        return 0

    for ligne in lignes:
        fichier_id = ligne["id"]
        try:
            supabase.table(table).update({"statut_vectorisation": "en_cours"}).eq("id", fichier_id).execute()
            fonction_vectorisation(ligne)
            supabase.table(table).update({
                "statut_vectorisation": "pret",
                "erreur_vectorisation": None,
            }).eq("id", fichier_id).execute()
        except Exception as e:
            tentatives = (ligne.get("tentatives_vectorisation") or 0) + 1
            nouveau_statut = "echec" if tentatives >= MAX_TENTATIVES else "en_attente"
            logging.error(f"ERREUR vectorisation ({table}, fichier_id={fichier_id}, tentative {tentatives}) : {e}")
            try:
                supabase.table(table).update({
                    "statut_vectorisation": nouveau_statut,
                    "tentatives_vectorisation": tentatives,
                    "erreur_vectorisation": str(e)[:500],
                }).eq("id", fichier_id).execute()
            except Exception as e2:
                logging.error(f"ERREUR mise à jour statut échec ({table}, fichier_id={fichier_id}) : {e2}")

    return len(lignes)


def traiter_file_attente_une_fois() -> int:
    """Un seul passage sur les deux bibliothèques -- voir api/main.py:_boucle_vectorisation pour la boucle continue."""
    total = 0
    total += _traiter_lot("fichiers_uploades", COLONNES_PRIVEE, _vectoriser_privee, filtre_niveau=True)
    total += _traiter_lot("bibliotheque_publique", COLONNES_PUBLIQUE, _vectoriser_publique, filtre_niveau=False)
    return total
