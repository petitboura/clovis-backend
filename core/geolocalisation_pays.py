"""
08/09/2026, demande Bourama : dans la bibliothèque publique, les
dossiers/fichiers du pays de l'utilisateur doivent remonter en tête de
liste ("Tous" et "Dossiers"). Détection choisie par Bourama : par IP
côté serveur, sans jamais demander de permission au navigateur.

08/09/2026 (même jour, quelques heures plus tard) -- INCIDENT : l'appel
réseau à ipwho.is (service de géolocalisation externe utilisé au départ)
s'est mis à bloquer 15 à 20 secondes au lieu de respecter le timeout de
2s fixé, et ce blocage a fini par affamer le pool de threads de tout le
service (le scroll continu de la bibliothèque publique -- fichiers ET
dossiers -- s'est arrêté de fonctionner, et même des endpoints sans
rapport comme le WebSocket du chat ont vu leur latence grimper jusqu'à
100s dans les logs Railway). Cause probable : la résolution DNS/
connexion à cet hôte externe n'est pas fiable depuis le réseau de
Railway, or le paramètre `timeout` de `requests` ne couvre pas de façon
fiable la phase de résolution DNS -- un blocage à ce niveau peut donc
dépasser largement le timeout déclaré.

Correctif appliqué : plus AUCUN appel réseau externe pour l'instant --
`pays_utilisateur()` renvoie toujours None immédiatement (aucune mise
en avant, comportement strictement identique à avant l'ajout de cette
fonctionnalité). Le mapping code ISO -> nom français et la logique de
correspondance avec les valeurs `pays` déjà utilisées sont conservés
tels quels pour une reprise ultérieure, mais `_detecter_sans_cache` ne
fait plus le moindre appel HTTP -- voir Bourama avant de rebrancher un
appel réseau ici, et si c'est fait, l'entourer d'un timeout réellement
infranchissable (ex: thread dédié + `.result(timeout=...)`, jamais
`requests` seul) pour ne plus jamais pouvoir bloquer un thread de
requête FastAPI.
"""

import unicodedata

from core.listes_bibliotheque_publique import lister_valeurs

# IPs locales/privées fréquentes en dev/tests -- jamais résolvables par
# un service de géolocalisation externe, pas la peine d'essayer.
PREFIXES_IP_NON_RESOLVABLES = ("127.", "10.", "192.168.", "::1")

# Table de correspondance code ISO -> nom français, volontairement
# centrée sur les pays francophones (public visé par Djiguignè/Clovis)
# + quelques pays fréquents. Conservée pour une reprise ultérieure de la
# détection (voir docstring ci-dessus) -- inutilisée tant que
# _detecter_sans_cache ne fait aucun appel réseau.
NOMS_PAYS_PAR_CODE = {
    "TN": "Tunisie", "FR": "France", "DZ": "Algérie", "MA": "Maroc",
    "SN": "Sénégal", "CI": "Côte d'Ivoire", "ML": "Mali", "CM": "Cameroun",
    "BE": "Belgique", "CH": "Suisse", "CA": "Canada", "LU": "Luxembourg",
    "TG": "Togo", "BJ": "Bénin", "BF": "Burkina Faso", "NE": "Niger",
    "GN": "Guinée", "MG": "Madagascar", "CD": "République démocratique du Congo",
    "CG": "Congo", "GA": "Gabon", "MR": "Mauritanie", "TD": "Tchad",
    "DJ": "Djibouti", "KM": "Comores", "HT": "Haïti", "LB": "Liban",
    "US": "États-Unis", "GB": "Royaume-Uni", "DE": "Allemagne", "ES": "Espagne",
    "IT": "Italie", "EG": "Égypte", "SA": "Arabie saoudite", "AE": "Émirats arabes unis",
    "QA": "Qatar", "TR": "Turquie",
}


def _normaliser(valeur: str) -> str:
    """Insensible à la casse et aux accents, pour comparer des noms de pays tapés librement."""
    sans_accents = unicodedata.normalize("NFKD", valeur).encode("ascii", "ignore").decode("ascii")
    return sans_accents.strip().lower()


def _nom_deja_utilise(nom_mappe: str) -> str | None:
    """Cherche parmi les valeurs `pays` déjà publiées une correspondance insensible casse/accents, renvoie l'orthographe exacte stockée en base."""
    cible = _normaliser(nom_mappe)
    for valeur in lister_valeurs("pays"):
        if _normaliser(valeur) == cible:
            return valeur
    return None


def _detecter_sans_cache(ip: str) -> str | None:
    # 08/09/2026, INCIDENT -- désactivé, voir docstring en tête de
    # fichier. Ne JAMAIS remettre un appel réseau direct ici sans un
    # timeout infranchissable (thread dédié + .result(timeout=...)).
    return None


def pays_utilisateur(request) -> str | None:
    """Pays détecté de l'utilisateur courant -- désactivé depuis l'incident du 08/09/2026, voir docstring en tête de fichier. Renvoie toujours None."""
    return None
