"""
Section "Mes comportements" -- l'étudiant peut enregistrer PLUSIEURS
instructions perso (2026-08-06, demande Bourama : "on peut en mettre
plusieurs hein, pas juste un"), pas un seul texte fourre-tout. Chacune
s'applique EN PLUS du system_prompt déjà résolu (généraliste, matière
d'un enseignant, ou "sans enseignant"), quel que soit l'agent -- pas
seulement les agents à contenu dynamique par matière. Voir l'injection
dans core/main.py::_construire_system_prompt et les endpoints dans
api/comportements_etudiants.py.
"""

import logging
import os

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET = os.environ["SUPABASE_SECRET"]
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)


def lister_comportements(agent_id: str, etudiant_id: str) -> list[dict]:
    """Liste ordonnée (plus ancien -> plus récent) des instructions
    perso de cet étudiant pour cet agent. Liste vide si rien
    d'enregistré -- jamais None, pour simplifier les deux appelants
    (endpoint GET et injection prompt)."""
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("id, texte")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportements {agent_id}/{etudiant_id}) : {e}")
        return []
    return [{"id": ligne["id"], "texte": ligne["texte"]} for ligne in (res.data or []) if ligne.get("texte", "").strip()]


def ajouter_comportement(agent_id: str, etudiant_id: str, texte: str) -> dict:
    texte = texte.strip()
    res = (
        supabase.table("comportements_etudiants")
        .insert({"agent_id": agent_id, "etudiant_id": etudiant_id, "texte": texte})
        .execute()
    )
    ligne = res.data[0]
    return {"id": ligne["id"], "texte": ligne["texte"]}


def modifier_comportement(agent_id: str, etudiant_id: str, comportement_id: str, texte: str) -> dict | None:
    texte = texte.strip()
    res = (
        supabase.table("comportements_etudiants")
        .update({"texte": texte})
        .eq("id", comportement_id)
        .eq("agent_id", agent_id)
        .eq("etudiant_id", etudiant_id)
        .execute()
    )
    if not res.data:
        return None
    ligne = res.data[0]
    return {"id": ligne["id"], "texte": ligne["texte"]}


def supprimer_comportement(agent_id: str, etudiant_id: str, comportement_id: str) -> bool:
    res = (
        supabase.table("comportements_etudiants")
        .delete()
        .eq("id", comportement_id)
        .eq("agent_id", agent_id)
        .eq("etudiant_id", etudiant_id)
        .execute()
    )
    return bool(res.data)
