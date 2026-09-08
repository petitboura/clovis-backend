"""
08/09/2026, demande Bourama : dans la bibliothèque publique, les
dossiers/fichiers du pays de l'utilisateur doivent remonter en tête de
liste ("Tous" et "Dossiers"). Détection choisie par Bourama : par IP
côté serveur, sans jamais demander de permission au navigateur.

Fonctionnement :
1. IP réelle du client extraite via api.journal.ip_client (déjà utilisé
   pour le journal d'audit -- gère le proxy Railway/X-Forwarded-For).
2. Appel à un service de géolocalisation IP externe (ipwho.is, gratuit,
   sans clé) pour obtenir le code pays ISO (ex: "TN").
3. Le champ `pays` de la bibliothèque publique est du texte entièrement
   libre tapé par les utilisateurs (voir core/listes_bibliotheque_
   publique.py, aucune liste fermée) -- pas de correspondance garantie
   avec le nom du pays détecté. On mappe donc le code ISO vers un nom
   français, puis on cherche, insensible à la casse/aux accents, si ce
   nom correspond à une valeur DÉJÀ utilisée dans la bibliothèque
   publique (via lister_valeurs("pays")) pour renvoyer exactement la
   même orthographe que celle stockée en base (indispensable pour un
   `.eq("pays", ...)` côté appelant). Si aucune valeur existante ne
   correspond, on renvoie quand même le nom mappé (le tri par priorité
   ne fera simplement rien tant que personne n'a publié sous ce nom --
   comportement sans risque, jamais d'erreur affichée).

Best-effort partout : un souci réseau, un timeout, une IP locale/non
résolvable ou un pays non couvert par la table de correspondance ne
doivent JAMAIS bloquer ni ralentir sensiblement le chargement de la
bibliothèque publique -- on renvoie simplement None (pas de mise en
avant, comportement identique à avant cette fonctionnalité).

Cache en mémoire (process du service Railway) par IP, 6h de durée de
vie : le pays d'une IP donnée ne change presque jamais, pas la peine de
rappeler le service externe à chaque scroll infini.
"""

import logging
import time
import unicodedata

import requests

from api.journal import ip_client
from core.listes_bibliotheque_publique import lister_valeurs

DELAI_MAX_SECONDES = 2
DUREE_CACHE_SECONDES = 6 * 60 * 60

# IPs locales/privées fréquentes en dev/tests -- jamais résolvables par
# un service de géolocalisation externe, pas la peine d'essayer.
PREFIXES_IP_NON_RESOLVABLES = ("127.", "10.", "192.168.", "::1")

_cache: dict[str, tuple[str | None, float]] = {}

# Table de correspondance code ISO -> nom français, volontairement
# centrée sur les pays francophones (public visé par Djiguignè/Clovis)
# + quelques pays fréquents. Un code absent de cette table renvoie
# simplement None (pas de mise en avant) plutôt que de deviner un nom.
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
    try:
        reponse = requests.get(f"https://ipwho.is/{ip}", timeout=DELAI_MAX_SECONDES)
        donnees = reponse.json()
    except Exception as e:
        logging.error(f"ERREUR geolocalisation IP ({ip}) : {e}")
        return None
    if not donnees.get("success", True):  # ipwho.is renvoie success=False sur IP invalide
        return None
    code = (donnees.get("country_code") or "").strip().upper()
    nom_mappe = NOMS_PAYS_PAR_CODE.get(code)
    if not nom_mappe:
        return None
    return _nom_deja_utilise(nom_mappe) or nom_mappe


def pays_utilisateur(request) -> str | None:
    """Pays détecté de l'utilisateur courant (nom tel qu'il apparaîtrait dans le champ `pays`), ou None si indétectable/inconnu."""
    ip = ip_client(request)
    if not ip or ip.startswith(PREFIXES_IP_NON_RESOLVABLES):
        return None

    maintenant = time.time()
    en_cache = _cache.get(ip)
    if en_cache and en_cache[1] > maintenant:
        return en_cache[0]

    pays = _detecter_sans_cache(ip)
    _cache[ip] = (pays, maintenant + DUREE_CACHE_SECONDES)
    return pays
