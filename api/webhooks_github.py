"""
Webhook GitHub -- écoute l'événement "release" du dépôt clovis-frontend
(où vivent les releases de l'app mobile, voir clovis-mobile.md) pour
prévenir tous les téléphones qu'une nouvelle version est disponible.
Demande Bourama, 05/09/2026 (suite au bug de la page /telecharger qui
restait bloquée sur l'ancienne version pendant 1h).

À configurer côté GitHub : Settings > Webhooks > Add webhook sur le
dépôt clovis-frontend, Payload URL = <URL de ce backend>/api/webhooks/
github-release, Content type = application/json, secret = la même
valeur que GITHUB_WEBHOOK_SECRET_RELEASE ci-dessous, événement = juste
"Releases".

TODO Bourama (variable pas encore définie, comme les autres secrets de
ce fichier) :
- Générer une chaîne aléatoire longue (ex: `openssl rand -hex 32`),
  la mettre à la fois dans les variables d'environnement du backend
  (GITHUB_WEBHOOK_SECRET_RELEASE) ET dans le champ "secret" du webhook
  GitHub. Tant qu'elle est absente, ce webhook refuse tout (401),
  jamais d'exécution non vérifiée.
"""

import hashlib
import hmac
import logging
import os

from fastapi import APIRouter, Request, Response

from core.notifications_push import notifier_nouvelle_version_disponible

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _secret() -> str | None:
    return os.environ.get("GITHUB_WEBHOOK_SECRET_RELEASE")


def _signature_valide(corps_brut: bytes, signature_recue: str | None, secret: str) -> bool:
    """
    GitHub signe le corps brut de la requête en HMAC SHA-256, préfixé
    "sha256=". Comparaison en temps constant (hmac.compare_digest) pour
    éviter une attaque par timing sur la comparaison de signature.
    """
    if not signature_recue or not signature_recue.startswith("sha256="):
        return False
    attendu = "sha256=" + hmac.new(secret.encode(), corps_brut, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, signature_recue)


@router.post("/github-release")
async def webhook_github_release(requete: Request):
    secret = _secret()
    if not secret:
        logging.error("GITHUB_WEBHOOK_SECRET_RELEASE absent -- webhook release refusé.")
        return Response(status_code=500)

    corps_brut = await requete.body()
    signature = requete.headers.get("x-hub-signature-256")
    if not _signature_valide(corps_brut, signature, secret):
        return Response(status_code=401)

    if requete.headers.get("x-github-event") != "release":
        return Response(status_code=204)

    payload = await requete.json()
    release = payload.get("release") or {}

    # "published" couvre une release normale ET une pré-release qui
    # passe en publié -- on écarte explicitement brouillon/pré-release
    # pour ne notifier que sur une VRAIE release publique (même règle
    # que l'API GitHub /releases/latest utilisée par /telecharger et
    # VerificateurMiseAJour.kt, qui les ignore déjà automatiquement).
    if payload.get("action") != "published" or release.get("draft") or release.get("prerelease"):
        return Response(status_code=204)

    version = str(release.get("tag_name", "")).removeprefix("v")
    if not version:
        logging.error("Webhook release GitHub : tag_name absent du payload, notification ignorée.")
        return Response(status_code=204)

    envoyes = notifier_nouvelle_version_disponible(version)
    logging.info(f"Notif nouvelle version {version} envoyée à {envoyes} appareil(s).")
    return Response(status_code=204)
