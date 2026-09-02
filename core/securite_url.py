"""
Protection contre le SSRF (Server-Side Request Forgery).

Contexte : Clovis va chercher lui-même le contenu des liens collés dans
un message de chat (page web, image) ou envoyés en upload (image). Sans
vérification, un lien pointant vers une adresse interne (le serveur
lui-même, le réseau privé de l'hébergeur, ou l'adresse "metadata" propre
à chaque fournisseur cloud qui expose parfois des identifiants) ferait
que le SERVEUR envoie une requête à cet endroit pour le compte de
n'importe qui -- exactement ce qu'un pare-feu normal empêcherait un
visiteur externe de faire directement.

Cette fonction doit être appelée juste avant CHAQUE requête HTTP sortante
dont l'URL est fournie (directement ou indirectement) par un utilisateur.
"""

import ipaddress
import socket
from urllib.parse import urlparse

# Hôtes/domaines explicitement interdits même s'ils résolvent en IP
# publique (nom que l'hébergeur pourrait exposer en interne).
HOTES_INTERDITS = {"localhost", "metadata.google.internal"}

# Ports jamais autorisés pour un lien "contenu web" (services internes
# usuels -- bases de données, cache, etc.), en plus du filtrage par IP.
PORTS_INTERDITS = {22, 25, 3306, 5432, 6379, 27017}


class UrlNonAutorisee(Exception):
    """Levée quand une URL fournie par un utilisateur est jugée dangereuse à récupérer."""


def _ip_est_interdite(ip_str: str) -> bool:
    """
    True si l'IP est privée, loopback, link-local (inclut 169.254.169.254,
    l'adresse "metadata" utilisée par AWS/GCP/Azure pour exposer des
    identifiants internes aux machines), multicast, ou réservée.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # IP illisible -> on refuse par prudence
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def valider_url_externe(url: str) -> None:
    """
    Vérifie qu'une URL fournie par un utilisateur peut être récupérée
    sans danger pour l'infrastructure de Clovis. Ne renvoie rien si OK ;
    lève UrlNonAutorisee sinon.

    Résout le nom de domaine en IP et vérifie l'IP RÉELLE (pas juste le
    nom), pour ne pas être contourné par un domaine qui pointe
    volontairement vers une adresse interne (technique dite de "DNS
    rebinding").
    """
    try:
        morceaux = urlparse(url)
    except Exception:
        raise UrlNonAutorisee("URL illisible")

    if morceaux.scheme not in ("http", "https"):
        raise UrlNonAutorisee(f"schema non autorise : {morceaux.scheme!r}")

    hote = morceaux.hostname
    if not hote:
        raise UrlNonAutorisee("aucun hote dans l'URL")

    if hote.lower() in HOTES_INTERDITS:
        raise UrlNonAutorisee(f"hote interdit : {hote}")

    if morceaux.port and morceaux.port in PORTS_INTERDITS:
        raise UrlNonAutorisee(f"port interdit : {morceaux.port}")

    try:
        infos = socket.getaddrinfo(hote, None)
    except socket.gaierror:
        raise UrlNonAutorisee(f"resolution DNS impossible : {hote}")

    for info in infos:
        ip_str = info[4][0]
        if _ip_est_interdite(ip_str):
            raise UrlNonAutorisee(f"IP interdite ({ip_str}) pour l'hote {hote}")
