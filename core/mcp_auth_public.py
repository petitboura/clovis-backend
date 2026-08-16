"""
Vérification des jetons OAuth pour les serveurs MCP PUBLICS de Clovis
(core/serveur_mcp_public.py, core/serveur_mcp_espace.py -- tout serveur
destiné à être appelé depuis l'extérieur par un client MCP tiers comme
Claude, PAS les serveurs internes core/serveur_mcp_generation.py et
core/serveur_mcp_github.py qui restent en localhost, jamais concernés).

Factorisé ici plutôt que dupliqué dans chaque fichier serveur_mcp_*.py
(contrairement à la convention habituelle de duplication depuis api/*.py,
voir docstring de serveur_mcp_espace.py) : c'est un helper interne à
core/, pas une route api/*.py -- la convention documentée ("seuls les
modules core/*.py sont réutilisés, quand ils existent déjà") s'applique
donc normalement ici. Dupliquer une classe de vérification de jetons
OAuth dans plusieurs fichiers serait un risque de sécurité (divergence
silencieuse entre deux copies), à éviter contrairement à de la simple
logique métier.

PRINCIPE (delegation complète a Supabase Auth, fonctionnalité "OAuth 2.1
Server" -- Authentication > OAuth Server dans le tableau de bord
Supabase, à activer une fois, hors code, aucun outil ne peut le faire à
la place de Bourama) :
- Un client externe (Claude) découvre, via les métadonnées RFC 9728
  générées automatiquement par la librairie mcp à partir de
  `construire_auth_settings` ci-dessous, qu'il doit s'authentifier
  auprès de Supabase (`issuer_url`), jamais auprès de nous directement.
- Supabase gère l'écran de consentement (voir clovis-frontend
  app/oauth/consent/page.tsx), l'émission des jetons, leur
  rafraîchissement -- ce module ne fait QUE vérifier le jeton reçu à
  chaque appel d'outil.
- Les jetons OAuth émis par Supabase sont des JWT Supabase standards
  (mêmes claims qu'un jeton de session classique) : la vérification
  réutilise donc `supabase.auth.get_user(token)`, exactement comme
  api/auth.py:utilisateur_courant pour une session classique -- aucune
  nouvelle dépendance.
- L'identité de l'appelant n'est JAMAIS un paramètre que le modèle
  choisit : elle vient uniquement du jeton déjà vérifié par la librairie
  mcp elle-même avant que l'outil ne s'exécute (voir
  `user_id_depuis_contexte` ci-dessous).

RÉGLAGES À FAIRE UNE FOIS, HORS CODE, DANS LE TABLEAU DE BORD SUPABASE
(Authentication > OAuth Server) :
1. Activer "OAuth 2.1 Server" (bêta, gratuit).
2. Activer "Dynamic Client Registration".
3. Renseigner le chemin d'autorisation "/oauth/consent" (combiné à la
   Site URL déjà configurée pour clovis-frontend).
Tant que ce n'est pas fait, Supabase ne sert pas les points de découverte
OAuth et un client externe ne peut pas s'authentifier -- le code ici est
prêt à fonctionner dès que ces 3 réglages sont faits, sans modification
supplémentaire.
"""

import asyncio
import logging
import os

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import Context
from supabase import create_client

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error(
        "SUPABASE_URL ou SUPABASE_SECRET manquant : la verification des "
        "jetons OAuth des serveurs MCP publics sera toujours en echec."
    )

_supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

# URL publique de production (Railway, service clovis-backend -- voir
# Railway > prolific-truth > clovis-backend > domaine de service).
URL_BASE_PUBLIQUE = os.environ.get("URL_RESOURCE_SERVER_PUBLIC") or (
    "https://clovis-backend-production.up.railway.app"
)


class VerificateurJetonSupabase(TokenVerifier):
    """Vérifie un jeton d'accès OAuth émis par Supabase Auth.

    `get_user` est un appel bloquant (réseau) : sorti de la boucle
    asyncio via `asyncio.to_thread` pour ne pas geler le serveur pendant
    la vérification.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            reponse = await asyncio.to_thread(_supabase.auth.get_user, token)
        except Exception as e:
            logging.error(f"ERREUR verification jeton OAuth public : {e}")
            return None

        if not reponse or not reponse.user:
            return None

        return AccessToken(
            token=token,
            client_id=reponse.user.id,
            scopes=[],
            subject=reponse.user.id,
        )


def construire_auth_settings(chemin_montage: str) -> AuthSettings:
    """`chemin_montage` : ex. "/mcp/public" ou "/mcp/espace" -- doit
    correspondre exactement au chemin utilisé dans app.mount(...) côté
    api/main.py, sert d'identifiant de ressource RFC 8707/9728.
    """
    return AuthSettings(
        issuer_url=SUPABASE_URL,
        resource_server_url=f"{URL_BASE_PUBLIQUE}{chemin_montage}",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    )


def user_id_depuis_contexte(ctx: Context) -> str | None:
    """Id utilisateur du jeton déjà vérifié par la librairie MCP pour
    cette requête -- jamais un paramètre fourni par le modèle ou lu
    depuis les query params (contrairement à l'ancien mécanisme interne
    user_id/agent_id de core/serveur_mcp_generation.py, propre aux
    serveurs internes en localhost, pas à ceux-ci).
    """
    acces = ctx.request_context.request.auth
    if acces is None or not hasattr(acces, "subject"):
        return None
    return acces.subject
