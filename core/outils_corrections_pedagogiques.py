"""
Outil MCP interne (Point 2, Partie 4, 06/09/2026) : permet au PROF
d'enregistrer, directement dans le chat, une correction sur une réponse
de type A signalée par un élève. Le type B n'a pas de flux de correction
par le prof (il part vers la cascade de la Partie 10, pas construite ici) :
pas d'outil pour ce type.

Même pattern que core/outils_comportements_connaissance.py : user_id/
agent_id lus depuis les query params de la requête MCP (injectés
serveur-côté, voir core/registre_outils.py), jamais fournis par le
modèle. `correction_id` est lu de la même façon, Partie 5 (frontend,
pas encore construite) devra ajouter ce query param à l'URL du serveur
MCP local pour la conversation dédiée "corriger ce signalement" qu'elle
ouvre ; sans lui, l'outil ne peut pas savoir QUELLE correction en
attente est visée et refuse explicitement plutôt que de deviner.

user_id ici est bien celui du PROF en train de chatter (jamais l'élève
qui a signalé), vérifié contre corrections_pedagogiques.prof_id avant
toute écriture (voir core/corrections_pedagogiques.py::_correction_du_prof).
"""

import logging

from core.corrections_pedagogiques import enregistrer_correction_prof as _enregistrer_correction_prof
from core.outils_generation_commun import mcp_generation, Context


@mcp_generation.tool()
def enregistrer_correction_prof(
    question: str,
    reponse_fautive: str,
    contexte_conversation: str,
    correction_texte: str,
    ctx: Context,
) -> str:
    """
    Enregistre la correction du prof sur une réponse de type A signalée
    par un élève ("Bureau" > signalements, Partie 5) : généralise
    automatiquement cette correction en une règle réutilisable, puis la
    rattache aux comportements de l'ÉLÈVE concerné (elle s'appliquera
    ensuite à ses conversations futures, comme un comportement normal).

    Utilise cet outil UNIQUEMENT dans une conversation ouverte depuis un
    signalement précis (le contexte, question, réponse fautive, est
    déjà pré-rempli dans cette conversation). N'invente jamais les
    paramètres : reprends exactement la question et la réponse fautive
    telles qu'affichées, et la correction telle que le prof vient de la
    dicter/taper.

    Paramètres :
    - `question` : la question d'origine de l'élève.
    - `reponse_fautive` : la réponse incorrecte donnée par l'assistant.
    - `contexte_conversation` : un résumé bref du contexte pertinent
      (peut être vide si la question se suffit à elle-même).
    - `correction_texte` : la correction du prof, telle qu'il vient de
      la formuler dans ce message.
    """
    requete = ctx.request_context.request
    prof_id = requete.query_params.get("user_id")
    correction_id = requete.query_params.get("correction_id")
    if not prof_id:
        return "Erreur : impossible d'identifier le professeur."
    if not correction_id:
        return "Erreur : aucun signalement précis n'est rattaché à cette conversation."

    correction_texte = (correction_texte or "").strip()
    if not correction_texte:
        return "Erreur : le texte de correction est requis."

    try:
        ligne = _enregistrer_correction_prof(
            correction_id=correction_id,
            prof_id=prof_id,
            question=question or "",
            reponse_fautive=reponse_fautive or "",
            contexte_conversation=contexte_conversation or "",
            correction_texte=correction_texte,
        )
    except Exception as e:
        logging.error(f"ERREUR outil enregistrer_correction_prof ({correction_id}) : {e}")
        return "Erreur : impossible d'enregistrer cette correction, réessaie."

    if ligne is None:
        return "Ce signalement est introuvable, déjà traité, ou ne t'appartient pas."
    return "Correction enregistrée et transformée en règle pour cet élève."
