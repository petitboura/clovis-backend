"""
Génération de bundles : zippe des éléments hétérogènes (fichiers déjà
générés ailleurs, récupérés par URL, ou fournis en brut) en une seule
archive .zip.

Gratuit et local (juste de la compression + un téléchargement HTTP,
aucune clé API requise) : reste actif dès maintenant, comme
generation_code.py et generation_documents.py. Réutilise le même bucket
Supabase "generations" (dossier "bundles/").
"""

import io
import logging
import uuid
import zipfile

import requests

from api.auth import supabase
from core.securite_chemins import chemin_relatif_sur

BUCKET = "generations"


def generer_bundle(nom_projet: str, elements: list[dict]) -> str:
    """
    Zippe des éléments hétérogènes en un seul bundle.

    Chaque élément de `elements` est un dict avec :
      - "chemin" : chemin relatif dans le zip (ex. "rapport.pdf")
      - soit "contenu" : str ou bytes (contenu brut fourni directement)
      - soit "url" : URL publique Supabase d'un fichier déjà généré (à
        télécharger avant de l'ajouter au zip)

    Ex. :
    [
        {"chemin": "rapport.pdf", "url": "https://.../documents/xxx.pdf"},
        {"chemin": "donnees.csv", "contenu": "a,b\\n1,2"},
        {"chemin": "graphique.png", "contenu": b"..."},
    ]

    Lève une exception si un téléchargement ou l'upload échoue -- même
    contrat d'erreur que les autres modules generation_*.py : à
    l'appelant (serveur MCP) de transformer ça en message utilisateur
    clair, pas de logique de message d'erreur ici.
    """
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for element in elements:
            # CORRECTIF 2026-07-30 (audit sécurité) : "chemin" vient du
            # modèle, jamais vérifié auparavant -- un "../../../etc/x"
            # s'écrivait tel quel dans le zip (zip-slip à l'extraction).
            chemin = chemin_relatif_sur(element["chemin"])
            if "url" in element:
                try:
                    reponse = requests.get(element["url"], timeout=30)
                    reponse.raise_for_status()
                except Exception as e:
                    logging.error(
                        f"ERREUR TÉLÉCHARGEMENT (bundle {nom_projet}, "
                        f"élément {chemin}, url {element['url']}) : {e}"
                    )
                    raise
                archive.writestr(chemin, reponse.content)
            else:
                archive.writestr(chemin, element["contenu"])

    tampon.seek(0)
    chemin_stockage = f"bundles/{uuid.uuid4()}-{nom_projet}.zip"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, tampon.read(), {"content-type": "application/zip"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload bundle {chemin_stockage}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)
