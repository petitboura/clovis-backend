"""
Ajouté le 2026-07-21 (demande Bourama : l'utilisateur final doit pouvoir
voir/modifier/effacer ce que la plateforme retient de lui d'une session à
l'autre, pas seulement le profil dynamique par agent -- voir api/agents.py
`/mon-profil`).

Lit et écrit `conversation_summaries` (résumé long-terme, table déjà
utilisée par core/main.py : `_charger_resume_memoire` /
`_mettre_a_jour_resume_si_besoin`). Contrairement au profil dynamique
(agent_user_profiles, un par agent), ce résumé est UNIQUE par utilisateur,
valable pour tous les agents de la plateforme (compte unifié, voir
docstring de _charger_resume_memoire) -- pas de agent_id ici.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/memoire", tags=["memoire"])


class Memoire(BaseModel):
    resume: str = ""


class ModifierMemoirePayload(BaseModel):
    resume: str


@router.get("", response_model=Memoire)
def obtenir_ma_memoire(utilisateur=Depends(utilisateur_courant)):
    try:
        res = (
            supabase.table("conversation_summaries")
            .select("summary")
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture conversation_summaries user={utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_CHARGER_LA_MEMOIRE_POUR")

    return Memoire(resume=(res.data or {}).get("summary") or "" if res else "")


@router.patch("", status_code=204)
def modifier_ma_memoire(payload: ModifierMemoirePayload, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Correction manuelle par l'utilisateur -- même logique que
    `/api/agents/{id}/mon-profil` : il peut réécrire lui-même ce résumé
    (corriger une erreur, préciser quelque chose) sans attendre le
    prochain cycle de _mettre_a_jour_resume_si_besoin.
    """
    try:
        supabase.table("conversation_summaries").upsert({
            "user_id": utilisateur.id,
            "summary": payload.resume.strip(),
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (upsert conversation_summaries user={utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_D_ENREGISTRER_LA_MEMOIRE_POUR")

    journaliser(action="memoire.modifiee_par_user", user_id=utilisateur.id, cible_type="profile", cible_id=utilisateur.id, request=request)


@router.delete("", status_code=204)
def effacer_ma_memoire(request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    "Oublie tout ce que tu sais de moi" -- remet le résumé à zéro pour
    tous les agents de la plateforme (contrairement à
    `/api/agents/{id}/mon-profil` DELETE, qui ne touche qu'un seul agent).
    """
    try:
        supabase.table("conversation_summaries").delete().eq("user_id", utilisateur.id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (delete conversation_summaries user={utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_D_EFFACER_LA_MEMOIRE_POUR")

    journaliser(action="memoire.effacee_par_user", user_id=utilisateur.id, cible_type="profile", cible_id=utilisateur.id, request=request)
