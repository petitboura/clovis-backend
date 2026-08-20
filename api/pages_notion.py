"""
Section "Notion-like" (Partie 2), 2026-08-20, demande Bourama -- chantier
distinct de la structure programme (Partie 1), lot 1/5.

Voir migrations/2026_08_20_pages_blocs_notion.sql pour le schéma. Chaque
route vérifie que l'appelant est bien propriétaire de la ressource
(directement pour une page, en remontant à la page pour un bloc) -- jamais
de lecture/écriture croisée entre comptes ici.

Pas d'éditeur de blocs ici (lot 5), pas de LaTeX/pages carrefour (lot 2),
pas de bases de révision (lot 3), pas de répétition espacée (lot 4) --
uniquement le schéma et le CRUD de base. Voir aussi core/serveur_mcp_generation.py
et core/serveur_mcp_espace.py pour les outils de navigation exposés à l'IA
(demande explicite Bourama, 20/08 : l'IA doit pouvoir naviguer/s'orienter
dans cette structure, en interne comme sur le MCP public).
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from core.erreurs import erreur_api
from core.pages_notion_llm import (
    ajouter_reference_carrefour as _ajouter_reference_carrefour,
    lister_references_carrefour as _lister_references_carrefour,
    supprimer_reference_carrefour as _supprimer_reference_carrefour,
)

logging.basicConfig(level=logging.INFO)

router_pages = APIRouter(prefix="/api/pages", tags=["pages_notion"])
router_blocs = APIRouter(prefix="/api/blocs", tags=["pages_notion"])

# Types de blocs actuellement gérés côté affichage -- liste ouverte (la
# colonne `type` est un texte libre en base, voir migration), mais on
# valide ici pour éviter qu'un type mal orthographié parte silencieusement
# en base. Les lots 2/3 étendront cette liste (équation, vue base de
# données, etc.) au lieu de la redéfinir ailleurs.
TYPES_BLOCS_CONNUS = {
    "texte",
    "titre",
    "liste_puces",
    "liste_numerotee",
    "case_a_cocher",
    "citation",
    "separateur",
    "equation",
}

TYPES_CIBLE_CARREFOUR = ("programme", "matiere", "chapitre", "document")


class PagePayload(BaseModel):
    titre: str = ""
    parent_id: str | None = None
    ordre: int = 0


class PagePatchPayload(BaseModel):
    titre: str | None = None
    ordre: int | None = None


class Page(BaseModel):
    id: str
    proprietaire_id: str
    parent_id: str | None = None
    titre: str
    ordre: int
    est_carrefour: bool = False
    created_at: str
    updated_at: str


class BlocPayload(BaseModel):
    page_id: str
    type: str = "texte"
    contenu: dict = {}
    ordre: int = 0


class BlocPatchPayload(BaseModel):
    type: str | None = None
    contenu: dict | None = None
    ordre: int | None = None


class Bloc(BaseModel):
    id: str
    page_id: str
    type: str
    contenu: dict
    ordre: int
    created_at: str
    updated_at: str


def _charger_page_ou_404(page_id: str, utilisateur_id: str) -> dict:
    """Charge une page en vérifiant la propriété, sinon 404 (jamais de 403,
    même logique que _charger_programme_ou_404 dans api/programmes.py)."""
    try:
        res = (
            supabase.table("pages")
            .select("*")
            .eq("id", page_id)
            .eq("proprietaire_id", utilisateur_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture page {page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if not res or not res.data:
        raise erreur_api(404, "PAGE_INTROUVABLE")
    return res.data


def _charger_bloc_ou_404(bloc_id: str, utilisateur_id: str) -> dict:
    """Charge un bloc en vérifiant que sa page appartient à l'appelant."""
    try:
        res = supabase.table("blocs").select("*").eq("id", bloc_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture bloc {bloc_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if not res or not res.data:
        raise erreur_api(404, "BLOC_INTROUVABLE")
    bloc = res.data
    _charger_page_ou_404(bloc["page_id"], utilisateur_id)  # 404 si la page n'est pas à l'appelant
    return bloc


# ================================ Pages ==================================


@router_pages.get("", response_model=list[Page])
def lister_pages_racines(utilisateur=Depends(utilisateur_courant)):
    """Pages sans parent (racines) de l'utilisateur. Pour les sous-pages
    d'une page donnée, voir GET /api/pages/{id}/sous-pages."""
    try:
        res = (
            supabase.table("pages")
            .select("*")
            .eq("proprietaire_id", utilisateur.id)
            .is_("parent_id", "null")
            .order("ordre")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste pages racines {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [Page(**ligne) for ligne in (res.data or [])]


@router_pages.post("", response_model=Page, status_code=201)
def creer_page(payload: PagePayload, utilisateur=Depends(utilisateur_courant)):
    if payload.parent_id:
        _charger_page_ou_404(payload.parent_id, utilisateur.id)  # 404 si le parent n'est pas à l'appelant
    try:
        res = (
            supabase.table("pages")
            .insert(
                {
                    "proprietaire_id": utilisateur.id,
                    "parent_id": payload.parent_id,
                    "titre": payload.titre.strip(),
                    "ordre": payload.ordre,
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création page {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return Page(**res.data[0])


@router_pages.get("/{page_id}")
def lire_page(page_id: str, utilisateur=Depends(utilisateur_courant)):
    """Détail d'une page avec ses sous-pages et ses blocs, dans l'ordre."""
    page = _charger_page_ou_404(page_id, utilisateur.id)
    try:
        sous_pages = (
            supabase.table("pages").select("*").eq("parent_id", page_id).order("ordre").execute()
        )
        blocs = supabase.table("blocs").select("*").eq("page_id", page_id).order("ordre").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (détail page {page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return {**page, "sous_pages": sous_pages.data or [], "blocs": blocs.data or []}


@router_pages.get("/{page_id}/sous-pages", response_model=list[Page])
def lister_sous_pages(page_id: str, utilisateur=Depends(utilisateur_courant)):
    _charger_page_ou_404(page_id, utilisateur.id)
    try:
        res = supabase.table("pages").select("*").eq("parent_id", page_id).order("ordre").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (sous-pages de {page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [Page(**ligne) for ligne in (res.data or [])]


@router_pages.patch("/{page_id}", response_model=Page)
def modifier_page(page_id: str, payload: PagePatchPayload, utilisateur=Depends(utilisateur_courant)):
    _charger_page_ou_404(page_id, utilisateur.id)
    maj = {}
    if payload.titre is not None:
        maj["titre"] = payload.titre.strip()
    if payload.ordre is not None:
        maj["ordre"] = payload.ordre
    if not maj:
        raise erreur_api(400, "AUCUNE_MODIFICATION_FOURNIE")
    maj["updated_at"] = "now()"
    try:
        res = supabase.table("pages").update(maj).eq("id", page_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification page {page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return Page(**res.data[0])


@router_pages.delete("/{page_id}", status_code=204)
def supprimer_page(page_id: str, utilisateur=Depends(utilisateur_courant)):
    """Supprime la page -- cascade SQL sur ses blocs et ses sous-pages
    (voir migration : parent_id et page_id sont tous deux ON DELETE CASCADE)."""
    _charger_page_ou_404(page_id, utilisateur.id)
    try:
        supabase.table("pages").delete().eq("id", page_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression page {page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")


class ReferenceCarrefourPayload(BaseModel):
    type_cible: str
    cible_id: str


class ReferenceCarrefour(BaseModel):
    id: str
    type_cible: str
    cible_id: str
    label: str


@router_pages.get("/{page_id}/carrefour", response_model=list[ReferenceCarrefour])
def lister_carrefour(page_id: str, utilisateur=Depends(utilisateur_courant)):
    _charger_page_ou_404(page_id, utilisateur.id)
    return _lister_references_carrefour(page_id)


@router_pages.post("/{page_id}/carrefour", response_model=ReferenceCarrefour, status_code=201)
def ajouter_carrefour(page_id: str, payload: ReferenceCarrefourPayload, utilisateur=Depends(utilisateur_courant)):
    if payload.type_cible not in TYPES_CIBLE_CARREFOUR:
        raise erreur_api(422, "TYPE_DE_CIBLE_INVALIDE")
    ref = _ajouter_reference_carrefour(utilisateur.id, page_id, payload.type_cible, payload.cible_id)
    if ref is None:
        raise erreur_api(404, "PAGE_OU_CIBLE_INTROUVABLE")
    refs = _lister_references_carrefour(page_id)
    correspondante = next((r for r in refs if r["id"] == ref["id"]), None)
    if correspondante is None:
        raise erreur_api(500, "ERREUR_INCONNUE")
    return correspondante


@router_pages.delete("/{page_id}/carrefour/{reference_id}", status_code=204)
def supprimer_carrefour(page_id: str, reference_id: str, utilisateur=Depends(utilisateur_courant)):
    ok = _supprimer_reference_carrefour(utilisateur.id, page_id, reference_id)
    if not ok:
        raise erreur_api(404, "PAGE_INTROUVABLE")


# ================================ Blocs ===================================


@router_blocs.post("", response_model=Bloc, status_code=201)
def creer_bloc(payload: BlocPayload, utilisateur=Depends(utilisateur_courant)):
    _charger_page_ou_404(payload.page_id, utilisateur.id)
    type_bloc = payload.type if payload.type in TYPES_BLOCS_CONNUS else "texte"
    try:
        res = (
            supabase.table("blocs")
            .insert(
                {
                    "page_id": payload.page_id,
                    "type": type_bloc,
                    "contenu": payload.contenu,
                    "ordre": payload.ordre,
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création bloc, page {payload.page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return Bloc(**res.data[0])


@router_blocs.patch("/{bloc_id}", response_model=Bloc)
def modifier_bloc(bloc_id: str, payload: BlocPatchPayload, utilisateur=Depends(utilisateur_courant)):
    _charger_bloc_ou_404(bloc_id, utilisateur.id)
    maj = {}
    if payload.type is not None:
        maj["type"] = payload.type if payload.type in TYPES_BLOCS_CONNUS else "texte"
    if payload.contenu is not None:
        maj["contenu"] = payload.contenu
    if payload.ordre is not None:
        maj["ordre"] = payload.ordre
    if not maj:
        raise erreur_api(400, "AUCUNE_MODIFICATION_FOURNIE")
    maj["updated_at"] = "now()"
    try:
        res = supabase.table("blocs").update(maj).eq("id", bloc_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification bloc {bloc_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return Bloc(**res.data[0])


@router_blocs.delete("/{bloc_id}", status_code=204)
def supprimer_bloc(bloc_id: str, utilisateur=Depends(utilisateur_courant)):
    _charger_bloc_ou_404(bloc_id, utilisateur.id)
    try:
        supabase.table("blocs").delete().eq("id", bloc_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression bloc {bloc_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
