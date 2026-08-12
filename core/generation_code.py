"""
Génération de fichier(s) de code à partir d'un ou plusieurs fichiers.

Gratuit et local (juste de la compression le cas échéant, aucune clé API
requise) : reste actif dès maintenant, comme generation_documents.py.
Réutilise le même bucket Supabase "generations" (dossier "code/" au lieu
de "documents/").
"""

import io
import logging
import mimetypes
import uuid
import zipfile

from api.auth import supabase
from core.securite_chemins import chemin_relatif_sur

BUCKET = "generations"

# mimetypes ne connaît pas tout (ex: .tsx, .jsx) -- complète pour les
# extensions de code les plus courantes plutôt que de retomber sur
# application/octet-stream, qui force un téléchargement au lieu d'un
# affichage/aperçu dans le navigateur.
_TYPES_MIME_SUPPLEMENTAIRES = {
    ".py": "text/x-python",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".jsx": "text/javascript",
    ".md": "text/markdown",
    ".yml": "text/yaml",
    ".yaml": "text/yaml",
}


def _content_type(chemin_fichier: str) -> str:
    extension = "." + chemin_fichier.rsplit(".", 1)[-1] if "." in chemin_fichier else ""
    if extension in _TYPES_MIME_SUPPLEMENTAIRES:
        return _TYPES_MIME_SUPPLEMENTAIRES[extension]
    type_devine, _ = mimetypes.guess_type(chemin_fichier)
    return type_devine or "text/plain"


def generer_zip_depuis_fichiers(nom_projet: str, fichiers: dict[str, str]) -> str:
    """
    `fichiers` : dictionnaire {chemin_relatif: contenu_texte}, ex.
    {"main.py": "print('hello')", "README.md": "# Mon projet"}.

    Un seul fichier -> upload direct (PAS de zip) : Bourama a remonté en
    test réel qu'un fichier .py seul était forcé dans une archive .zip,
    inutile et gênant (il faut dézipper pour lire un unique fichier).
    Plusieurs fichiers -> zip, comme avant (seul moyen de livrer une
    arborescence en une URL).

    Upload dans Supabase Storage, renvoie l'URL publique. Même contrat
    d'erreur que generation_documents.py : les exceptions remontent
    telles quelles.
    """
    if len(fichiers) == 1:
        chemin_fichier, contenu = next(iter(fichiers.items()))
        # CORRECTIF 2026-07-30 (audit sécurité, voir securite_chemins.py) :
        # chemin_fichier vient du modèle, jamais vérifié auparavant.
        chemin_fichier = chemin_relatif_sur(chemin_fichier, repli="fichier.txt")
        chemin_stockage = f"code/{uuid.uuid4()}-{chemin_fichier}"
        try:
            supabase.storage.from_(BUCKET).upload(
                chemin_stockage, contenu.encode("utf-8"), {"content-type": _content_type(chemin_fichier)}
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE STORAGE (upload code {chemin_stockage}) : {e}")
            raise
        return supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for chemin_fichier, contenu in fichiers.items():
            # CORRECTIF 2026-07-30 (audit sécurité) : sans ça, un chemin du
            # type "../../../etc/cron.d/x" s'écrivait tel quel dans le zip
            # (zip-slip à l'extraction chez la personne qui le télécharge).
            archive.writestr(chemin_relatif_sur(chemin_fichier), contenu)
    tampon.seek(0)

    chemin_stockage = f"code/{uuid.uuid4()}-{nom_projet}.zip"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, tampon.read(), {"content-type": "application/zip"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload code {chemin_stockage}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)
