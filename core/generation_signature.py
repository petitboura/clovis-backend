"""
Signature électronique de documents via Lumin (api.luminpdf.com).

Comme generation_images.py : gaté par la présence de LUMIN_API_KEY, mais
CONTRAIREMENT aux images, Lumin est gratuit jusqu'à 5 signatures/mois
(largement suffisant pour commencer, aucune urgence budgétaire ici,
voir https://developers.luminpdf.com).

Flux : le contenu (markdown) est d'abord converti en PDF et stocké sur
Supabase (réutilise generation_documents.py telle quelle -- pas de
duplication), puis cette URL publique est envoyée à Lumin comme
`file_url`. Lumin envoie ensuite un email à chaque signataire avec un
lien pour signer.

Clé à générer une fois (gratuit) : Lumin -> Settings -> Developer
settings -> API keys -> Generate key. Puis LUMIN_API_KEY dans les
variables d'environnement Railway.
"""

import logging
import os
import time

import requests

from core.generation_documents import generer_pdf_depuis_markdown

BASE_URL = "https://api.luminpdf.com/v1"


def _get_secret(cle):
    return os.environ.get(cle)


def signature_disponible() -> bool:
    return bool(_get_secret("LUMIN_API_KEY"))


def _headers():
    return {"X-API-KEY": _get_secret("LUMIN_API_KEY"), "Content-Type": "application/json"}


def envoyer_pour_signature(
    titre: str,
    contenu_markdown: str,
    signataires: list[dict],
    jours_expiration: int = 14,
) -> dict:
    """
    `signataires` : liste de {"nom": ..., "email": ...}.

    Génère le PDF (via generation_documents.py, même moteur que pour un
    document classique), l'envoie à Lumin pour signature, renvoie
    {"signature_request_id": ..., "statut": ..., "url_document": ...}.

    Lève une exception si LUMIN_API_KEY est absente, si la génération du
    PDF échoue, ou si Lumin renvoie une erreur.
    """
    cle = _get_secret("LUMIN_API_KEY")
    if not cle:
        raise RuntimeError("Signature indisponible : LUMIN_API_KEY n'est pas configurée.")

    url_pdf = generer_pdf_depuis_markdown(titre, contenu_markdown)

    expires_at_ms = int((time.time() + jours_expiration * 86400) * 1000)

    reponse = requests.post(
        f"{BASE_URL}/signature_request/send",
        headers=_headers(),
        json={
            "title": titre[:255],
            "file_url": url_pdf,
            "signers": [
                {"name": s["nom"], "email_address": s["email"]} for s in signataires
            ],
            "expires_at": expires_at_ms,
        },
        timeout=30,
    )
    if reponse.status_code != 201:
        logging.error(f"ERREUR Lumin (envoi signature) : {reponse.status_code} {reponse.text}")
        reponse.raise_for_status()

    resultat = reponse.json()["signature_request"]
    return {
        "signature_request_id": resultat["signature_request_id"],
        "statut": resultat["status"],
        "url_document": url_pdf,
    }


def statut_signature(signature_request_id: str) -> dict:
    """
    Consulte l'état d'une demande de signature déjà envoyée (ex:
    WAITING_FOR_PROCESSING, NEED_TO_SIGN, signé, expiré...).
    """
    cle = _get_secret("LUMIN_API_KEY")
    if not cle:
        raise RuntimeError("Signature indisponible : LUMIN_API_KEY n'est pas configurée.")

    reponse = requests.get(
        f"{BASE_URL}/signature_request/{signature_request_id}",
        headers=_headers(),
        timeout=30,
    )
    reponse.raise_for_status()
    return reponse.json()
