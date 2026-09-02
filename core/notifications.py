"""
Cree le 02/09/2026, Bourama : centre de notifications Clovis (bouton
cloche dans le header, web + mobile). Voir migration
notifications_ajout_types_clovis_et_colonnes_affichage (Supabase) pour
le contexte complet -- la table notifications existait deja pour un
usage "plateforme createur" (follow/comment/rating/...) jamais expose
dans l'app, ce module ne touche QUE les 4 nouveaux types demandes par
Bourama :
- rappel_echu : voir core/notifications_push.py::traiter_rappels_echus
- action_ia_terminee : voir core/actions_appareil_mobile.py::marquer_resultat
- document_recu_code : voir core/codes_partage.py (entrer_code et
  propager_fichier_range_dossier)
- message_systeme : jamais automatique -- Bourama le declenche lui-meme
  en langage naturel, voir api/notifications.py::envoyer_message_systeme

creer_notification() fait deux choses a chaque appel : (1) insert en
base (persistant, visible au prochain chargement du panneau) ; (2) tente
une diffusion en direct via le canal WebSocket existant
(core/canal_temps_reel.py) si l'utilisateur est connecte -- best effort,
ne bloque jamais l'insertion si personne n'est connecte ou si l'envoi
echoue.
"""

import asyncio
import logging

from api.auth import supabase
from core.canal_temps_reel import notifier_utilisateur

TYPES_VALIDES = {"rappel_echu", "action_ia_terminee", "document_recu_code", "message_systeme"}


def creer_notification(user_id: str, type_notif: str, titre: str, contenu: str | None = None, lien: str | None = None) -> dict | None:
    """
    Insere une notification pour user_id et tente sa diffusion en
    direct. Renvoie la ligne inseree (avec son id), ou None si l'insert
    a echoue (loggue, jamais leve -- un appelant comme
    traiter_rappels_echus ne doit jamais planter a cause d'un probleme
    de notification, le rappel lui-meme a deja ete envoye).

    type_notif doit etre l'un des TYPES_VALIDES ci-dessus -- les anciens
    types (follow, comment, ...) ne passent jamais par cette fonction.
    """
    if type_notif not in TYPES_VALIDES:
        logging.error(f"ERREUR creer_notification : type_notif invalide '{type_notif}' (user={user_id})")
        return None

    try:
        res = (
            supabase.table("notifications")
            .insert(
                {
                    "user_id": user_id,
                    "type": type_notif,
                    "titre": titre,
                    "contenu": contenu,
                    "lien": lien,
                }
            )
            .execute()
        )
        ligne = res.data[0] if res.data else None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (creer_notification user={user_id}, type={type_notif}) : {e}")
        return None

    if ligne is not None:
        try:
            asyncio.create_task(notifier_utilisateur(user_id, ligne))
        except RuntimeError:
            # Pas de boucle asyncio en cours (ex: script/tache synchrone
            # hors du serveur FastAPI) -- la notification reste en base,
            # le panneau la recuperera au prochain chargement, juste pas
            # en direct. Jamais bloquant.
            logging.warning(f"AVERTISSEMENT creer_notification : pas de boucle asyncio pour diffuser en direct (user={user_id})")

    return ligne
