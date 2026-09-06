"""
Généralisation par LLM d'une correction de prof (Point 2, Partie 4,
06/09/2026) en une instruction réutilisable, prête à être transformée en
comportement/skill via core/comportements_etudiants.py::ajouter_comportement
(qui appelle lui-même _generer_skill, ce fichier-ci ne produit QUE le
texte source, jamais le skill_md final, deux responsabilités séparées
comme demandé dans le plan de travail).

Entrée : question de l'élève, réponse fautive de l'IA, contexte de
conversation, correction du prof (texte libre, dicté ou tapé dans le
chat). Sortie : une instruction généralisée, écrite comme si c'était
l'élève/le prof qui l'avait formulée pour "Mes comportements" (même
registre que ce que _generer_skill attend en entrée ailleurs),
au-delà du cas précis signalé, applicable à des questions similaires.
"""

import logging

from groq import Groq

from core.comportements_etudiants import get_secret

logging.basicConfig(level=logging.INFO)

# Même modèle "costaud" que la génération de skill (core/comportements_etudiants.py
# ::MODELE_SKILL), généraliser une correction pédagogique est un travail
# de raisonnement, pas un résumé d'une phrase.
MODELE_GENERALISATION = "openai/gpt-oss-120b"


def generaliser_correction_pedagogique(question: str, reponse_fautive: str, contexte: str, correction_prof: str) -> str:
    """Renvoie une instruction généralisée (texte brut, pas de
    frontmatter) prête à être passée à ajouter_comportement. Fail-safe
    strict : toute erreur LLM renvoie directement correction_prof tel
    quel plutôt que de bloquer l'enregistrement de la correction (une
    règle non généralisée vaut mieux qu'une correction perdue)."""
    correction_prof = (correction_prof or "").strip()
    if not correction_prof:
        return correction_prof

    prompt = (
        "Un professeur vient de corriger une réponse incorrecte donnée par un "
        "assistant IA à un élève. Généralise sa correction en UNE instruction "
        "claire et réutilisable, écrite à la deuxième personne comme si elle "
        "s'adressait directement à l'assistant, qui doit s'appliquer à toute "
        "question similaire à l'avenir (pas seulement reformuler la bonne "
        "réponse à CETTE question précise, extraire la règle/méthode "
        "générale derrière la correction du prof). Réponds UNIQUEMENT avec "
        "le texte de l'instruction généralisée, rien d'autre autour.\n\n"
        f"Question de l'élève :\n{question}\n\n"
        f"Réponse incorrecte de l'assistant :\n{reponse_fautive}\n\n"
        f"Contexte de la conversation :\n{contexte or '(aucun)'}\n\n"
        f"Correction du prof :\n{correction_prof}"
    )

    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=20.0)
        completion = client.chat.completions.create(
            model=MODELE_GENERALISATION,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=500,
            timeout=20.0,
        )
        regle = (completion.choices[0].message.content or "").strip()
        return regle or correction_prof
    except Exception as e:
        logging.error(f"ERREUR généralisation correction pédagogique : {e}")
        return correction_prof
