"""
Limitation de débit (rate limiting) centralisée pour l'API Clovis.

Objectif : empêcher qu'un utilisateur (ou un bot/spam anonyme) puisse
envoyer un nombre illimité de requêtes par minute sur les endpoints
coûteux (chat, génération de contenu), ce qui pourrait faire exploser la
facture des APIs externes (Groq, Gemini, etc.) ou saturer le serveur.

Clé de comptage :
- Si la requête porte un token Supabase valide -> on limite par
  utilisateur (son id), pas par IP. Ça évite qu'un réseau partagé
  (ex: plusieurs élèves sur le wifi d'un lycée) ne partage une même
  limite par erreur.
- Sinon (visiteur anonyme, ex: chat public) -> on limite par IP.

Ce fichier ne contient QUE la configuration du limiteur. Les limites
précises par endpoint sont posées directement sur chaque route via le
décorateur @limiteur.limit(...).
"""

import logging

from fastapi import Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address


def _cle_limitation(request: Request) -> str:
    """
    Détermine la clé de comptage pour une requête donnée.

    On essaie de résoudre l'utilisateur directement depuis le token
    Supabase de l'en-tête Authorization (indépendamment de la logique
    d'auth propre à chaque route, pour ne pas dépendre de l'ordre
    d'exécution des dépendances FastAPI). Si absent ou invalide -> IP.

    Le résultat est mis en cache sur request.state pour ne faire cet
    appel qu'une seule fois même si plusieurs limites s'appliquent à la
    même requête.
    """
    if hasattr(request.state, "_cle_limitation_cache"):
        return request.state._cle_limitation_cache

    cle = None
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        if token:
            try:
                from api.auth import supabase  # import tardif : évite un cycle avec api.auth

                reponse = supabase.auth.get_user(token)
                if reponse and reponse.user:
                    cle = f"user:{reponse.user.id}"
            except Exception:
                # Token invalide/expiré : on retombe simplement sur l'IP,
                # la 401 sera de toute façon levée plus loin par la route.
                pass

    if cle is None:
        cle = f"ip:{get_remote_address(request)}"

    request.state._cle_limitation_cache = cle
    return cle


limiteur = Limiter(key_func=_cle_limitation)


def gestionnaire_limite_depassee(request: Request, exc):
    """
    Gestionnaire d'exception pour un dépassement de limite : journalise
    (pour repérer les abus/patterns suspects) puis renvoie la réponse
    429 standard de slowapi.
    """
    logging.warning(
        f"RATE LIMIT depasse -- cle={_cle_limitation(request)} "
        f"chemin={request.url.path}"
    )
    return _rate_limit_exceeded_handler(request, exc)
