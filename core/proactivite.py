"""
Proactivité (25/07) : un agent peut relancer un utilisateur inactif de sa
propre initiative, sans que celui-ci n'ait rien demandé -- l'inverse du
planificateur de rappels (core/notifications_push.py), qui n'agit que sur
demande explicite ("préviens-moi dans 3 jours de...").

Générique par construction (demande explicite de Bourama, 25/07) : ce
module ne connaît RIEN du domaine d'un agent en particulier (tutorat,
coaching business, écriture créative...) -- c'est l'agent lui-même, via
son propre prompt système + la conversation passée, qui juge de la
pertinence d'une relance (voir _decider_relance). Ce fichier ne fait que
détecter l'inactivité et déclencher la décision, jamais le contenu.

Configurable par le créateur (25/07, colonnes agents.proactivite_* -- voir
migration proactivite_config_createur) : QUAND (proactivite_delai_jours),
à quelle fréquence max (proactivite_cooldown_jours), et POURQUOI/COMMENT
(proactivite_instructions, texte libre comme system_prompt -- si vide,
INSTRUCTION_PROACTIVITE_DEFAUT s'applique).

Boucle appelante : voir api/main.py (_boucle_planificateur_proactivite).
Tourne beaucoup moins souvent que celle des rappels -- l'inactivité se
mesure en jours, pas en minutes.
"""

import logging
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from groq import Groq

from api.auth import supabase
from core.main import get_secret, GROQ_PRIMARY, _construire_system_prompt, _ressemble_a_du_json_casse
from core.notifications_push import envoyer_notification_push, notifications_push_disponible

COOLDOWN_VERIFICATION = timedelta(hours=6)  # ne re-vérifie pas une paire (agent, utilisateur) plus souvent que ça
NB_MESSAGES_CONTEXTE = 10  # historique récent donné au modèle pour juger (réduit du 25/07 -- voir TAILLE_MAX_MESSAGE)
TAILLE_MAX_MESSAGE = 500  # troncature (25/07) : évite de dépasser la limite TPM Groq sur de longs échanges

SENTINELLE_AUCUNE_RELANCE = "AUCUNE_RELANCE"

# Utilisé UNIQUEMENT si le créateur n'a rien écrit dans
# agents.proactivite_instructions -- sinon son texte remplace ce
# paragraphe de critères (mais le contrat technique de la sentinelle,
# lui, reste toujours identique, voir _construire_instruction_proactivite).
INSTRUCTION_PROACTIVITE_DEFAUT = (
    "Ne relance QUE s'il y a une vraie raison concrète ancrée dans la "
    "conversation (un objectif mentionné, une échéance, quelque chose "
    "resté inachevé) -- jamais une relance générique du style \"tu es "
    "là ?\" ou \"des nouvelles ?\" sans contenu réel."
)


def _construire_instruction_proactivite(instructions_createur: str | None) -> str:
    criteres = (instructions_createur or "").strip() or INSTRUCTION_PROACTIVITE_DEFAUT
    return f"""

DÉCISION DE RELANCE PROACTIVE : la personne ci-dessus ne t'a pas écrit
depuis un moment (voir la conversation). C'est TOI qui prends
l'initiative de la contacter, elle n'a rien demandé.

CRITÈRES DÉFINIS PAR LE CRÉATEUR DE CET AGENT POUR DÉCIDER D'UNE RELANCE
(quand, pourquoi, comment, sur quelle base) :
{criteres}

- Si une relance est pertinente selon ces critères : réponds UNIQUEMENT
  avec le message à envoyer directement à la personne (rien d'autre
  autour, pas de méta-commentaire).
- Si aucune relance n'est pertinente : réponds EXACTEMENT et UNIQUEMENT
  "{SENTINELLE_AUCUNE_RELANCE}", rien d'autre, aucune ponctuation en plus.
"""


def _marquer_verification(user_id: str, agent_id: str) -> None:
    """
    UPDATE puis INSERT si absent (PAS un upsert naïf) : un upsert sur les
    seules colonnes fournies écraserait derniere_relance_envoyee_a à NULL
    si la ligne existe déjà, puisque cette colonne ne serait pas incluse
    dans le payload.
    """
    maintenant = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            supabase.table("relances_proactives")
            .update({"derniere_verification_a": maintenant})
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .execute()
        )
        if not res.data:
            supabase.table("relances_proactives").insert(
                {"user_id": user_id, "agent_id": agent_id, "derniere_verification_a": maintenant}
            ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer vérification relance, user={user_id}, agent={agent_id}) : {e}")


def _marquer_relance_envoyee(user_id: str, agent_id: str) -> None:
    try:
        supabase.table("relances_proactives").update(
            {"derniere_relance_envoyee_a": datetime.now(timezone.utc).isoformat()}
        ).eq("user_id", user_id).eq("agent_id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer relance envoyée, user={user_id}, agent={agent_id}) : {e}")


def _decider_relance(
    agent_id: str, user_id: str, instructions_createur: str | None, propager_erreurs: bool = False
) -> str | None:
    """
    Laisse l'agent (son propre prompt système + la conversation passée +
    les critères du créateur, voir _construire_instruction_proactivite)
    juger de la pertinence d'une relance. Renvoie le message à envoyer,
    ou None si aucune relance n'est pertinente.

    propager_erreurs=False (par défaut, utilisé par le planificateur en
    tâche de fond) : fail-silent, une relance ratée n'est jamais grave.
    propager_erreurs=True (utilisé par l'endpoint de test, voir
    api/agents.py:tester_proactivite) : les erreurs remontent au lieu
    d'être avalées -- sinon un échec technique (ex: quota Groq dépassé,
    constaté le 25/07) est indiscernable d'une vraie décision "je ne
    relance pas" côté interface.
    """
    try:
        historique = (
            supabase.table("historique_conversations")
            .select("role, content, created_at")
            .eq("agent_id", agent_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(NB_MESSAGES_CONTEXTE)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture historique décision relance, user={user_id}, agent={agent_id}) : {e}")
        if propager_erreurs:
            raise
        return None

    messages_recents = list(reversed(historique.data or []))
    if not messages_recents:
        return None

    try:
        # message_utilisateur="" : pas de nouveau message pour le RAG ici,
        # on garde quand même persona + mémoire + profil (voir
        # _construire_system_prompt dans core/main.py, réutilisée telle
        # quelle pour rester cohérent avec le vrai chat).
        system_final = _construire_system_prompt("", agent_id, user_id, longueur_reponse="courte")
    except Exception as e:
        logging.error(f"ERREUR construction prompt (décision relance, agent={agent_id}) : {e}")
        if propager_erreurs:
            raise
        return None
    system_final += _construire_instruction_proactivite(instructions_createur)

    messages = [{"role": "system", "content": system_final}]
    for m in messages_recents:
        role = "assistant" if m["role"] == "assistant" else "user"
        contenu = m["content"] or ""
        if len(contenu) > TAILLE_MAX_MESSAGE:
            contenu = contenu[:TAILLE_MAX_MESSAGE] + "… (tronqué)"
        messages.append({"role": role, "content": contenu})
    messages.append(
        {"role": "user", "content": "[Aucun nouveau message -- décide si tu relances, selon les consignes ci-dessus.]"}
    )

    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=20.0)
        completion = client.chat.completions.create(model=GROQ_PRIMARY, messages=messages)
        texte = (completion.choices[0].message.content or "").strip()
    except Exception as e:
        logging.error(f"ERREUR Groq (décision relance, agent={agent_id}, user={user_id}) : {e}")
        if propager_erreurs:
            raise
        return None

    if not texte or texte.strip().upper() == SENTINELLE_AUCUNE_RELANCE:
        return None
    if _ressemble_a_du_json_casse(texte):
        logging.warning(f"Relance ignorée (réponse suspecte, agent={agent_id}, user={user_id}).")
        return None
    return texte


def verifier_relances_proactives() -> int:
    """
    Appelée périodiquement (voir api/main.py). Pour chaque agent avec
    proactivite_active=true, cherche les utilisateurs inactifs depuis
    SEUIL_INACTIVITE, ayant activé notifications_proactives_actives, pas
    vérifiés depuis COOLDOWN_VERIFICATION ni relancés depuis
    COOLDOWN_RELANCE -- puis laisse l'agent décider (_decider_relance).
    Renvoie le nombre de relances effectivement envoyées.
    """
    if not notifications_push_disponible():
        return 0

    try:
        agents_actifs = (
            supabase.table("agents")
            .select("id, nom, proactivite_delai_jours, proactivite_cooldown_jours, proactivite_instructions")
            .eq("proactivite_active", True)
            .eq("actif", True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents proactifs) : {e}")
        return 0

    maintenant = datetime.now(timezone.utc)
    envoyees = 0

    for agent in agents_actifs.data or []:
        agent_id = agent["id"]
        delai_jours = agent.get("proactivite_delai_jours") or 4
        cooldown_jours = agent.get("proactivite_cooldown_jours") or 7
        instructions_createur = agent.get("proactivite_instructions")
        seuil_inactivite = (maintenant - timedelta(days=delai_jours)).isoformat()
        cooldown_relance = timedelta(days=cooldown_jours)

        # Dernier message (question OU réponse) par utilisateur pour cet
        # agent. NOTE : lit jusqu'à 500 lignes récentes puis déduplique
        # côté Python -- suffisant au volume actuel (voir commentaire de
        # migration), à revoir avec une vraie colonne "dernière activité"
        # si le nombre de messages par agent grossit beaucoup.
        try:
            derniers_messages = (
                supabase.table("historique_conversations")
                .select("user_id, created_at")
                .eq("agent_id", agent_id)
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture historique agent={agent_id}) : {e}")
            continue

        dernier_message_par_utilisateur = {}
        for ligne in derniers_messages.data or []:
            uid = ligne["user_id"]
            if uid not in dernier_message_par_utilisateur:
                dernier_message_par_utilisateur[uid] = ligne["created_at"]

        for user_id, dernier_message_a in dernier_message_par_utilisateur.items():
            if dernier_message_a > seuil_inactivite:
                continue  # encore actif, rien à faire

            try:
                profil = (
                    supabase.table("profiles")
                    .select("notifications_proactives_actives")
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (lecture profil user={user_id}) : {e}")
                continue
            if not profil.data or not profil.data.get("notifications_proactives_actives"):
                continue  # opt-out côté utilisateur -- on n'insiste jamais

            try:
                suivi = (
                    supabase.table("relances_proactives")
                    .select("derniere_verification_a, derniere_relance_envoyee_a")
                    .eq("user_id", user_id)
                    .eq("agent_id", agent_id)
                    .maybe_single()
                    .execute()
                )
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (lecture suivi relance user={user_id}, agent={agent_id}) : {e}")
                suivi = None

            if suivi and suivi.data:
                derniere_verif = suivi.data.get("derniere_verification_a")
                if derniere_verif and derniere_verif > (maintenant - COOLDOWN_VERIFICATION).isoformat():
                    continue  # déjà vérifié récemment, pas la peine de re-décider
                derniere_relance = suivi.data.get("derniere_relance_envoyee_a")
                if derniere_relance and derniere_relance > (maintenant - cooldown_relance).isoformat():
                    _marquer_verification(user_id, agent_id)
                    continue  # relancé récemment, on laisse respirer

            message_relance = _decider_relance(agent_id, user_id, instructions_createur)
            _marquer_verification(user_id, agent_id)

            if not message_relance:
                continue

            try:
                envoyer_notification_push(user_id, agent.get("nom") or "Nouveau message", message_relance)
                supabase.table("conversations").insert(
                    {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": message_relance}
                ).execute()
                supabase.table("historique_conversations").insert(
                    {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": message_relance}
                ).execute()
                _marquer_relance_envoyee(user_id, agent_id)
                envoyees += 1
            except Exception as e:
                logging.error(f"ERREUR envoi relance proactive (user={user_id}, agent={agent_id}) : {e}")

    return envoyees
