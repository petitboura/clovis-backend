"""
Outil MCP pour le catalogue PUBLIC des skills ("comportements" en
interne, voir core/comportements_etudiants.py) -- 09/09/2026, demande
Bourama : "pareil avec les skills publique". Avant cet ajout, ce
catalogue (table comportements_publics, section "Catalogue" de
l'interface) n'était accessible à AUCUN LLM, ni pour chercher ni pour
activer -- uniquement via l'UI (api/comportements_publics.py).

Contrairement au catalogue public de la bibliothèque, il n'y a NI
dossiers NI filtres (pays/niveau/...) pour les skills publics --
seulement nom/description/texte/nombre d'activations. Nouvel outil
séparé de gerer_comportement (skills PERSONNELS de cet utilisateur),
même principe de séparation que gerer_dossier_catalogue_public vs
gerer_dossier_bibliotheque.
"""

import logging

from core.outils_generation_commun import mcp_generation, Context
from core.comportements_etudiants import (
    lister_comportements_publics as _lister_comportements_publics,
    publier_comportement_public as _publier_comportement_public,
    activer_comportement_public as _activer_comportement_public,
    retirer_skill_public as _retirer_skill_public,
)


@mcp_generation.tool()
def gerer_comportement_public(
    action: str,
    ctx: Context,
    mot_cle: str = "",
    comportement_id: str = "",
    comportement_public_id: str = "",
) -> str:
    """
    Gère le catalogue PUBLIC des skills ("Catalogue" de l'interface,
    visible par tout le monde) -- distinct de gerer_comportement (skills
    PERSONNELS de cet utilisateur uniquement, jamais publiés).
    IMPORTANT (terme utilisateur) : "skill(s)" est le SEUL mot utilisé
    dans l'interface, "comportement" reste un nom interne.

    `action` doit être l'une de :
    - "chercher" : cherche/liste les skills publics (nom, description,
      nombre d'activations). Paramètre optionnel : `mot_cle` -- laisse
      vide pour lister les plus populaires sans filtre.
    - "publier" : publie une copie figée d'un skill PERSONNEL de cet
      utilisateur (un qu'il a déjà, voir gerer_comportement action
      "lister" pour obtenir son id) dans le catalogue public. La copie
      publiée est indépendante : la modifier ensuite en personnel ne
      change plus la version publique. Paramètre : `comportement_id`
      (id du skill PERSONNEL à publier, PAS un id du catalogue public).
    - "activer" : active un skill du catalogue public chez cet
      utilisateur -- crée une copie indépendante dans SES skills
      personnels (section "Mes comportements"/"Mes skills" de "Mon
      espace"), déjà active. S'il l'a déjà activé, renvoie sa copie
      existante plutôt que d'en recréer une deuxième. Paramètre :
      `comportement_public_id` (id obtenu via "chercher").
    - "retirer" : retire un skill que CET utilisateur a lui-même publié
      dans le catalogue public (n'importe qui l'ayant déjà activé garde
      sa propre copie indépendante, jamais affectée). Paramètre :
      `comportement_public_id`.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    agent_id = ctx.request_context.request.query_params.get("agent_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "chercher":
        try:
            resultats = _lister_comportements_publics(mot_cle)
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement_public (chercher) : {e}")
            return "Erreur : impossible de chercher dans le catalogue public de skills, réessaie."
        if not resultats:
            return "Aucun skill public trouvé."
        return "\n".join(
            f"- {r['nom']} [id: {r['id']}] ({r.get('activations_count', 0)} activation(s))"
            + (f" -- {r['description']}" if r.get("description") else "")
            for r in resultats
        )

    if action == "publier":
        if not (comportement_id or "").strip():
            return "Erreur : comportement_id manquant."
        if not agent_id:
            return "Erreur : agent non identifié."
        try:
            entree = _publier_comportement_public(agent_id, user_id, comportement_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement_public (publier) : {e}")
            return "Erreur : impossible de publier ce skill, réessaie."
        if entree is None:
            return "Erreur : ce skill est introuvable, ou ne t'appartient pas."
        return f"Skill publié dans le catalogue public [id: {entree['id']}]."

    if action == "activer":
        if not (comportement_public_id or "").strip():
            return "Erreur : comportement_public_id manquant."
        try:
            copie = _activer_comportement_public(comportement_public_id, user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement_public (activer) : {e}")
            return "Erreur : impossible d'activer ce skill, réessaie."
        if copie is None:
            return "Erreur : ce skill public est introuvable."
        return f"Skill « {copie.get('nom') or ''} » activé dans tes skills personnels."

    if action == "retirer":
        if not (comportement_public_id or "").strip():
            return "Erreur : comportement_public_id manquant."
        try:
            ok = _retirer_skill_public(comportement_public_id, user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement_public (retirer) : {e}")
            return "Erreur : impossible de retirer ce skill, réessaie."
        if not ok:
            return "Erreur : ce skill public est introuvable, ou tu n'en es pas l'auteur."
        return "Skill retiré du catalogue public."

    return f"Erreur : action '{action}' inconnue. Actions valides : chercher, publier, activer, retirer."
