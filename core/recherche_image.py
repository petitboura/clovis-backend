"""
Recherche d'image -- DEUX fournisseurs, en cascade, comme les modèles de
langage de secours (Groq -> Gemini) et comme generer_image (Pollinations
-> Together AI) :

1. Pixabay (https://pixabay.com/api) : GRATUIT, clé simple (pas d'OAuth,
   pas de carte bancaire), 100 requêtes/minute, photos + illustrations
   + vecteurs -- essayé en premier (bibliothèque plus large, utile pour
   un usage étudiant : schémas/illustrations en plus des photos).
2. Pexels (https://api.pexels.com/v1) : GRATUIT, clé simple, 200
   requêtes/heure -- utilisé UNIQUEMENT si Pixabay échoue ou ne renvoie
   rien (clé absente, quota dépassé, panne...), jamais en même temps.

Choix fait avec Bourama le 01/09/2026 (comparaison Pixabay vs Pexels) :
aucun des deux n'est strictement meilleur, donc cascade plutôt qu'un
choix figé -- si l'un est indisponible, l'autre prend le relais tout
seul, sans jamais faire échouer la recherche pour l'étudiant.

Licences : les deux autorisent un usage commercial libre, Pixabay sans
attribution obligatoire -- le nom du fournisseur ("credit") est quand
même conservé dans le résultat, affiché en petit dans la galerie
(clovis-frontend), par courtoisie envers les créateurs.

NON TESTÉ EN CONDITIONS RÉELLES (clés PIXABAY_API_KEY/PEXELS_API_KEY pas
encore configurées au moment de l'écriture, 01/09/2026) -- à vérifier au
premier vrai test, comme d'habitude (voir generation_images.py).
"""

import logging
import os

import requests

_URL_PIXABAY = "https://pixabay.com/api/"
_URL_PEXELS = "https://api.pexels.com/v1/search"


def _get_secret(cle):
    return os.environ.get(cle)


def _chercher_via_pixabay(requete: str, nombre: int, cle: str) -> list[dict]:
    reponse = requests.get(
        _URL_PIXABAY,
        params={
            "key": cle,
            "q": requete,
            "per_page": max(3, min(nombre, 20)),  # minimum imposé par l'API Pixabay
            "safesearch": "true",
        },
        timeout=15,
    )
    reponse.raise_for_status()
    hits = reponse.json().get("hits", [])
    # "url" = image DIRECTE (pas pageURL, qui pointe vers la page Pixabay
    # marketing) -- nécessaire pour que le visionneur en app
    # (VisionneurPositionGlobal.tsx, typeMime "image/*") puisse l'afficher
    # directement, comme n'importe quelle autre image de la bibliothèque.
    return [
        {
            "titre": h.get("tags") or requete,
            "url": h.get("largeImageURL") or h.get("webformatURL"),
            "miniature": h.get("webformatURL") or h.get("previewURL"),
            "credit": f"{h['user']} (Pixabay)" if h.get("user") else "Pixabay",
        }
        for h in hits[:nombre]
        if (h.get("largeImageURL") or h.get("webformatURL")) and (h.get("webformatURL") or h.get("previewURL"))
    ]


def _chercher_via_pexels(requete: str, nombre: int, cle: str) -> list[dict]:
    reponse = requests.get(
        _URL_PEXELS,
        headers={"Authorization": cle},
        params={"query": requete, "per_page": max(1, min(nombre, 15))},
        timeout=15,
    )
    reponse.raise_for_status()
    photos = reponse.json().get("photos", [])
    return [
        {
            "titre": p.get("alt") or requete,
            "url": (p.get("src") or {}).get("large") or (p.get("src") or {}).get("original"),
            "miniature": (p.get("src") or {}).get("medium") or (p.get("src") or {}).get("small"),
            "credit": f"{p['photographer']} (Pexels)" if p.get("photographer") else "Pexels",
        }
        for p in photos[:nombre]
        if ((p.get("src") or {}).get("large") or (p.get("src") or {}).get("original")) and (p.get("src") or {}).get("medium")
    ]


def rechercher_images(requete: str, nombre: int = 6) -> list[dict]:
    """
    Cherche des images sur le web. Essaie Pixabay en premier, retombe
    automatiquement sur Pexels si Pixabay échoue ou ne renvoie rien.
    Renvoie une liste (peut être vide) de {"titre", "url", "miniature",
    "credit"}.
    """
    cle_pixabay = _get_secret("PIXABAY_API_KEY")
    if cle_pixabay:
        try:
            resultats = _chercher_via_pixabay(requete, nombre, cle_pixabay)
            if resultats:
                return resultats
        except Exception as e:
            logging.error(f"ERREUR RECHERCHE IMAGE (Pixabay, requête {requete!r}) : {e}")
    else:
        logging.warning("PIXABAY_API_KEY manquante -- recherche d'image tentée directement via Pexels.")

    cle_pexels = _get_secret("PEXELS_API_KEY")
    if not cle_pexels:
        logging.error("PEXELS_API_KEY manquante -- aucun fournisseur de recherche d'image disponible.")
        return []

    try:
        return _chercher_via_pexels(requete, nombre, cle_pexels)
    except Exception as e:
        logging.error(f"ERREUR RECHERCHE IMAGE (Pexels, requête {requete!r}) : {e}")
        return []
