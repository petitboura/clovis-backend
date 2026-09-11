"""
Outils MCP internes pour le système de signalements pédagogiques
(refonte du 10/09/2026, voir core/signalements.py).

Contrairement à l'ancien enregistrer_correction_prof (supprimé), ces
outils reçoivent `signalement_id` comme un PARAMÈTRE NORMAL passé par
le modèle -- plus de query param magique lu côté serveur. Le frontend
pré-remplit le premier message de la conversation dédiée avec cet id
en clair, et cette docstring dit explicitement au modèle de le
reprendre tel quel dans ses appels d'outil pour cette conversation.

user_id (prof) reste lu depuis les query params (jamais fourni par le
modèle, même pattern que le reste du fichier core/outils_*.py),
vérifié contre signalements.prof_id avant toute écriture.
"""

import logging

from core.signalements import (
    obtenir_signalement as _obtenir_signalement,
    enregistrer_note as _enregistrer_note,
    rattacher_signalement as _rattacher_signalement,
    consulter_par_notion as _consulter_par_notion,
)
from core.avancement_notions_ia import resoudre_code_actif_eleve as _resoudre_code_actif_eleve
from core.mode_actif_conversation import obtenir_mode_actif as _obtenir_mode_actif
from core.outils_generation_commun import mcp_generation, Context


def _prof_id(ctx: Context) -> str | None:
    return ctx.request_context.request.query_params.get("user_id")


@mcp_generation.tool()
def consulter_signalement(signalement_id: str, ctx: Context) -> str:
    """
    Relit l'état actuel d'un signalement pédagogique : question, réponse
    fautive, ce que l'élève a rendu visible ou non, ce qu'il a décrit
    comme problème, et la note de synthèse actuelle si une discussion a
    déjà commencé. Utilise cet outil au début d'une conversation ouverte
    depuis "Bureau > signalements" pour savoir de quoi il s'agit, et à
    nouveau si la discussion reprend après une pause.

    `signalement_id` : reprends exactement l'identifiant donné dans le
    premier message de cette conversation, ne l'invente jamais.
    """
    prof_id = _prof_id(ctx)
    if not prof_id:
        return "Erreur : impossible d'identifier le professeur."

    ligne = _obtenir_signalement(signalement_id)
    if ligne is None or ligne.get("prof_id") != prof_id:
        return "Ce signalement est introuvable ou ne t'appartient pas."

    parties = []
    if ligne["visible_question"]:
        parties.append(f"Question de l'élève : {ligne['question_texte']}")
    else:
        parties.append("Question de l'élève : non partagée (tu peux la demander).")
    if ligne["visible_reponse"]:
        parties.append(f"Réponse donnée par l'IA : {ligne['reponse_texte']}")
    else:
        parties.append("Réponse de l'IA : non partagée (tu peux la demander).")
    if ligne["visible_conversation"] and ligne.get("contexte_conversation"):
        parties.append(f"Contexte de conversation : {ligne['contexte_conversation']}")
    if ligne.get("probleme_observe"):
        parties.append(f"Problème décrit par l'élève : {ligne['probleme_observe']}")
    if ligne.get("correction_texte"):
        parties.append(f"Note de synthèse actuelle : {ligne['correction_texte']}")
    else:
        parties.append("Aucune note de synthèse enregistrée pour l'instant.")

    return "\n".join(parties)


@mcp_generation.tool()
def enregistrer_note_signalement(signalement_id: str, note: str, ctx: Context) -> str:
    """
    Enregistre ou met à jour la note de synthèse d'un signalement,
    d'après la discussion en cours avec le prof. Appelable à tout
    moment de la discussion, autant de fois que nécessaire -- jamais
    obligatoire, jamais une fin de discussion forcée. N'invente jamais
    le contenu : reformule fidèlement ce sur quoi vous venez de tomber
    d'accord avec le prof, rien de plus.

    `signalement_id` : reprends exactement l'identifiant donné dans le
    premier message de cette conversation.
    """
    prof_id = _prof_id(ctx)
    if not prof_id:
        return "Erreur : impossible d'identifier le professeur."

    note = (note or "").strip()
    if not note:
        return "Erreur : la note ne peut pas être vide."

    try:
        ligne = _enregistrer_note(signalement_id, prof_id, note)
    except Exception as e:
        logging.error(f"ERREUR outil enregistrer_note_signalement ({signalement_id}) : {e}")
        return "Erreur : impossible d'enregistrer cette note, réessaie."

    if ligne is None:
        return "Ce signalement est introuvable ou ne t'appartient pas."
    return "Note enregistrée."


@mcp_generation.tool()
def rattacher_signalement_notion(
    signalement_id: str,
    code_id: str | None,
    notion_id: str | None,
    ctx: Context,
) -> str:
    """
    Rattache ce signalement à une matière (code_id) et/ou une notion
    précise (notion_id), les deux combinables. Utilise cet outil
    uniquement si le prof te donne explicitement la matière/notion
    concernée pendant la discussion -- ne devine jamais un rattachement
    à partir du seul contenu de la question.
    """
    prof_id = _prof_id(ctx)
    if not prof_id:
        return "Erreur : impossible d'identifier le professeur."

    try:
        ligne = _rattacher_signalement(signalement_id, prof_id, code_id, notion_id)
    except Exception as e:
        logging.error(f"ERREUR outil rattacher_signalement_notion ({signalement_id}) : {e}")
        return "Erreur : impossible de rattacher ce signalement, réessaie."

    if ligne is None:
        return "Ce signalement est introuvable ou ne t'appartient pas."
    return "Signalement rattaché."


@mcp_generation.tool()
def consulter_signalements_pertinents(ctx: Context) -> str:
    """
    Outil SECONDAIRE (10/09/2026, refonte du système de signalements) :
    les notes du prof sur les signalements passés, pertinentes pour la
    matière/notion active de cette conversation, sont déjà injectées
    automatiquement dans ton prompt système à chaque message (voir
    signalements_pertinents_pour_injection) -- dans la plupart des cas
    tu n'as PAS besoin d'appeler cet outil. Utilise-le seulement si tu
    penses qu'une note pertinente n'est pas apparue dans cette liste
    automatique (par exemple une note rattachée à la matière mais pas
    à la notion précise dont vous parlez là, ou l'inverse).

    Aucun paramètre : relit simplement, avec la même portée
    (matière/notion active de CETTE conversation) que l'injection
    automatique -- n'invente jamais une matière ou une notion
    différente de celle réellement active.
    """
    etudiant_id = ctx.request_context.request.query_params.get("user_id")
    agent_id = ctx.request_context.request.query_params.get("agent_id")
    if not etudiant_id or not agent_id:
        return "Erreur : impossible d'identifier l'élève ou l'agent."

    conversation_id = ctx.request_context.request.query_params.get("conversation_id")
    rattachement_id_actif = None
    if conversation_id:
        mode_actif = _obtenir_mode_actif(conversation_id, etudiant_id)
        rattachement_id_actif = mode_actif.get("rattachement_id") if mode_actif else None

    code = _resoudre_code_actif_eleve(etudiant_id, rattachement_id_actif)
    if code is None:
        return "Cet élève n'est rattaché à aucune matière -- aucune note à consulter."
    if isinstance(code, list):
        return "Cet élève est rattaché à plusieurs matières sans mode actif choisi, impossible de savoir laquelle consulter."

    lignes = _consulter_par_notion(agent_id, etudiant_id, code["id"], None)
    if not lignes:
        return "Aucune note du prof disponible pour cette matière."

    parties = []
    for l in lignes:
        probleme = f" (problème observé : \"{l['probleme_observe']}\")" if l.get("probleme_observe") else ""
        parties.append(f"- {l['correction_texte']}{probleme}")
    return "\n".join(parties)
