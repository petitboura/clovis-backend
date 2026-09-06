"""Restriction majeur/mineur (Partie 7, plan confiance pédagogique,
06/09/2026, demande Bourama -- voir Point 3). Aucun champ de majorité
n'existait avant (confirmé par l'audit du plan) : `profiles.est_majeur`,
nullable, NULL = jamais renseigné, traité comme majeur (comportement
actuel inchangé pour tout compte qui n'a jamais répondu à cette
question -- rien ne doit se bloquer pour les comptes existants).

Deux règles, seulement quand est_majeur vaut explicitement False :
- accès au chat bloqué tant qu'aucun code n'est rattaché (voir
  api/chat.py) ;
- mode actif à choix unique, pas de changement libre en cours de
  conversation une fois un premier choix fait (voir
  api/mode_actif_conversation.py).

Version "neutre par défaut configurée par l'équipe Clovis" pour un
mineur sans code explicitement HORS PÉRIMÈTRE (voir vision, Point 3) --
un mineur sans code voit juste l'accès bloqué, pas de version dégradée.
"""
import logging

from api.auth import supabase
from core.codes_partage import lister_mes_rattachements


def est_mineur(user_id: str) -> bool:
    """True seulement si l'utilisateur a explicitement déclaré être
    mineur. False si jamais renseigné (NULL) ou déclaré majeur."""
    try:
        res = (
            supabase.table("profiles")
            .select("est_majeur")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture est_majeur {user_id}) : {e}")
        return False
    if not res or not res.data:
        return False
    return res.data.get("est_majeur") is False


def acces_chat_bloque_pour_mineur(user_id: str) -> bool:
    """True si CET utilisateur est mineur ET n'a aucun code rattaché
    (voir core/codes_partage.py::lister_mes_rattachements -- ne compte
    que les rattachements vers un code encore actif)."""
    if not est_mineur(user_id):
        return False
    return len(lister_mes_rattachements(user_id)) == 0
