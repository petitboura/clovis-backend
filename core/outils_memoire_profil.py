"""
Outils MCP liés à l'utilisateur : mémoire de conversation, historique,
profil, messagerie interne (envoyer_message) et rappels programmés
(planifier_rappel).

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code.

Clovis (12/08) : memoire/profil/RAG/matiere ne sont plus pre-fetches et
injectes systematiquement dans le system prompt (voir core/main.py,
_construire_system_prompt) -- ce sont maintenant des outils que le
modele appelle lui-meme s'il juge pertinent, au meme titre que les
outils generation/bibliotheque ci-dessus.
"""

import json
import logging

from api.roles import (
    resoudre_destinataire_autorise as _resoudre_destinataire_autorise,
    _inserer_message,
)
from core.notifications_push import (
    planifier_rappel as _planifier_rappel,
    un_canal_push_disponible,
)

from core.outils_generation_commun import mcp_generation, Context, _supabase_memoire



@mcp_generation.tool()
def gerer_memoire_utilisateur(
    action: str,
    ctx: Context,
    champs_json: str = "",
) -> str:
    """
    Gère la mémoire long-terme structurée de CET utilisateur, valable
    d'une conversation à l'autre (préférences, matières suivies,
    difficultés récurrentes, projets en cours, etc.), consolidé le
    26/08, un seul outil, plusieurs actions.

    `action` doit être l'une de :
    - "consulter" : renvoie ce qui est déjà su de cet utilisateur, sous
      forme de JSON (peut être vide si rien n'a encore été noté). À
      utiliser au début d'une conversation si ça peut aider à mieux
      répondre, ou dès qu'un élément de contexte passé serait utile.
      Aucun paramètre.
    - "mettre_a_jour" : note ou met à jour un ou plusieurs éléments, à
      utiliser dès que tu apprends quelque chose d'utile à retenir pour
      les prochaines conversations (préférence, difficulté récurrente,
      projet en cours, etc.). Le schéma est libre : garde les clés déjà
      utilisées si elles collent (ex. "profil_personnel",
      "preferences_pedagogiques", "matieres_ou_sujets",
      "objectifs_et_projets", "points_de_continuite"), ou crée-en de
      nouvelles si aucune ne convient. Paramètre `champs_json` : objet
      JSON, ex. '{"preferences_pedagogiques": {"style_explication":
      "avec des exemples concrets"}}', fusionné avec la mémoire
      existante (les clés de premier niveau fournies remplacent leur
      ancienne valeur, le reste est conservé tel quel). N'écris ici que
      ce qui a une vraie valeur à long terme, pas le contenu d'un seul
      message.
    - "effacer" : efface DÉFINITIVEMENT le résumé long-terme ("oublie
      tout ce que tu sais de moi"). Aucun paramètre. SENSIBLE : demande
      toujours confirmation à l'utilisateur avant d'être exécuté, quelle
      que soit la formulation de sa demande.
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    if action == "consulter":
        try:
            res = (
                _supabase_memoire.table("conversation_summaries")
                .select("donnees")
                .eq("user_id", user_id)
                .maybe_single()
                .execute()
            )
            donnees = (res.data or {}).get("donnees") or {}
            if not donnees:
                return "Rien en mémoire pour cet utilisateur pour l'instant."
            return json.dumps(donnees, ensure_ascii=False)
        except Exception as e:
            logging.error(f"ERREUR gerer_memoire_utilisateur (consulter) : {e}")
            return "Erreur : impossible de consulter la mémoire, réessaie."

    if action == "mettre_a_jour":
        try:
            try:
                patch = json.loads(champs_json)
            except Exception:
                return "Erreur : champs_json doit être un objet JSON valide."
            if not isinstance(patch, dict):
                return "Erreur : champs_json doit être un objet JSON (pas une liste ou une valeur simple)."

            res = (
                _supabase_memoire.table("conversation_summaries")
                .select("donnees")
                .eq("user_id", user_id)
                .maybe_single()
                .execute()
            )
            actuel = (res.data or {}).get("donnees") or {}
            actuel.update(patch)

            _supabase_memoire.table("conversation_summaries").upsert(
                {"user_id": user_id, "donnees": actuel}, on_conflict="user_id"
            ).execute()
            return "Mémoire mise à jour."
        except Exception as e:
            logging.error(f"ERREUR gerer_memoire_utilisateur (mettre_a_jour) : {e}")
            return "Erreur : la mise à jour de la mémoire a échoué, réessaie."

    if action == "effacer":
        try:
            _supabase_memoire.table("conversation_summaries").delete().eq("user_id", user_id).execute()
        except Exception as e:
            logging.error(f"ERREUR gerer_memoire_utilisateur (effacer) : {e}")
            return "Erreur : impossible d'effacer la mémoire, réessaie."
        return "Mémoire effacée."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : consulter, "
        "mettre_a_jour, effacer."
    )


@mcp_generation.tool()
def lister_conversations_historique(ctx: Context) -> str:
    """
    Liste les fils de discussion distincts entre CET utilisateur et
    Clovis (section "Historique"), le plus récemment actif en premier.
    Renvoie pour chacun : conversation_id ("legacy" pour les échanges
    d'avant l'historique par fil), titre (début du premier message),
    dernière activité.
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    agent_id = requete.query_params.get("agent_id")
    if not user_id or not agent_id:
        return "Erreur : impossible d'identifier l'utilisateur ou l'agent."
    try:
        lignes = (
            _supabase_memoire.table("historique_conversations")
            .select("conversation_id, role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .order("created_at")
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR outil lister_conversations_historique : {e}")
        return "Erreur : impossible de charger l'historique, réessaie."

    if not lignes:
        return "Aucune conversation dans l'historique pour l'instant."

    fils: dict = {}
    for ligne in lignes:
        cle = ligne["conversation_id"] or "legacy"
        if cle not in fils:
            fils[cle] = {"premier_message_user": None, "derniere_activite": ligne["created_at"]}
        if ligne["role"] == "user" and fils[cle]["premier_message_user"] is None:
            fils[cle]["premier_message_user"] = ligne["content"]
        fils[cle]["derniere_activite"] = ligne["created_at"]

    resultats = []
    for cle, fil in fils.items():
        if cle == "legacy":
            titre = "Avant l'historique par conversation"
        else:
            titre = (fil["premier_message_user"] or "(sans titre)")[:80]
        resultats.append((fil["derniere_activite"], f"- {titre} | dernière activité : {fil['derniere_activite']} [conversation_id: {cle}]"))
    resultats.sort(reverse=True)
    return "\n".join(l for _, l in resultats)


@mcp_generation.tool()
def lire_conversation_historique(conversation_id: str, ctx: Context) -> str:
    """
    Contenu complet d'un fil de discussion précis entre CET utilisateur
    et Clovis, à partir de son conversation_id (voir
    lister_conversations_historique -- utilise littéralement "legacy"
    pour recharger les échanges d'avant l'historique par fil).
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    agent_id = requete.query_params.get("agent_id")
    if not user_id or not agent_id:
        return "Erreur : impossible d'identifier l'utilisateur ou l'agent."
    try:
        req = (
            _supabase_memoire.table("historique_conversations")
            .select("role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
        )
        if conversation_id == "legacy":
            req = req.is_("conversation_id", "null")
        else:
            req = req.eq("conversation_id", conversation_id)
        lignes = req.order("created_at").execute().data or []
    except Exception as e:
        logging.error(f"ERREUR outil lire_conversation_historique : {e}")
        return "Erreur : impossible de charger cette conversation, réessaie."

    if not lignes:
        return "Cette conversation est introuvable ou vide."

    return "\n".join(f"[{l['role']}] {l['content']}" for l in lignes)


@mcp_generation.tool()
def consulter_profil_utilisateur(ctx: Context) -> str:
    """
    Consulte le profil connu de CET utilisateur pour Clovis (données
    déjà extraites au fil des conversations : qui il est, son contexte
    scolaire, etc.). À utiliser si ça peut aider à personnaliser ta
    réponse. Renvoie un JSON (peut être vide).
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Aucun profil disponible."
        res = (
            _supabase_memoire.table("agent_user_profiles")
            .select("donnees")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .maybe_single()
            .execute()
        )
        donnees = (res.data or {}).get("donnees") or {}
        if not donnees:
            return "Rien dans le profil de cet utilisateur pour l'instant."
        return json.dumps(donnees, ensure_ascii=False)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_profil_utilisateur : {e}")
        return "Erreur : impossible de consulter le profil, réessaie."



# Section entière d'outils "Programme académique" (lister_mes_programmes /
# consulter_programme / ajouter_programme / modifier_programme /
# supprimer_programme / ajouter_matiere / ... / consulter_matiere_programme /
# consulter_chapitre_programme / consulter_examens_programme) retirée le
# 29/08/2026 (demande Bourama) : dépendait de core/programme_llm.py et
# core/programme_ecriture.py, désormais isolés et désactivés -- voir
# _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md. NE JAMAIS
# réintroduire ces outils sans redemander la spécification à Bourama.

@mcp_generation.tool()
def mettre_a_jour_profil_utilisateur(champs_json: str, ctx: Context) -> str:
    """
    Met à jour le profil de CET utilisateur dès que tu apprends une
    information utile à retenir sur qui il est (pas sur ce qu'il sait
    ou apprend, ça c'est la mémoire, voir gerer_memoire_utilisateur
    action "mettre_a_jour").
    Schéma libre, mêmes règles que pour la mémoire : `champs_json` est
    un objet JSON fusionné avec le profil existant.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Erreur : impossible d'identifier l'utilisateur pour mettre à jour le profil."
        try:
            patch = json.loads(champs_json)
        except Exception:
            return "Erreur : champs_json doit être un objet JSON valide."
        if not isinstance(patch, dict):
            return "Erreur : champs_json doit être un objet JSON (pas une liste ou une valeur simple)."

        res = (
            _supabase_memoire.table("agent_user_profiles")
            .select("donnees")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .maybe_single()
            .execute()
        )
        actuel = (res.data or {}).get("donnees") or {}
        actuel.update(patch)

        _supabase_memoire.table("agent_user_profiles").upsert(
            {"user_id": user_id, "agent_id": agent_id, "donnees": actuel}, on_conflict="agent_id,user_id"
        ).execute()
        return "Profil mis à jour."
    except Exception as e:
        logging.error(f"ERREUR outil mettre_a_jour_profil_utilisateur : {e}")
        return "Erreur : la mise à jour du profil a échoué, réessaie."



# Enregistré conditionnellement, gate par un_canal_push_disponible()
# (voir notifications_push.py) -- élargi le 23/08/2026 (Lot 3 Partie 3
# mobile) : VAPID (navigateur) OU FCM (Android) OU APNs (iOS), le
# rappel part maintenant vers tous les canaux dont dispose
# l'utilisateur, l'outil IA lui-même ne change pas. Outil de ce fichier
# qui a besoin de connaître l'identité de l'appelant (user_id/agent_id) :
# récupérés via ctx.request_context.request.query_params, transmis dans
# l'URL par _url_generation() (registre_outils.py) -- même mécanique
# reprise par envoyer_message ci-dessous. NON TESTÉ EN CONDITIONS
# RÉELLES : si ça échoue au premier essai, vérifier en premier que
# request_context.request est bien accessible dans ce mode
# (stateless_http) -- c'est le point d'incertitude documenté ici.
if un_canal_push_disponible():
    @mcp_generation.tool()
    def planifier_rappel(contenu: str, dans_minutes: int, ctx: Context) -> str:
        """
        Planifie une notification push à envoyer à l'utilisateur après
        un délai donné (ex: "préviens-moi dans 3 jours de réviser").
        `contenu` est le texte de la notification, `dans_minutes` le
        délai en minutes (ex: 4320 pour 3 jours).
        """
        try:
            requete = ctx.request_context.request
            user_id = requete.query_params.get("user_id")
            agent_id = requete.query_params.get("agent_id")
            if not user_id:
                return "Erreur : impossible d'identifier l'utilisateur pour ce rappel."
            _planifier_rappel(user_id, agent_id, contenu, dans_minutes)
            return f"Rappel programmé dans {dans_minutes} minutes."
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : la planification du rappel a échoué, réessaie."


# Ajouté le 2026-08-04 (demande Bourama, hiérarchie de rôles) : contrairement
# aux autres outils de ce fichier, PAS de gate conditionnel -- toujours
# enregistré, car actif d'office sur les IA auto-créées à choix de rôle
# (voir _creer_agent_minimal dans api/roles.py) et sans dépendance à une
# clé API externe. Renvoie toujours du texte (jamais de génération de
# fichier) : le round-trip vers le modèle (standard pour tous les outils
# depuis le 15/08, voir registre_outils.py) reste de toute façon
# nécessaire pour qu'il relaie une erreur de destinataire ambigu/introuvable
# à l'utilisateur.
#
# TESTÉ le 2026-08-04 (tâche E) : voir test_envoyer_message_manuel.py,
# qui construit une vraie requête Starlette (même mécanisme que
# mcp/server/_streamable_http_modern.py) pour confirmer que
# ctx.request_context.request.query_params est bien accessible en mode
# stateless_http -- c'était le point d'incertitude non vérifié documenté
# ici avant cette date (voir aussi planifier_rappel juste au-dessus, qui
# repose sur le même mécanisme et reste à couvrir de la même façon).
@mcp_generation.tool()
def envoyer_message(nom_destinataire: str, contenu: str, ctx: Context) -> str:
    """
    Envoie un message direct à une personne autorisée de ta hiérarchie
    (ton établissement, ton enseignant, tes étudiants, ou un autre
    étudiant de ton établissement selon ton rôle). `nom_destinataire`
    est le nom affiché de la personne à qui écrire, `contenu` le texte
    du message.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        if not user_id:
            return "Erreur : impossible d'identifier l'expéditeur pour ce message."
        if not contenu.strip():
            return "Erreur : le message est vide."

        destinataire_id, erreur = _resoudre_destinataire_autorise(user_id, nom_destinataire)
        if erreur:
            return erreur

        _inserer_message(user_id, destinataire_id, contenu)
        return f"Message envoyé à {nom_destinataire}."
    except Exception as e:
        logging.error(f"ERREUR outil generation (envoyer_message) : {e}")
        return "Erreur : l'envoi du message a échoué, réessaie."
