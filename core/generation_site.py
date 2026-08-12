"""
Génération de sites web : soit un .zip téléchargeable (gratuit, réutilise
generation_code.py), soit un déploiement en ligne directement utilisable
via l'API Vercel.

CONTRAIREMENT à generation_documents.py et generation_code.py, le
déploiement en ligne N'EST PAS strictement gratuit à grande échelle
(quota Vercel), donc gaté par la présence de VERCEL_API_TOKEN dans les
secrets/variables d'environnement, même pattern que TOGETHER_API_KEY
pour generation_images.py.

Utilise le compte Vercel de Bourama (un seul token pour toute la
plateforme, pas de connexion "par utilisateur" pour l'instant -- voir
échange du 2026-07-22). C'est à l'agent, via son prompt système, de
demander à l'utilisateur s'il veut le code seul (zip, deployer_site pas
appelé) ou un lien en ligne (deployer_site appelé), pas à ce module de
décider.

Tant que Bourama n'a pas ajouté VERCEL_API_TOKEN (Railway -> variables
d'environnement), `site_deploiement_disponible()` renvoie False et le
serveur MCP ne propose même pas l'outil de déploiement -- seul
generer_site_zip reste disponible (gratuit, jamais gaté).
"""

import logging
import os

import requests

BASE_URL = "https://api.vercel.com/v13/deployments"


def _get_secret(cle):
    return os.environ.get(cle)


def site_deploiement_disponible() -> bool:
    """
    Utilisé par le serveur MCP pour décider si l'outil deployer_site
    doit être proposé à l'agent.
    """
    return bool(_get_secret("VERCEL_API_TOKEN"))


def deployer_site(nom_projet: str, fichiers: dict[str, str]) -> str:
    """
    Déploie un site statique (HTML/CSS/JS) sur Vercel, avec le compte
    plateforme, et renvoie l'URL publique en ligne.

    `fichiers` : dictionnaire {chemin_relatif: contenu_texte}, ex.
    {"index.html": "<html>...</html>", "style.css": "body {...}"}.

    Lève une exception si la clé est absente (l'appelant doit avoir déjà
    vérifié site_deploiement_disponible() avant d'appeler cette fonction
    -- garde-fou volontairement redondant, pas une excuse pour sauter la
    vérification en amont) ou si l'appel à Vercel échoue.
    """
    cle = _get_secret("VERCEL_API_TOKEN")
    if not cle:
        raise RuntimeError(
            "Déploiement de site indisponible : VERCEL_API_TOKEN n'est pas configurée."
        )

    fichiers_vercel = [
        {"file": chemin, "data": contenu} for chemin, contenu in fichiers.items()
    ]

    try:
        reponse = requests.post(
            BASE_URL,
            headers={"Authorization": f"Bearer {cle}"},
            json={
                "name": nom_projet,
                "files": fichiers_vercel,
                "target": "production",
                # Site statique pur : pas de build a lancer cote Vercel,
                # les fichiers fournis sont serves tels quels.
                "projectSettings": {"framework": None},
            },
            timeout=60,
        )
        reponse.raise_for_status()
    except Exception as e:
        logging.error(f"ERREUR DEPLOIEMENT VERCEL (projet {nom_projet}) : {e}")
        raise

    url_deploiement = reponse.json()["url"]
    return f"https://{url_deploiement}"
