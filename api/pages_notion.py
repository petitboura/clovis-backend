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

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from core.erreurs import erreur_api
from core.bibliotheque_fichiers import enregistrer_fichier
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
    "base_donnees",  # bug corrigé le 21/08/2026 -- manquait ici alors que
    # présent dans la copie de core/pages_notion_llm.py (chemin MCP/IA),
    # ce qui rétrogradait silencieusement en "texte" tout bloc base de
    # données créé depuis l'interface (chemin REST, api/blocs).
    "image",     # Partie 2, 22/08/2026 -- contenu = {"url": "...", "nom": "..."}
    "fichier",   # idem, fichier générique (pas forcément une image)
    "video",     # contenu = {"url": "..."} -- lien externe (YouTube etc.), pas d'upload direct
    "embed",     # contenu = {"url": "..."} -- intégration générique (site, doc externe)
    "bascule",   # toggle -- contenu = {"texte": "...", "ouvert": bool}, peut contenir des blocs enfants (parent_bloc_id)
}

TYPES_CIBLE_CARREFOUR = ("programme", "matiere", "chapitre", "document")


class PagePayload(BaseModel):
    titre: str = ""
    parent_id: str | None = None
    ordre: int = 0
    icone: str | None = None


class PagePatchPayload(BaseModel):
    titre: str | None = None
    ordre: int | None = None
    icone: str | None = None


class Page(BaseModel):
    id: str
    proprietaire_id: str
    parent_id: str | None = None
    titre: str
    ordre: int
    est_carrefour: bool = False
    icone: str | None = None
    created_at: str
    updated_at: str


class BlocPayload(BaseModel):
    page_id: str
    type: str = "texte"
    contenu: dict = {}
    ordre: int = 0
    parent_bloc_id: str | None = None


class BlocPatchPayload(BaseModel):
    type: str | None = None
    contenu: dict | None = None
    ordre: int | None = None
    parent_bloc_id: str | None = None
    parent_bloc_id_defini: bool = False  # True si parent_bloc_id doit être appliqué même à None (sortir un bloc de son parent) -- distingue "non fourni" de "remis à la racine"


class Bloc(BaseModel):
    id: str
    page_id: str
    type: str
    contenu: dict
    ordre: int
    parent_bloc_id: str | None = None
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


@router_pages.get("/recherche/tout", response_model=list[Page])
def rechercher_pages(q: str = "", utilisateur=Depends(utilisateur_courant)):
    """Recherche simple sur le titre des pages (insensible à la casse) --
    utilisée par la recherche globale (Cmd+K côté frontend) et
    l'autocomplete de lien [[ ]] / @ dans l'éditeur de bloc. Volontairement
    limitée au titre (pas de recherche plein texte dans le contenu des
    blocs) -- "simple et fiable", même principe que le reste de cette
    section (voir commentaires api/bases_donnees.py).
    Chemin à deux segments (/recherche/tout) : ne collisionne jamais avec
    GET /{page_id} (un seul segment) quel que soit l'ordre d'enregistrement
    des routes.
    """
    q = (q or "").strip()
    if not q:
        return []
    try:
        res = (
            supabase.table("pages")
            .select("*")
            .eq("proprietaire_id", utilisateur.id)
            .ilike("titre", f"%{q}%")
            .order("titre")
            .limit(20)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche pages {utilisateur.id}) : {e}")
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
                    "icone": payload.icone,
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
    if payload.icone is not None:
        maj["icone"] = payload.icone or None
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


TYPES_IMAGE_AUTORISES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
TAILLE_MAX_IMAGE_OCTETS = 5 * 1024 * 1024  # 5 Mo, même limite que api/uploads.py
TAILLE_MAX_FICHIER_OCTETS = 20 * 1024 * 1024  # 20 Mo -- fichier générique (PDF, etc.), plus permissif qu'une image


@router_blocs.post("/upload", response_model=Bloc, status_code=201)
async def uploader_bloc_fichier(
    page_id: str = Form(...),
    type_bloc: str = Form(...),  # "image" ou "fichier"
    ordre: int = Form(0),
    parent_bloc_id: str | None = Form(None),
    fichier: UploadFile = File(...),
    utilisateur=Depends(utilisateur_courant),
):
    """Upload une image ou un fichier générique et crée le bloc
    correspondant en une seule opération (pas de bloc coquille orphelin
    si l'upload échoue). Réutilise core/bibliotheque_fichiers.py, déjà
    utilisé par api/uploads.py pour le chat -- même bucket, même
    mécanisme, `origine="notes"` pour distinguer dans fichiers_uploades.
    """
    _charger_page_ou_404(page_id, utilisateur.id)
    if parent_bloc_id:
        _charger_bloc_ou_404(parent_bloc_id, utilisateur.id)
    if type_bloc not in ("image", "fichier"):
        raise erreur_api(400, "TYPE_DOIT_ETRE_IMAGE_OU_FICHIER")

    contenu_brut = await fichier.read()
    if len(contenu_brut) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    limite = TAILLE_MAX_IMAGE_OCTETS if type_bloc == "image" else TAILLE_MAX_FICHIER_OCTETS
    if len(contenu_brut) > limite:
        raise erreur_api(400, "FICHIER_TROP_LOURD")
    if type_bloc == "image" and fichier.content_type not in TYPES_IMAGE_AUTORISES:
        raise erreur_api(400, "FORMAT_IMAGE_NON_SUPPORTE")

    try:
        ligne = enregistrer_fichier(
            contenu=contenu_brut,
            nom_fichier=fichier.filename or ("image" if type_bloc == "image" else "fichier"),
            type_mime=fichier.content_type or "application/octet-stream",
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=f"Pièce jointe page Notes ({page_id})",
            origine="notes",
        )
    except Exception as e:
        logging.error(f"ERREUR upload bloc {type_bloc} (page {page_id}) : {e}")
        raise erreur_api(500, "ECHEC_DE_L_UPLOAD_REESSAIE")

    try:
        res = (
            supabase.table("blocs")
            .insert(
                {
                    "page_id": page_id,
                    "type": type_bloc,
                    "contenu": {"url": ligne["url_publique"], "nom": fichier.filename or ""},
                    "ordre": ordre,
                    "parent_bloc_id": parent_bloc_id,
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création bloc {type_bloc}, page {page_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return Bloc(**res.data[0])


@router_blocs.post("", response_model=Bloc, status_code=201)
def creer_bloc(payload: BlocPayload, utilisateur=Depends(utilisateur_courant)):
    _charger_page_ou_404(payload.page_id, utilisateur.id)
    type_bloc = payload.type if payload.type in TYPES_BLOCS_CONNUS else "texte"
    if payload.parent_bloc_id:
        _charger_bloc_ou_404(payload.parent_bloc_id, utilisateur.id)  # 404 si le bloc parent n'est pas à l'appelant
    try:
        res = (
            supabase.table("blocs")
            .insert(
                {
                    "page_id": payload.page_id,
                    "type": type_bloc,
                    "contenu": payload.contenu,
                    "ordre": payload.ordre,
                    "parent_bloc_id": payload.parent_bloc_id,
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
    if payload.parent_bloc_id_defini:
        if payload.parent_bloc_id:
            _charger_bloc_ou_404(payload.parent_bloc_id, utilisateur.id)
        maj["parent_bloc_id"] = payload.parent_bloc_id
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
