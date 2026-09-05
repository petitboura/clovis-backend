"""
Notifications push (navigateur/mobile), déclenchées soit par l'agent
("préviens-moi dans 3 jours de..."), soit par un événement système
(voir envoyer_notification_push(), utilisable directement par n'importe
quel autre module -- ex. quand une signature Lumin est confirmée).

Gaté par VAPID_PRIVATE_KEY_PEM_B64 + VAPID_PUBLIC_KEY (voir
scripts/generer_cles_vapid.py pour les générer une seule fois).

Trois tables Supabase nécessaires (voir migrations
notifications_push_tables et 2026_08_23_push_natif_appareils_mobiles) :
- abonnements_push : qui est abonné, sur quel navigateur (endpoint +
  clés p256dh/auth fournies par le navigateur, Web Push standard)
- appareils_mobiles_push_tokens : token natif FCM (Android) ou APNs
  (iOS) fourni par l'app mobile Clovis (dépôt clovis-mobile, Lot 3
  Partie 3 -- voir 03-notifications-rappels.md). Un token opaque, rien
  à voir avec le schéma Web Push -- table séparée exprès.
- rappels : les notifications programmées par l'agent, pas encore
  envoyées (déclenche_a, contenu, envoye)

Le PLANIFICATEUR qui vérifie les rappels arrivés à échéance tourne dans
api/main.py (tâche de fond ajoutée au lifespan), pas ici -- ce fichier
ne fait qu'exposer les fonctions, pas la boucle elle-même.

Canal natif (23/08/2026, Lot 3 Partie 3 mobile) : envoyer_notification_push
livre maintenant à TOUS les canaux dont l'utilisateur dispose (navigateur
ET mobile), pas seulement Web Push -- l'app mobile Clovis reçoit donc
automatiquement les mêmes rappels que le navigateur, sans changement côté
appelant (planifier_rappel, proactivité, etc.). Gaté indépendamment par
canal : un utilisateur peut recevoir un rappel sur son téléphone même si
VAPID n'est pas configuré, et inversement.

FCM (Android) : API HTTP v1, authentifiée par compte de service Google
(OAuth2, PAS l'ancienne "server key" -- dépréciée et coupée par Google
en 2024). Variable d'environnement FCM_SERVICE_ACCOUNT_JSON_B64 =
le JSON du compte de service Firebase, encodé en base64 (même
convention que VAPID_PRIVATE_KEY_PEM_B64 ci-dessous). FCM_PROJECT_ID =
l'id du projet Firebase.

APNs (iOS) : API HTTP/2 avec jeton JWT signé ES256 (méthode "token
based provider authentication" recommandée par Apple, pas de
certificat .p12 à renouveler tous les ans). Variables : APNS_KEY_P8_B64
(le fichier .p8 téléchargé sur developer.apple.com, encodé en base64),
APNS_KEY_ID, APNS_TEAM_ID, APNS_BUNDLE_ID (com.clovis.app).

TODO Bourama (aucune de ces valeurs n'existe encore, comme BASE_URL/
SUPABASE_ANON_KEY côté clovis-mobile) :
- Créer un projet Firebase (gratuit), activer Cloud Messaging, générer
  une clé de compte de service (Project Settings > Service accounts >
  Generate new private key) -> FCM_SERVICE_ACCOUNT_JSON_B64/FCM_PROJECT_ID.
- Une fois le compte Apple Developer Program créé (voir ios/README.md
  dans clovis-mobile) : Certificates, Identifiers & Profiles > Keys >
  créer une clé APNs (.p8) -> APNS_KEY_P8_B64/APNS_KEY_ID/APNS_TEAM_ID.
Tant que ces variables sont absentes, le canal correspondant est
silencieusement inactif (voir _fcm_disponible/_apns_disponible) -- ne
bloque jamais le reste (Web Push continue de fonctionner).
"""

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt as pyjwt
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest
from pywebpush import webpush, WebPushException

from api.auth import supabase

VAPID_CLAIMS_SUB = "mailto:contact@maame.africa"  # à changer par une vraie adresse si besoin

FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]
APNS_BUNDLE_ID = "com.clovis.app"  # voir ios/README.md dans clovis-mobile, à réaligner si Bourama change l'id


def _get_secret(cle):
    return os.environ.get(cle)


def notifications_push_disponible() -> bool:
    return bool(_get_secret("VAPID_PRIVATE_KEY_PEM_B64")) and bool(_get_secret("VAPID_PUBLIC_KEY"))


def _fcm_disponible() -> bool:
    return bool(_get_secret("FCM_SERVICE_ACCOUNT_JSON_B64")) and bool(_get_secret("FCM_PROJECT_ID"))


def _apns_disponible() -> bool:
    return bool(_get_secret("APNS_KEY_P8_B64")) and bool(_get_secret("APNS_KEY_ID")) and bool(
        _get_secret("APNS_TEAM_ID")
    )


def un_canal_push_disponible() -> bool:
    """
    Utilisé par le planificateur (api/main.py) pour décider de démarrer
    la boucle de vérification des rappels -- élargi (23/08/2026) pour ne
    plus dépendre uniquement de VAPID, un utilisateur mobile sans
    navigateur abonné doit quand même recevoir ses rappels.
    """
    return notifications_push_disponible() or _fcm_disponible() or _apns_disponible()


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


def enregistrer_token_natif(user_id: str, plateforme: str, token: str, appareil_id: str | None = None) -> None:
    """
    Appelée par api/appareils_mobiles.py quand l'app mobile (Android/iOS)
    obtient ou renouvelle son token FCM/APNs. Upsert sur `token` (unique
    en base) : si le même token revient, on rafraîchit juste user_id/
    plateforme/appareil_id/mis_a_jour_le plutôt que de dupliquer -- un
    token FCM change rarement mais peut être ré-émis par le SDK à tout
    moment.

    `appareil_id` (ajouté le 04/09/2026) : permet à envoyer_action_appareil
    de cibler CE téléphone précis plutôt que de diffuser à tous les
    tokens de l'utilisateur, voir migrations/2026_09_04_appareil_id_ciblage.sql.
    """
    try:
        supabase.table("appareils_mobiles_push_tokens").upsert(
            {
                "user_id": user_id,
                "plateforme": plateforme,
                "token": token,
                "appareil_id": appareil_id,
                "mis_a_jour_le": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="token",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrer_token_natif user={user_id}) : {e}")
        raise


def supprimer_token_natif(user_id: str, token: str) -> None:
    try:
        supabase.table("appareils_mobiles_push_tokens").delete().eq("user_id", user_id).eq(
            "token", token
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (supprimer_token_natif user={user_id}) : {e}")
        raise


def _access_token_fcm() -> str:
    """
    Jeton OAuth2 de courte durée pour l'API FCM HTTP v1, dérivé du
    compte de service (voir FCM_SERVICE_ACCOUNT_JSON_B64 en tête de
    fichier). google-auth gère lui-même le cache/renouvellement tant
    que ce module reste en mémoire, pas la peine de le refaire à la main.
    """
    infos = json.loads(base64.b64decode(_get_secret("FCM_SERVICE_ACCOUNT_JSON_B64")))
    creds = service_account.Credentials.from_service_account_info(infos, scopes=FCM_SCOPES)
    creds.refresh(GoogleAuthRequest())
    return creds.token


def _envoyer_fcm(token: str, titre: str, corps: str, prioritaire: bool = False) -> bool:
    """
    Renvoie False (jamais d'exception) si l'échec signifie "token mort"
    (UNREGISTERED/NOT_FOUND) -- l'appelant nettoie alors la table. Toute
    autre erreur est loggée et traitée comme un échec non-fatal (best
    effort, comme pywebpush ci-dessus).

    Payload en `data` (PAS `notification`) volontairement : un payload
    `notification` est affiché automatiquement par le système quand l'app
    Android est en arrière-plan, SANS passer par onMessageReceived côté
    app -- ce qui empêcherait le repli plein-écran/heads-up géré côté
    client (voir ClovisFirebaseMessagingService.kt dans clovis-mobile).
    Avec `data` seul, onMessageReceived est toujours appelé.
    """
    projet = _get_secret("FCM_PROJECT_ID")
    url = f"https://fcm.googleapis.com/v1/projects/{projet}/messages:send"
    corps_requete = {
        "message": {
            "token": token,
            "data": {"title": titre, "body": corps, "prioritaire": "true" if prioritaire else "false"},
            # Priorité haute : nécessaire pour un rappel qui doit
            # arriver à l'heure même écran éteint (voir doctrine Android
            # Doze/App Standby). Ne garantit PAS l'alerte plein écran --
            # voir 03-notifications-rappels.md, à gérer côté app avec
            # NotificationManager.canUseFullScreenIntent (politique Play
            # depuis janvier 2025, réservée calling/alarm).
            "android": {"priority": "high"},
        }
    }
    try:
        reponse = httpx.post(
            url,
            headers={"Authorization": f"Bearer {_access_token_fcm()}"},
            json=corps_requete,
            timeout=10,
        )
        if reponse.status_code == 200:
            return True
        if reponse.status_code in (404,) or "UNREGISTERED" in reponse.text:
            return False
        logging.error(f"ERREUR FCM (token={token[:12]}...) : {reponse.status_code} {reponse.text}")
        return False
    except Exception as e:
        logging.error(f"ERREUR envoi FCM (token={token[:12]}...) : {e}")
        return False


def _jeton_apns() -> str:
    cle_p8 = base64.b64decode(_get_secret("APNS_KEY_P8_B64")).decode("ascii")
    return pyjwt.encode(
        {"iss": _get_secret("APNS_TEAM_ID"), "iat": int(time.time())},
        cle_p8,
        algorithm="ES256",
        headers={"kid": _get_secret("APNS_KEY_ID")},
    )


def _envoyer_apns(token: str, titre: str, corps: str) -> bool:
    """
    Même contrat de retour que _envoyer_fcm. Serveur de prod Apple par
    défaut (api.push.apple.com) -- pas de bascule sandbox/prod
    automatique ici, à gérer si besoin le jour où un environnement de
    test iOS existe réellement (voir ios/README.md, pas encore le cas).
    """
    url = f"https://api.push.apple.com/3/device/{token}"
    corps_requete = {"aps": {"alert": {"title": titre, "body": corps}, "sound": "default"}}
    try:
        with httpx.Client(http2=True) as client:
            reponse = client.post(
                url,
                headers={
                    "authorization": f"bearer {_jeton_apns()}",
                    "apns-topic": APNS_BUNDLE_ID,
                    "apns-priority": "10",
                },
                json=corps_requete,
                timeout=10,
            )
        if reponse.status_code == 200:
            return True
        if reponse.status_code == 410 or (
            reponse.status_code == 400 and "BadDeviceToken" in reponse.text
        ):
            return False
        logging.error(f"ERREUR APNs (token={token[:12]}...) : {reponse.status_code} {reponse.text}")
        return False
    except Exception as e:
        logging.error(f"ERREUR envoi APNs (token={token[:12]}...) : {e}")
        return False


def _envoyer_fcm_action(token: str, action_id: str, type_action: str) -> bool:
    """
    Meme contrat de retour que _envoyer_fcm, mais payload `data` different
    et volontairement SANS title/body : ce n'est pas une notification a
    montrer a l'utilisateur, c'est un signal d'execution silencieux (voir
    onMessageReceived cote ClovisFirebaseMessagingService.kt, qui doit
    distinguer type="action" de type="rappel" avant d'afficher quoi que
    ce soit). Priorite haute pour les memes raisons Doze/App Standby que
    _envoyer_fcm.
    """
    projet = _get_secret("FCM_PROJECT_ID")
    url = f"https://fcm.googleapis.com/v1/projects/{projet}/messages:send"
    corps_requete = {
        "message": {
            "token": token,
            "data": {"type": "action", "action_id": action_id, "type_action": type_action},
            "android": {"priority": "high"},
        }
    }
    try:
        reponse = httpx.post(
            url,
            headers={"Authorization": f"Bearer {_access_token_fcm()}"},
            json=corps_requete,
            timeout=10,
        )
        if reponse.status_code == 200:
            return True
        if reponse.status_code in (404,) or "UNREGISTERED" in reponse.text:
            return False
        logging.error(f"ERREUR FCM action (token={token[:12]}...) : {reponse.status_code} {reponse.text}")
        return False
    except Exception as e:
        logging.error(f"ERREUR envoi FCM action (token={token[:12]}...) : {e}")
        return False


def _envoyer_apns_action(token: str, action_id: str, type_action: str) -> bool:
    """
    Equivalent iOS de _envoyer_fcm_action : push "content-available"
    silencieux (pas d'alerte/son), l'app recoit l'evenement en tache de
    fond via application(_:didReceiveRemoteNotification:) sans rien
    afficher, exactement comme onMessageReceived cote Android.
    """
    url = f"https://api.push.apple.com/3/device/{token}"
    corps_requete = {
        "aps": {"content-available": 1},
        "type": "action",
        "action_id": action_id,
        "type_action": type_action,
    }
    try:
        with httpx.Client(http2=True) as client:
            reponse = client.post(
                url,
                headers={
                    "authorization": f"bearer {_jeton_apns()}",
                    "apns-topic": APNS_BUNDLE_ID,
                    "apns-priority": "5",  # priorite basse obligatoire pour un push silencieux (regle Apple)
                    "apns-push-type": "background",
                },
                json=corps_requete,
                timeout=10,
            )
        if reponse.status_code == 200:
            return True
        if reponse.status_code == 410 or (
            reponse.status_code == 400 and "BadDeviceToken" in reponse.text
        ):
            return False
        logging.error(f"ERREUR APNs action (token={token[:12]}...) : {reponse.status_code} {reponse.text}")
        return False
    except Exception as e:
        logging.error(f"ERREUR envoi APNs action (token={token[:12]}...) : {e}")
        return False


def envoyer_action_appareil(
    user_id: str, action_id: str, type_action: str, appareil_id_cible: str | None = None
) -> int:
    """
    Reveille le telephone pour qu'il aille chercher une action en attente
    (voir core/actions_appareil_mobile.py). Push SILENCIEUX -- contrairement
    a envoyer_notification_push, ce n'est pas une notification a montrer a
    l'utilisateur mais un signal d'execution. Seulement les canaux natifs
    (FCM/APNs) : Web Push exclu, aucun mecanisme d'execution cote
    navigateur pour ce lot (l'agent ne pilote que le telephone, pas
    l'appareil sur lequel tourne clovis-frontend dans un onglet).

    `appareil_id_cible` (ajoute le 04/09/2026) : si fourni, reveille
    UNIQUEMENT le(s) token(s) de cet appareil precis (indispensable des
    que l'etudiant a deux telephones de la meme plateforme, sinon le
    mauvais telephone recoit le reveil en meme temps que le bon). None =
    diffusion large a tous les tokens de l'utilisateur, comportement
    d'avant cette date -- le filet de secours GET /actions/en-attente
    (lire_actions_en_attente) filtre de toute facon par appareil_id cote
    app, donc un reveil recu par le mauvais appareil reste sans effet.
    """
    if not (_fcm_disponible() or _apns_disponible()):
        raise RuntimeError("Aucun canal natif (FCM/APNs) configure pour les actions appareil.")

    envoyes = 0
    try:
        requete = supabase.table("appareils_mobiles_push_tokens").select(
            "plateforme, token"
        ).eq("user_id", user_id)
        if appareil_id_cible:
            requete = requete.eq("appareil_id", appareil_id_cible)
        res = requete.execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture tokens natifs pour action user={user_id}) : {e}")
        return 0

    for appareil in res.data or []:
        plateforme, token = appareil["plateforme"], appareil["token"]
        ok = False
        if plateforme == "android" and _fcm_disponible():
            ok = _envoyer_fcm_action(token, action_id, type_action)
        elif plateforme == "ios" and _apns_disponible():
            ok = _envoyer_apns_action(token, action_id, type_action)
        else:
            continue  # canal de cette plateforme pas configure, on ignore ce token

        if ok:
            envoyes += 1
        else:
            supprimer_token_natif(user_id, token)

    return envoyes


def envoyer_notification_push(user_id: str, titre: str, corps: str, url: str = None) -> int:
    """
    Envoie une notification push à TOUS les canaux de user_id --
    navigateurs abonnés (Web Push) ET appareils mobiles natifs (FCM/
    APNs, ajouté 23/08/2026 pour clovis-mobile). Réutilisable par
    n'importe quel autre module pour un événement système (ex:
    signature Lumin confirmée, vidéo prête) -- pas seulement par le
    planificateur de rappels.

    Renvoie le nombre d'appareils effectivement notifiés, tous canaux
    confondus. Un canal indisponible (clés absentes) est ignoré
    silencieusement plutôt que de faire échouer les autres -- voir
    un_canal_push_disponible() pour le cas où AUCUN canal n'existe.
    """
    if not un_canal_push_disponible():
        raise RuntimeError("Notifications push indisponibles : aucun canal (VAPID/FCM/APNs) configuré.")

    envoyes = 0

    if notifications_push_disponible():
        try:
            res = supabase.table("abonnements_push").select("endpoint, p256dh, auth").eq(
                "user_id", user_id
            ).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture abonnements user={user_id}) : {e}")
            res = None

        if res is not None:
            payload = json.dumps({"title": titre, "body": corps, "url": url})
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
                        # Abonnement mort (désinstallé/révoqué) -- nettoyage
                        # silencieux, pas une vraie erreur.
                        supprimer_abonnement(user_id, abonnement["endpoint"])
                    else:
                        logging.error(
                            f"ERREUR pywebpush (user={user_id}, endpoint={abonnement['endpoint']}) : {e}"
                        )

    if _fcm_disponible() or _apns_disponible():
        try:
            res_natifs = supabase.table("appareils_mobiles_push_tokens").select(
                "plateforme, token"
            ).eq("user_id", user_id).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture tokens natifs user={user_id}) : {e}")
            res_natifs = None

        if res_natifs is not None:
            for appareil in res_natifs.data or []:
                plateforme, token = appareil["plateforme"], appareil["token"]
                ok = False
                if plateforme == "android" and _fcm_disponible():
                    ok = _envoyer_fcm(token, titre, corps)
                elif plateforme == "ios" and _apns_disponible():
                    ok = _envoyer_apns(token, titre, corps)
                else:
                    continue  # canal de cette plateforme pas configuré, on ignore ce token

                if ok:
                    envoyes += 1
                else:
                    # Token mort (désinstallé, réinitialisé) -- nettoyage
                    # silencieux, même logique que les abonnements Web Push.
                    supprimer_token_natif(user_id, token)

    return envoyes


def notifier_nouvelle_version_disponible(version: str) -> int:
    """
    Diffuse une notification "nouvelle version disponible" à TOUS les
    appareils mobiles enregistrés, tous utilisateurs confondus --
    déclenchée par le webhook GitHub à la publication d'une release
    (voir api/webhooks_github.py). Demande Bourama, 05/09/2026.

    Contrairement à envoyer_notification_push, ne cible pas un user_id
    précis : diffusion large, car aujourd'hui aucune installation ne
    vient du Play Store (pas encore publié faute de frais payés) --
    tous les appareils enregistrés sont donc concernés de la même façon.
    Le jour où l'app est réellement publiée sur le Play Store, il faudra
    revenir ici pour exclure ces appareils (le Play Store gère déjà ses
    propres mises à jour) -- nécessitera d'abord de distinguer le flavor
    (play/externe) dans appareils_mobiles_push_tokens, ce qui n'existe
    pas aujourd'hui (signalé à Bourama, pas encore fait).

    Web Push (abonnements_push) volontairement exclu : notification
    pertinente uniquement pour qui a l'app installée, pas pour un simple
    visiteur du site dans son navigateur.

    Renvoie le nombre d'appareils effectivement notifiés.
    """
    if not (_fcm_disponible() or _apns_disponible()):
        logging.warning(
            "notifier_nouvelle_version_disponible : aucun canal natif (FCM/APNs) configuré, notification ignorée."
        )
        return 0

    titre = "Nouvelle version de Clovis disponible"
    corps = f"La version {version} est prête à être installée."

    try:
        res = supabase.table("appareils_mobiles_push_tokens").select("user_id, plateforme, token").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture de tous les tokens natifs pour notif version) : {e}")
        return 0

    envoyes = 0
    for appareil in res.data or []:
        plateforme, token, user_id = appareil["plateforme"], appareil["token"], appareil["user_id"]
        ok = False
        if plateforme == "android" and _fcm_disponible():
            ok = _envoyer_fcm(token, titre, corps)
        elif plateforme == "ios" and _apns_disponible():
            ok = _envoyer_apns(token, titre, corps)
        else:
            continue  # canal de cette plateforme pas configuré, on ignore ce token

        if ok:
            envoyes += 1
        else:
            supprimer_token_natif(user_id, token)

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
            continue

        # Ajoute le 02/09/2026, Bourama : centre de notifications (bouton
        # cloche). Import local (comme envoyer_action_appareil plus haut
        # dans ce fichier) pour eviter tout risque de cycle d'import
        # entre notifications_push et notifications. Best effort : le
        # rappel lui-meme est deja traite au-dessus, une erreur ici ne
        # doit jamais faire echouer le planificateur.
        try:
            from core.notifications import creer_notification

            creer_notification(rappel["user_id"], "rappel_echu", "Rappel", rappel["contenu"], lien="/rappels")
        except Exception as e:
            logging.error(f"ERREUR creation notification rappel id={rappel['id']} : {e}")

    return traites
