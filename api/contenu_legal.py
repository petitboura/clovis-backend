"""
Contenu légal (CGU, politique de copyright). 22/08/2026, chantier
"rendre la bibliothèque publique plus sérieuse" (voir guide Notion
"Guide pour droit d'auteur"). Lecture publique uniquement, aucune
route d'écriture ici : la table `contenu_legal` est éditée directement
par Bourama via le dashboard Supabase, même principe que
agents_administrateurs (voir migration
2026_08_22_bibliotheque_publique_moderation_signalements.sql).
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from api.auth import supabase
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/legal", tags=["contenu_legal"])

CLES_VALIDES = ("cgu", "copyright")


class ContenuLegalReponse(BaseModel):
    cle: str
    titre: str
    contenu_markdown: str
    updated_at: str


@router.get("/{cle}", response_model=ContenuLegalReponse)
def lire_contenu_legal(cle: str):
    if cle not in CLES_VALIDES:
        raise erreur_api(404, "CONTENU_LEGAL_INTROUVABLE")
    try:
        res = supabase.table("contenu_legal").select("*").eq("cle", cle).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture contenu_legal {cle}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if not res or not res.data:
        raise erreur_api(404, "CONTENU_LEGAL_INTROUVABLE")
    return res.data
