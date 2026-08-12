"""
Notifications push (navigateur/mobile), déclenchées soit par l'agent
("préviens-moi dans 3 jours de..."), soit par un événement système
(voir envoyer_notification_push(), utilisable directement par n'importe
quel autre module -- ex. quand une signature Lumin est confirmée).

Gaté par VAPID_PRIVATE_KEY_PEM_B64 + VAPID_PUBLIC_KEY (voir
scripts/generer_cles_vapid.py pour les générer une seule fois).

Deux tables Supabase nécessaires (voir migration
notifications_push_tables) :
- abonnements_push : qui est abonné, sur quel appareil (endpoint +
  clés p256dh/auth fournies par le navigateur)
- rappels : les notifications programmées par l'agent, pas encore
  envoyées (déclenche_a, contenu, envoye)

Le PLANIFICATEUR qui vérifie les rappels arrivés à échéance tourne dans
api/main.py (tâche de fond ajoutée au lifespan), pas ici -- ce fichier
ne fait qu'exposer les fonctions, pas la boucle elle-même.
"""

import base64
import logging
import os
from datetime import datetime, timedelta, timezone

from pywebpush import webpush, WebPushException

from api.auth import supabase

VAPID_CLAIMS_SUB = "mailto:contact@maame.africa"  # à changer par une vraie adresse si besoin


def _get_secret(cle):
    return os.environ.get(cle)


def notifications_push_disponible() -> bool:
    return bool(_get_secret("VAPID_PRIVATE_KEY_PEM_B64")) and bool(_get_secret("VAPID_PUBLIC_KEY"))


def cle_publique_vapid() -> str:
    """Utilisé par la route REST que le frontend appelle pour s'abonner."""
    return _get_secret("VAPID_PUBLIC_KEY") or ""


def _pem_prive() -> str:
    b64 = _get_secret("VAPID_PRIVATE_KEY_PEM_B64")
    return base64.b64decode(b64).decode("ascii")


def enregistrer_abonnement(user_id: str, subscription_info: dict) -> None:
    """
    `subscription_info` : l'objet renvoyé tel quel par
    `PushManager.subscribe()` côté navigateur ({"endpoint": ...,
    "keys": {"p256dh": ..., "auth": ...}}).

    Upsert sur (user_id, endpoint) : un même utilisateur peut avoir
    plusieurs appareils abonnés, mais pas de doublon pour le même
    endpoint.
    """
    try:
        supabase.table("abonnements_push").upsert(
            {
                "user_id": user_id,
                "endpoint": subscription_info["endpoint"],
                "p256dh": subscription_info["keys"]["p256dh"],
                "auth": subscription_info["keys"]["auth"],
            },
            on_conflict="user_id,endpoint",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrer_abonnement user={user_id}) : {e}")
        raise


def supprimer_abonnement(user_id: str, endpoint: str) -> None:
    try:
        supabase.table("abonnements_push").delete().eq("user_id", user_id).eq(
            "endpoint", endpoint
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (supprimer_abonnement user={user_id}) : {e}")
        raise


def envoyer_notification_push(user_id: str, titre: str, corps: str, url: str = None) -> int:
    """
    Envoie une notification push à TOUS les appareils abonnés de
    user_id. Réutilisable par n'importe quel autre module pour un
    événement système (ex: signature Lumin confirmée, vidéo prête) --
    pas seulement par le planificateur de rappels.

    Renvoie le nombre d'appareils effectivement notifiés. Les
    abonnements expirés/invalides (410/404 renvoyés par le navigateur)
    sont automatiquement supprimés de abonnements_push -- pas une
    erreur à remonter, juste du nettoyage normal.
    """
    if not notifications_push_disponible():
        raise RuntimeError("Notifications push indisponibles : clés VAPID non configurées.")

    try:
        res = supabase.table("abonnements_push").select("endpoint, p256dh, auth").eq(
            "user_id", user_id
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture abonnements user={user_id}) : {e}")
        raise

    import json
    payload = json.dumps({"title": titre, "body": corps, "url": url})

    envoyes = 0
    for abonnement in res.data or []:
        subscription_info = {
            "endpoint": abonnement["endpoint"],
            "keys": {"p256dh": abonnement["p256dh"], "auth": abonnement["auth"]},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=_pem_prive(),
                vapid_claims={"sub": VAPID_CLAIMS_SUB},
            )
            envoyes += 1
        except WebPushException as e:
            code = e.response.status_code if e.response is not None else None
            if code in (404, 410):
                # Abonnement mort (l'utilisateur a désinstallé/révoqué) --
                # nettoyage silencieux, pas une vraie erreur.
                supprimer_abonnement(user_id, abonnement["endpoint"])
            else:
                logging.error(f"ERREUR pywebpush (user={user_id}, endpoint={abonnement['endpoint']}) : {e}")

    return envoyes


def planifier_rappel(user_id: str, agent_id: str, contenu: str, dans_minutes: int) -> int:
    """
    Enregistre un rappel à envoyer plus tard (voir le planificateur dans
    api/main.py, qui vérifie cette table périodiquement). Renvoie
    l'id du rappel créé.
    """
    declenche_a = (datetime.now(timezone.utc) + timedelta(minutes=int(dans_minutes))).isoformat()
    try:
        res = (
            supabase.table("rappels")
            .insert(
                {
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "contenu": contenu,
                    "declenche_a": declenche_a,
                    "envoye": False,
                },
            )
            .execute()
        )
        return res.data[0]["id"]
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (planifier_rappel user={user_id}) : {e}")
        raise


def traiter_rappels_echus() -> int:
    """
    Appelée périodiquement par le planificateur (api/main.py). Cherche
    les rappels dont l'échéance est passée et pas encore envoyés, les
    envoie, les marque comme envoyés. Renvoie le nombre traité.
    """
    maintenant = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            supabase.table("rappels")
            .select("id, user_id, contenu")
            .eq("envoye", False)
            .lte("declenche_a", maintenant)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture rappels échus) : {e}")
        return 0

    traites = 0
    for rappel in res.data or []:
        try:
            envoyer_notification_push(rappel["user_id"], "Rappel", rappel["contenu"])
            supabase.table("rappels").update({"envoye": True}).eq("id", rappel["id"]).execute()
            traites += 1
        except Exception as e:
            logging.error(f"ERREUR traitement rappel id={rappel['id']} : {e}")

    return traites
