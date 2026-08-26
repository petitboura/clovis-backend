"""
Cree le 24/08/2026, Bourama : Lot 1A Partie 3 (app mobile), brancher le
cerveau -- canal de decision entre l'agent Clovis et l'appareil.

Reprend le meme esprit que core/notifications_push.py (planifier_rappel /
traiter_rappels_echus) mais pour une action a EXECUTER immediatement sur
le telephone (pas une notification a afficher plus tard). Voir
api/appareils_mobiles.py pour les routes qui exposent ceci a l'app.

Cycle de vie d'une action :
1. Un module backend (ex: futur outil agent) appelle creer_action(...) ->
   insert en_attente + push FCM/APNs immediat et silencieux (voir
   notifications_push.envoyer_action_appareil).
2. L'app recoit le push, appelle GET /actions/{id} pour les details.
3. L'app execute, puis rapporte via POST /actions/{id}/resultat.
4. marquer_resultat met a jour le statut ici.

Branche le 26/08/2026 : l'outil agent executer_action_mobile (voir
core/serveur_mcp_generation.py) appelle desormais creer_action() avec
des types_action reels, "dossier_*" (voir
core/dossiers_designes_mobile.py pour le miroir des noms de dossiers
que l'agent peut cibler) et "accessibilite_*" (flavor Android externe
uniquement, cliquer/saisir par texte cible). Liste exacte et forme des
`parametres` documentee dans le docstring de l'outil, seule source de
verite a tenir a jour si de nouveaux types sont ajoutes.

Reste volontairement HORS de ce lot : les actions de session DND/volume
(etat initial capture en memoire cote app, deja identifie comme fragile
si l'app est tuee pendant une session active), a trancher separement
avec Bourama.
"""

import logging
from datetime import datetime, timezone

from api.auth import supabase


def creer_action(user_id: str, type_action: str, parametres: dict) -> str:
    """
    Enregistre une action en attente et reveille le telephone via push.
    Renvoie l'id de l'action creee.
    """
    try:
        res = (
            supabase.table("actions_appareil_mobile")
            .insert(
                {
                    "user_id": user_id,
                    "type_action": type_action,
                    "parametres": parametres,
                    "statut": "en_attente",
                }
            )
            .execute()
        )
        action_id = res.data[0]["id"]
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (creer_action user={user_id}) : {e}")
        raise

    from core.notifications_push import envoyer_action_appareil

    try:
        envoyer_action_appareil(user_id, action_id, type_action)
    except Exception as e:
        # L'action reste en base meme si le push echoue (ex: aucun token
        # enregistre, ou canaux FCM/APNs pas encore configures -- voir
        # TODO Bourama dans notifications_push.py) : l'app la retrouvera
        # au prochain lancement via GET /actions/en-attente.
        logging.error(f"ERREUR envoi push action (action_id={action_id}) : {e}")

    return action_id


def lire_action(action_id: str, user_id: str) -> dict | None:
    try:
        res = (
            supabase.table("actions_appareil_mobile")
            .select("id, type_action, parametres, statut")
            .eq("id", action_id)
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        return res.data
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lire_action id={action_id}) : {e}")
        return None


def lire_actions_en_attente(user_id: str) -> list[dict]:
    """
    Filet de secours si le push n'est pas arrive (app fermee, token pas
    encore configure...) -- l'app peut appeler ceci a chaque ouverture
    pour rattraper les actions manquees.
    """
    try:
        res = (
            supabase.table("actions_appareil_mobile")
            .select("id, type_action, parametres")
            .eq("user_id", user_id)
            .eq("statut", "en_attente")
            .order("cree_le")
            .execute()
        )
        return res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lire_actions_en_attente user={user_id}) : {e}")
        return []


def marquer_resultat(action_id: str, user_id: str, succes: bool, resultat: str = "") -> None:
    try:
        supabase.table("actions_appareil_mobile").update(
            {
                "statut": "executee" if succes else "echouee",
                "resultat": resultat,
                "execute_le": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", action_id).eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer_resultat id={action_id}) : {e}")
        raise
