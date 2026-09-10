"""
Fabrique le client HTTP à passer à chaque create_client() Supabase du
backend (~30 fichiers, chacun crée sa propre connexion indépendante).

Contexte (10/09, bug ConnectionTerminated signalé par Bourama -- ex:
routage_outils.py traitant une lecture Supabase cassée comme "aucun outil
retenu") : lecture du code source de postgrest-py (SyncPostgrestClient,
postgrest/_sync/client.py) -- le client HTTP interne active HTTP/2 par
défaut (http2=True) et garde CETTE MÊME connexion ouverte pour toute la
durée de vie du process (chaque fichier construit son client une seule
fois au chargement du module, jamais recréé ensuite). Quand Supabase ferme
cette connexion de son côté (normal, arrive régulièrement, sans rapport
avec une panne chez eux), httpx/h2 ne détecte pas toujours le GOAWAY avant
la requête suivante -- la lib essaie donc de réutiliser une connexion déjà
morte, d'où ConnectionTerminated, sur n'importe laquelle des ~30 connexions
créées dans le projet, à peu près n'importe où dans l'application.

En HTTP/1.1 (pas de multiplexage), httpx détecte une connexion morte dans
son pool et en ouvre une nouvelle automatiquement avant chaque requête --
comportement standard, sans intervention nécessaire côté appelant. On
désactive donc HTTP/2 pour ces connexions. keepalive_expiry borne aussi la
durée de vie d'une connexion en veille dans le pool, pour réduire encore le
risque qu'une connexion déjà fermée par Supabase y traîne.

Usage, à chaque site de création d'un client Supabase :
    from supabase import create_client, ClientOptions
    from client_http_supabase import nouveau_client_http_supabase
    supabase = create_client(
        URL, SECRET,
        options=ClientOptions(httpx_client=nouveau_client_http_supabase()),
    )

Le timeout (120s) reprend la valeur par défaut de postgrest-py
(DEFAULT_POSTGREST_CLIENT_TIMEOUT) : passer un httpx_client explicite fait
ignorer ce défaut par la lib, il faut donc le reposer nous-mêmes pour ne
rien changer d'autre au comportement existant.
"""
import httpx

TIMEOUT_SUPABASE = 120.0


def nouveau_client_http_supabase():
    """
    Un nouveau httpx.Client à chaque appel (jamais partagé entre plusieurs
    create_client -- chaque fichier garde sa propre connexion indépendante,
    comme aujourd'hui ; seul le réglage de cette connexion change).
    """
    return httpx.Client(
        http2=False,
        timeout=TIMEOUT_SUPABASE,
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=20.0),
    )
