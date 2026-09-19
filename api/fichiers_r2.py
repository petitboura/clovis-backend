"""
Route de service des fichiers stockés sur Cloudflare R2.

Le bucket R2 reste privé. Cette route sert les objets au travers du backend.
Elle prend en charge GET/HEAD et les requêtes HTTP Range nécessaires aux
lecteurs audio/vidéo et aux aperçus de fichiers.
"""

import mimetypes

from fastapi import APIRouter, Request, Response

from core import stockage_r2
from core.erreurs import erreur_api

router = APIRouter(prefix="/fichiers/r2", tags=["fichiers"])


def _type_mime(chemin: str, resultat: dict | None = None) -> str:
    return (
        (resultat or {}).get("ContentType")
        or mimetypes.guess_type(chemin)[0]
        or "application/octet-stream"
    )


def _parse_range(value: str | None, taille: int) -> tuple[int, int] | None:
    if not value or not value.startswith("bytes="):
        return None
    spec = value[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        return None

    debut, fin = spec.split("-", 1)
    try:
        if debut == "":
            suffixe = int(fin)
            if suffixe <= 0:
                return None
            debut = max(taille - suffixe, 0)
            fin = taille - 1
        else:
            debut = int(debut)
            fin = int(fin) if fin else taille - 1
    except ValueError:
        return None

    if debut < 0 or debut >= taille or fin < debut:
        return None
    return debut, min(fin, taille - 1)


@router.api_route("/{bucket_logique}/{chemin:path}", methods=["GET", "HEAD"])
async def servir_fichier_r2(bucket_logique: str, chemin: str, request: Request):
    try:
        meta = stockage_r2._client.head_object(
            Bucket=stockage_r2.R2_BUCKET_NAME,
            Key=stockage_r2._cle_objet(bucket_logique, chemin),
        )
    except Exception:
        raise erreur_api(404, "FICHIER_INTROUVABLE")

    taille = int(meta.get("ContentLength") or 0)
    type_mime = _type_mime(chemin, meta)

    if request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Content-Type": type_mime,
                "Content-Length": str(taille),
                "Accept-Ranges": "bytes",
            },
        )

    range_value = request.headers.get("range")
    plage = _parse_range(range_value, taille) if taille else None

    if range_value and plage is None:
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{taille}",
                "Accept-Ranges": "bytes",
            },
        )

    try:
        if plage:
            debut, fin = plage
            resultat = stockage_r2._client.get_object(
                Bucket=stockage_r2.R2_BUCKET_NAME,
                Key=stockage_r2._cle_objet(bucket_logique, chemin),
                Range=f"bytes={debut}-{fin}",
            )
            contenu = resultat["Body"].read()
            return Response(
                content=contenu,
                status_code=206,
                headers={
                    "Content-Type": type_mime,
                    "Content-Length": str(len(contenu)),
                    "Content-Range": f"bytes {debut}-{fin}/{taille}",
                    "Accept-Ranges": "bytes",
                },
            )

        contenu = stockage_r2.from_(bucket_logique).download(chemin)
    except Exception:
        raise erreur_api(404, "FICHIER_INTROUVABLE")

    return Response(
        content=contenu,
        media_type=type_mime,
        headers={
            "Content-Length": str(len(contenu)),
            "Accept-Ranges": "bytes",
        },
    )
