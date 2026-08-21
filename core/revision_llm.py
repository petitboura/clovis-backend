"""
Section "Notion-like" (Partie 2) -- répétition espacée (lot 4/5),
2026-08-20, demande Bourama. Dépend du lot 3 (core/bases_donnees_llm.py)
: se branche sur les éléments déjà existants, ne recrée rien.

Algorithme : SM-2 simplifié (base de l'algorithme d'Anki), avec 4
niveaux de réponse au lieu de l'échelle 0-5 d'origine (plus simple pour
un étudiant) : echec, difficile, correct, facile -- mappés sur les
qualités SM-2 0, 3, 4, 5.

Règles (par élément, à chaque réponse) :
- echec (qualite < 3) : on repart de zéro -- repetitions=0, intervalle=1 jour.
- sinon : repetitions += 1
  - 1re répétition réussie -> intervalle = 1 jour
  - 2e répétition réussie -> intervalle = 6 jours
  - au-delà -> intervalle = intervalle précédent * facteur_facilite (arrondi)
- facteur_facilite ajusté à chaque réponse (jamais sous 1.3, plus la
  qualité est basse plus il baisse) -- formule SM-2 standard.
"""

import logging
import math
import os
from datetime import datetime, timedelta, timezone

from supabase import create_client

from core.bases_donnees_llm import element_appartient_a


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

QUALITES_CONNUES = {"echec": 0, "difficile": 3, "correct": 4, "facile": 5}


def _pages_de_utilisateur(user_id: str) -> list[str]:
    try:
        res = supabase.table("pages").select("id").eq("proprietaire_id", user_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (pages de {user_id}) : {e}")
        return []
    return [p["id"] for p in (res.data or [])]


def _bases_de_utilisateur(user_id: str) -> list[str]:
    page_ids = _pages_de_utilisateur(user_id)
    if not page_ids:
        return []
    try:
        res = supabase.table("bases_donnees").select("id").in_("page_id", page_ids).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (bases de {user_id}) : {e}")
        return []
    return [b["id"] for b in (res.data or [])]


def lister_elements_a_reviser(user_id: str, base_id: str | None = None) -> list[dict]:
    """Éléments dont la prochaine révision est due (aujourd'hui ou avant),
    tous appartenant à cet utilisateur -- toutes ses bases de révision
    confondues, sauf si base_id précise une base en particulier. Liste
    légère (id élément, id base, prochaine_revision) -- utilise
    consulter_base_donnees pour voir le contenu détaillé d'un élément."""
    if not user_id:
        return []
    if base_id:
        if base_id not in _bases_de_utilisateur(user_id):
            return []
        base_ids = [base_id]
    else:
        base_ids = _bases_de_utilisateur(user_id)
    if not base_ids:
        return []
    try:
        elements = (
            supabase.table("bases_donnees_elements").select("id, base_id").in_("base_id", base_ids).execute().data
            or []
        )
        if not elements:
            return []
        element_ids = [e["id"] for e in elements]
        base_id_par_element = {e["id"]: e["base_id"] for e in elements}
        etats = (
            supabase.table("revision_etats")
            .select("element_id, prochaine_revision")
            .in_("element_id", element_ids)
            .lte("prochaine_revision", datetime.now(timezone.utc).isoformat())
            .order("prochaine_revision")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (éléments à réviser, user_id={user_id}) : {e}")
        return []
    return [
        {
            "element_id": e["element_id"],
            "base_id": base_id_par_element.get(e["element_id"]),
            "prochaine_revision": e["prochaine_revision"],
        }
        for e in etats
    ]


def _calculer_nouvel_etat(etat_actuel: dict | None, qualite: int) -> dict:
    """Pure -- calcule le nouvel état SM-2 à partir de l'état précédent
    (ou des valeurs par défaut si c'est la première révision) et de la
    qualité de la réponse (0-5)."""
    repetitions = etat_actuel["repetitions"] if etat_actuel else 0
    intervalle = etat_actuel["intervalle_jours"] if etat_actuel else 1
    facteur = etat_actuel["facteur_facilite"] if etat_actuel else 2.5

    facteur = facteur + (0.1 - (5 - qualite) * (0.08 + (5 - qualite) * 0.02))
    facteur = max(1.3, facteur)

    if qualite < 3:
        repetitions = 0
        intervalle = 1
    else:
        repetitions += 1
        if repetitions == 1:
            intervalle = 1
        elif repetitions == 2:
            intervalle = 6
        else:
            intervalle = max(1, math.ceil(intervalle * facteur))

    maintenant = datetime.now(timezone.utc)
    return {
        "prochaine_revision": (maintenant + timedelta(days=intervalle)).isoformat(),
        "intervalle_jours": intervalle,
        "facteur_facilite": round(facteur, 2),
        "repetitions": repetitions,
        "derniere_revision": maintenant.isoformat(),
        "updated_at": maintenant.isoformat(),
    }


def enregistrer_reponse(user_id: str, element_id: str, qualite_texte: str) -> dict | None:
    """Enregistre une réponse de révision et recalcule la prochaine
    date -- crée l'état de révision au passage si c'est la première
    fois que cet élément est révisé. None si element_id invalide, ne
    correspond pas à cet utilisateur, ou qualite_texte inconnu."""
    if not user_id or qualite_texte not in QUALITES_CONNUES:
        return None
    if not element_appartient_a(element_id, user_id):
        return None
    qualite = QUALITES_CONNUES[qualite_texte]
    try:
        existant = (
            supabase.table("revision_etats")
            .select("repetitions, intervalle_jours, facteur_facilite")
            .eq("element_id", element_id)
            .maybe_single()
            .execute()
        )
        etat_actuel = existant.data if existant and existant.data else None
        nouvel_etat = _calculer_nouvel_etat(etat_actuel, qualite)
        res = (
            supabase.table("revision_etats")
            .upsert({"element_id": element_id, **nouvel_etat}, on_conflict="element_id")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement réponse révision, élément {element_id}) : {e}")
        return None
    return res.data[0] if res.data else None
