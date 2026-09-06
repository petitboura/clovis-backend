"""Mode actif par conversation (Partie 6, plan confiance pédagogique,
06/09/2026, demande Bourama). Voir Point 3 du document de vision : un
utilisateur peut avoir plusieurs codes rattachés en même temps (voir
core/codes_partage.py) -- ce module retient LEQUEL de ces rattachements
s'applique à une conversation donnée, pour affichage permanent côté
frontend et, plus tard (hors périmètre de cette partie), pour la
résolution du bon scope de comportement.

Table dédiée conversation_mode_actif (migration 2026_09_06), séparée de
codes_partage.py : aucune notion de "conversation" en base aujourd'hui
n'a de ligne propre (conversation_id n'est qu'une clé de regroupement
dans historique_conversations), donc pas de colonne existante où
accrocher cette information.
"""
import logging

from api.auth import supabase


def obtenir_mode_actif(conversation_id: str, user_id: str) -> dict | None:
    """Rattachement actuellement actif pour cette conversation, ou None
    si l'utilisateur n'a encore rien choisi (pas de défaut implicite --
    Point 3 : c'est un choix explicite de l'utilisateur)."""
    try:
        res = (
            supabase.table("conversation_mode_actif")
            .select("conversation_id, rattachement_id, updated_at")
            .eq("conversation_id", conversation_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture mode actif {conversation_id}) : {e}")
        return None
    return res.data if res else None


def _rattachement_appartient_a(rattachement_id: str, user_id: str) -> bool:
    """Vérifie que ce rattachement a bien été reçu par CET utilisateur --
    sans ça, n'importe qui pourrait activer un rattachement d'un autre
    en devinant son id."""
    try:
        res = (
            supabase.table("rattachements_codes")
            .select("id")
            .eq("id", rattachement_id)
            .eq("receveur_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification propriété rattachement {rattachement_id}) : {e}")
        return False
    return bool(res and res.data)


def definir_mode_actif(conversation_id: str, user_id: str, rattachement_id: str | None) -> dict | None:
    """Fixe (ou efface, si rattachement_id est None) le mode actif de
    cette conversation pour cet utilisateur. Upsert sur conversation_id
    seul (clé primaire) -- une conversation n'appartient qu'à un seul
    utilisateur, jamais recréée pour un autre. None si rattachement_id
    est fourni mais n'appartient pas à user_id."""
    if rattachement_id is not None and not _rattachement_appartient_a(rattachement_id, user_id):
        return None
    ligne = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "rattachement_id": rattachement_id,
    }
    try:
        res = (
            supabase.table("conversation_mode_actif")
            .upsert(ligne, on_conflict="conversation_id")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (définition mode actif {conversation_id}) : {e}")
        raise
    return res.data[0] if res.data else ligne
