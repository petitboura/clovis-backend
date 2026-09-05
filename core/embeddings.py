"""
Fonctions de vectorisation partagées par tous les modules du projet
(retriever, indexers).

Utilise gemini-embedding-001 (Google), en remplacement de l'ancien
text-embedding-ada-002 (OpenAI, via OpenRouter).

IMPORTANT : changer de modèle change la dimension des vecteurs. Les
vecteurs déjà stockés dans Supabase (dimension ada-002 = 1536) ne sont
PAS compatibles avec ceux produits ici. Un ré-index complet de tous les
documents et prompts existants est nécessaire après ce changement — voir
la migration Supabase associée (colonne vector(768), RPC mises à jour).
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types
from supabase import create_client

# gemini-embedding-001 sort en 3072-dim par défaut, mais supporte la
# troncature (Matryoshka Representation Learning) vers 768 ou 1536 sans
# perte de qualité significative. 768 = bon compromis stockage/vitesse
# pour la taille de ce projet.
DIMENSION_EMBEDDING = 768

MODELE_EMBEDDING = "gemini-embedding-001"


def get_secret(key):
    return os.environ.get(key)


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
    return _client


def vectoriser(texte, task_type="RETRIEVAL_DOCUMENT"):
    """
    Vectorise un texte avec gemini-embedding-001.

    `task_type` :
    - "RETRIEVAL_DOCUMENT" (défaut) : pour un chunk indexé (documents, prompts)
    - "RETRIEVAL_QUERY" : pour une question posée par l'étudiant (retriever.py)

    Séparer les deux améliore la qualité du matching : le modèle sait que
    d'un côté c'est un passage à retrouver, de l'autre une question qui
    cherche à le retrouver — la relation n'est pas symétrique.
    """
    response = _get_client().models.embed_content(
        model=MODELE_EMBEDDING,
        contents=texte,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=DIMENSION_EMBEDDING,
        ),
    )
    return response.embeddings[0].values


def est_erreur_quota_gemini(message: str) -> bool:
    """
    Détecte un quota Gemini épuisé (429 RESOURCE_EXHAUSTED, quota GRATUIT
    quotidien -- generativelanguage.googleapis.com/embed_content_free_
    tier_requests) à partir du texte d'une exception.
    """
    message = (message or "").upper()
    return "RESOURCE_EXHAUSTED" in message or "429" in message


# Ajoute le 05/09/2026, demande Bourama : coupe-circuit GLOBAL, partagé
# par TOUTES les files d'attente de vectorisation (bibliotheque perso/
# publique ET dossiers designes -- meme cle Google, meme quota
# quotidien). Des qu'UN SEUL fichier, n'importe lequel, n'importe
# laquelle des files, tape le quota epuise : plus AUCUN fichier de
# AUCUNE file n'est meme tente pendant DUREE_PAUSE_QUOTA (24h) -- "porte
# fermee" litteralement, pas juste ce fichier-la mis de cote. Stocke
# dans la table generique parametres_outils (cle/valeur) deja existante,
# pour survivre a un redemarrage/redeploiement Railway (une pause en
# memoire simple serait effacee au prochain push). Passe la pause,
# reprise 100% normale : plus aucune trace, tout redevient comme avant
# (pas d'exclusion permanente par fichier).
_CLE_PAUSE_QUOTA = "vectorisation_pause_quota_gemini_jusqua"
DUREE_PAUSE_QUOTA = timedelta(hours=24)

_supabase_parametres = None


def _get_supabase_parametres():
    global _supabase_parametres
    if _supabase_parametres is None:
        _supabase_parametres = create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_SECRET"))
    return _supabase_parametres


def est_en_pause_quota_gemini() -> bool:
    """
    True si la "porte est fermee" -- un quota a ete tape il y a moins de
    DUREE_PAUSE_QUOTA par N'IMPORTE QUELLE file de vectorisation. Toute
    erreur de lecture (table injoignable, ligne absente) est traitee
    comme "pas en pause" -- on ne bloque jamais tout le systeme a cause
    d'un probleme sur ce mecanisme lui-meme.
    """
    try:
        res = (
            _get_supabase_parametres()
            .table("parametres_outils")
            .select("valeur")
            .eq("cle", _CLE_PAUSE_QUOTA)
            .maybe_single()
            .execute()
        )
        valeur = (res.data or {}).get("valeur") if res else None
        if not valeur:
            return False
        pause_jusqua = datetime.fromisoformat(valeur)
        return datetime.now(timezone.utc) < pause_jusqua
    except Exception as e:
        logging.error(f"ERREUR lecture pause quota Gemini : {e}")
        return False


def activer_pause_quota_gemini() -> None:
    """
    Ferme la porte pour DUREE_PAUSE_QUOTA (24h) a partir de MAINTENANT --
    appelee des la premiere detection d'un quota epuise, par n'importe
    laquelle des files de vectorisation.
    """
    pause_jusqua = (datetime.now(timezone.utc) + DUREE_PAUSE_QUOTA).isoformat()
    try:
        _get_supabase_parametres().table("parametres_outils").upsert(
            {"cle": _CLE_PAUSE_QUOTA, "valeur": pause_jusqua}
        ).execute()
        logging.error(f"QUOTA GEMINI épuisé : pause de toute vectorisation jusqu'à {pause_jusqua}.")
    except Exception as e:
        logging.error(f"ERREUR activation pause quota Gemini : {e}")


def decouper_texte(texte, taille=500):
    """Découpe un texte en morceaux de `taille` mots."""
    mots = texte.split()
    return [" ".join(mots[i:i + taille]) for i in range(0, len(mots), taille)] or [""]

