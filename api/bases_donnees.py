"""
Section "Notion-like" (Partie 2), lot 3/5 -- bases de révision + gestion
de tâches, 2026-08-20, demande Bourama.

Toute la logique (vérification de propriété via la page, création du
bloc "base_donnees" en même temps que la base) vit dans
core/bases_donnees_llm.py, écrite pour les outils MCP. Ce fichier n'est
qu'une fine couche HTTP par-dessus, même principe que
api/emplacements_bibliotheque_programme.py au-dessus de
core/bibliotheque_programme.py.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from core.erreurs import erreur_api
from core.bases_donnees_llm import (
    TYPES_PROPRIETES_CONNUS,
    ajouter_base as _ajouter_base,
    ajouter_propriete as _ajouter_propriete,
    ajouter_element as _ajouter_element,
    modifier_valeurs_element as _modifier_valeurs_element,
    supprimer_element as _supprimer_element,
    base_appartient_a,
    element_appartient_a,
)

logging.basicConfig(level=logging.INFO)

router_bases_donnees = APIRouter(prefix="/api/bases-donnees", tags=["bases_donnees"])


class BasePayload(BaseModel):
    page_id: str
    titre: str = ""


class ProprietePayload(BaseModel):
    nom: str
    type: str = "texte"
    options: list = []


class ElementPayload(BaseModel):
    valeurs: dict = {}
    parent_element_id: str | None = None


class ValeursPayload(BaseModel):
    valeurs: dict


@router_bases_donnees.post("", status_code=201)
def creer_base(payload: BasePayload, utilisateur=Depends(utilisateur_courant)):
    base = _ajouter_base(utilisateur.id, payload.page_id, payload.titre)
    if base is None:
        raise erreur_api(404, "PAGE_INTROUVABLE")
    return base


@router_bases_donnees.get("/{base_id}")
def lire_base(base_id: str, utilisateur=Depends(utilisateur_courant)):
    base = base_appartient_a(base_id, utilisateur.id)
    if not base:
        raise erreur_api(404, "BASE_INTROUVABLE")
    try:
        proprietes = (
            supabase.table("bases_donnees_proprietes")
            .select("*")
            .eq("base_id", base_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
        elements = (
            supabase.table("bases_donnees_elements")
            .select("*")
            .eq("base_id", base_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
        valeurs = (
            (
                supabase.table("bases_donnees_valeurs")
                .select("*")
                .in_("element_id", [e["id"] for e in elements])
                .execute()
                .data
                or []
            )
            if elements
            else []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (détail base {base_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return {**base, "proprietes": proprietes, "elements": elements, "valeurs": valeurs}


@router_bases_donnees.post("/{base_id}/proprietes", status_code=201)
def creer_propriete(base_id: str, payload: ProprietePayload, utilisateur=Depends(utilisateur_courant)):
    if payload.type not in TYPES_PROPRIETES_CONNUS:
        raise erreur_api(422, "TYPE_DE_PROPRIETE_INVALIDE")
    propriete = _ajouter_propriete(utilisateur.id, base_id, payload.nom, payload.type, payload.options)
    if propriete is None:
        raise erreur_api(404, "BASE_INTROUVABLE")
    return propriete


@router_bases_donnees.post("/{base_id}/elements", status_code=201)
def creer_element(base_id: str, payload: ElementPayload, utilisateur=Depends(utilisateur_courant)):
    element = _ajouter_element(utilisateur.id, base_id, payload.valeurs, payload.parent_element_id)
    if element is None:
        raise erreur_api(404, "BASE_OU_PARENT_INTROUVABLE")
    return element


@router_bases_donnees.patch("/elements/{element_id}")
def modifier_element(element_id: str, payload: ValeursPayload, utilisateur=Depends(utilisateur_courant)):
    ok = _modifier_valeurs_element(utilisateur.id, element_id, payload.valeurs)
    if not ok:
        raise erreur_api(404, "ELEMENT_INTROUVABLE")
    return {"ok": True}


@router_bases_donnees.delete("/elements/{element_id}", status_code=204)
def supprimer_element_route(element_id: str, utilisateur=Depends(utilisateur_courant)):
    ok = _supprimer_element(utilisateur.id, element_id)
    if not ok:
        raise erreur_api(404, "ELEMENT_INTROUVABLE")
