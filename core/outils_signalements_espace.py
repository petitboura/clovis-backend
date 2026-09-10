"""
Outils MCP publics pour qu'un prof connecté à Clovis via un client MCP
externe puisse consulter et discuter de ses signalements pédagogiques
sans repasser par l'interface web (refonte du 10/09/2026, voir
core/outils_signalements.py pour l'équivalent interne).

Même convention que core/serveur_mcp_espace.py : logique importée
depuis core/signalements.py, identité de l'appelant exclusivement via
le jeton OAuth déjà vérifié (_user_id_authentifie), jamais un paramètre
fourni en clair par le modèle. `signalement_id` reste ici un paramètre
EXPLICITE (obtenu via clovis_lister_signalements_prof), comme côté
interne.
"""

import logging

from core.signalements import (
    lister_signalements_prof as _lister_signalements_prof,
    obtenir_signalement as _obtenir_signalement,
    enregistrer_note as _enregistrer_note,
)
from core.serveur_mcp_espace import mcp_espace, Context, ToolAnnotations, _user_id_authentifie


@mcp_espace.tool(
    name="clovis_lister_signalements_prof",
    title="Lister mes signalements pédagogiques",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_signalements_prof_espace(ctx: Context, statut: str = "nouveau") -> str:
    """
    Liste les signalements pédagogiques reçus par ce prof, avec leur id
    (nécessaire pour clovis_enregistrer_note_signalement).
    `statut` : "nouveau" (défaut) ou "discute".
    """
    prof_id = _user_id_authentifie(ctx)
    if not prof_id:
        return "Erreur : utilisateur non authentifié."
    try:
        signalements = _lister_signalements_prof(prof_id, statut=statut)
    except Exception as e:
        logging.error(f"ERREUR outil clovis_lister_signalements_prof : {e}")
        return "Erreur : impossible de lister les signalements, réessaie."
    if not signalements:
        return f"Aucun signalement au statut '{statut}'."
    lignes = []
    for s in signalements:
        question = s["question_texte"] if s["visible_question"] else "(non partagée)"
        reponse = s["reponse_texte"] if s["visible_reponse"] else "(non partagée)"
        lignes.append(f"- [id: {s['id']}] Question : {question}\n  Réponse : {reponse}")
    return "\n".join(lignes)


@mcp_espace.tool(
    name="clovis_enregistrer_note_signalement",
    title="Enregistrer une note sur un signalement",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def enregistrer_note_signalement_espace(signalement_id: str, note: str, ctx: Context) -> str:
    """
    Enregistre ou met à jour la note de synthèse sur un signalement
    précis (voir clovis_lister_signalements_prof pour obtenir son id).
    Appelable à tout moment, jamais une fin de discussion forcée.
    """
    prof_id = _user_id_authentifie(ctx)
    if not prof_id:
        return "Erreur : utilisateur non authentifié."
    note = (note or "").strip()
    if not note:
        return "Erreur : la note ne peut pas être vide."

    ligne = _obtenir_signalement(signalement_id)
    if ligne is None or ligne.get("prof_id") != prof_id:
        return "Ce signalement est introuvable ou ne t'appartient pas."

    try:
        resultat = _enregistrer_note(signalement_id, prof_id, note)
    except Exception as e:
        logging.error(f"ERREUR outil clovis_enregistrer_note_signalement ({signalement_id}) : {e}")
        return "Erreur : impossible d'enregistrer cette note, réessaie."

    if resultat is None:
        return "Ce signalement est introuvable ou ne t'appartient pas."
    return "Note enregistrée."
