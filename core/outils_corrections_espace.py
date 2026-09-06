"""
Outils MCP publics (Point 2, Partie 4, 06/09/2026, demande Bourama :
"les deux, aussi sur mcp_espace") pour qu'un prof connecté à Clovis via
un client MCP externe (Claude) puisse consulter et corriger ses
signalements de type A sans repasser par l'interface web.

Même convention que core/serveur_mcp_espace.py (voir sa docstring) :
logique dupliquée depuis core/corrections_pedagogiques.py plutôt
qu'importée depuis un fichier api/*.py, identité de l'appelant
exclusivement via le jeton OAuth déjà vérifié (_user_id_authentifie),
jamais un paramètre fourni en clair par le modèle.

Contrairement à l'outil interne (core/outils_corrections_pedagogiques.py,
qui lit correction_id depuis un query param injecté par la conversation
dédiée ouverte côté Bureau), ce fichier n'a pas cette injection
serveur-côté possible ici, `correction_id` est donc un paramètre
EXPLICITE de l'outil d'écriture, à obtenir via clovis_lister_corrections_prof
d'abord.
"""

import logging

from core.corrections_pedagogiques import (
    lister_corrections_prof as _lister_corrections_prof,
    enregistrer_correction_prof as _enregistrer_correction_prof,
)
from core.serveur_mcp_espace import mcp_espace, Context, ToolAnnotations, _user_id_authentifie


@mcp_espace.tool(
    name="clovis_lister_corrections_prof",
    title="Lister mes signalements à corriger",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_corrections_prof_espace(ctx: Context, statut: str = "nouveau") -> str:
    """
    Liste les signalements de type A (correctifs de fond) reçus par ce
    prof via ses codes, avec leur id (nécessaire pour
    clovis_enregistrer_correction_prof). `statut` : "nouveau" (défaut,
    en attente de correction) ou "traite".
    """
    prof_id = _user_id_authentifie(ctx)
    if not prof_id:
        return "Erreur : utilisateur non authentifié."
    try:
        corrections = _lister_corrections_prof(prof_id, type_="A", statut=statut)
    except Exception as e:
        logging.error(f"ERREUR outil clovis_lister_corrections_prof : {e}")
        return "Erreur : impossible de lister les signalements, réessaie."
    if not corrections:
        return f"Aucun signalement au statut '{statut}'."
    lignes = []
    for c in corrections:
        lignes.append(
            f"- [id: {c['id']}] Question : {c['question_texte']}\n  Réponse fautive : {c['reponse_texte']}"
        )
    return "\n".join(lignes)


@mcp_espace.tool(
    name="clovis_enregistrer_correction_prof",
    title="Corriger un signalement",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def enregistrer_correction_prof_espace(
    correction_id: str,
    correction_texte: str,
    ctx: Context,
    contexte_conversation: str = "",
) -> str:
    """
    Enregistre la correction de ce prof sur un signalement de type A
    précis (voir clovis_lister_corrections_prof pour obtenir son id) :
    généralise automatiquement la correction en une règle réutilisable,
    puis la rattache aux comportements de l'élève concerné.
    """
    prof_id = _user_id_authentifie(ctx)
    if not prof_id:
        return "Erreur : utilisateur non authentifié."
    correction_texte = (correction_texte or "").strip()
    if not correction_texte:
        return "Erreur : le texte de correction est requis."

    # question/reponse_fautive : repris de la ligne elle-même (déjà
    # capturés à la création du signalement) plutôt que redemandés au
    # modèle, ici, contrairement à l'outil interne, la conversation
    # n'est pas pré-remplie avec ce contexte.
    from core.corrections_pedagogiques import obtenir_correction as _obtenir_correction
    ligne = _obtenir_correction(correction_id)
    if ligne is None or ligne.get("prof_id") != prof_id or ligne.get("type") != "A":
        return "Ce signalement est introuvable, déjà traité, ou ne t'appartient pas."

    try:
        resultat = _enregistrer_correction_prof(
            correction_id=correction_id,
            prof_id=prof_id,
            question=ligne.get("question_texte") or "",
            reponse_fautive=ligne.get("reponse_texte") or "",
            contexte_conversation=contexte_conversation,
            correction_texte=correction_texte,
        )
    except Exception as e:
        logging.error(f"ERREUR outil clovis_enregistrer_correction_prof ({correction_id}) : {e}")
        return "Erreur : impossible d'enregistrer cette correction, réessaie."

    if resultat is None:
        return "Ce signalement est introuvable, déjà traité, ou ne t'appartient pas."
    return "Correction enregistrée et transformée en règle pour cet élève."
