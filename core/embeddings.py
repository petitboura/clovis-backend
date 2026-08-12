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

import os
from google import genai
from google.genai import types

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


def decouper_texte(texte, taille=500):
    """Découpe un texte en morceaux de `taille` mots."""
    mots = texte.split()
    return [" ".join(mots[i:i + taille]) for i in range(0, len(mots), taille)] or [""]

