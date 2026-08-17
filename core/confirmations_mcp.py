"""
Confirmations en attente pour l'outil MCP externe discuter_avec_clovis
(17/08/2026, demande Bourama). Voir migrations/2026_08_17_confirmations_mcp_espace.sql
pour le detail du pourquoi (etat_reprise contient des secrets en clair
dans table_routage -- cle API Tavily, jetons Notion/GitHub -- donc il ne
doit jamais transiter par un parametre d'outil MCP vers Claude).

Convention reprise des autres modules core/*.py : client Supabase propre
a ce fichier (voir docstring core/registre_outils.py / serveur_mcp_espace.py).
"""

import logging
import os
from datetime import datetime, timezone

from supabase import create_client

_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
_supabase = create_client(_SUPABASE_URL, _SUPABASE_SECRET)

_TABLE = "confirmations_mcp_espace"


def creer_confirmation(proprietaire_id: str, nom_outil: str, message: str, arguments: dict, etat_reprise: dict) -> str | None:
    """
    Enregistre une confirmation en attente et renvoie son id (a montrer a
    Claude/l'utilisateur -- jamais etat_reprise lui-meme, qui reste
    exclusivement en base). None si l'ecriture echoue.
    """
    try:
        ligne = _supabase.table(_TABLE).insert({
            "proprietaire_id": proprietaire_id,
            "nom_outil": nom_outil,
            "message": message,
            "arguments": arguments or {},
            "etat_reprise": etat_reprise,
        }).execute().data
    except Exception as e:
        logging.error(f"ERREUR creer_confirmation : {e}")
        return None
    return ligne[0]["id"] if ligne else None


def recuperer_confirmation(id_confirmation: str, proprietaire_id: str) -> dict | None:
    """
    Renvoie la ligne (dont etat_reprise) si elle existe, appartient a
    proprietaire_id, et n'est pas expiree. None sinon -- jamais
    d'exception laissee remonter a l'appelant (id invalide/mal forme
    inclus).
    """
    try:
        lignes = (
            _supabase.table(_TABLE)
            .select("*")
            .eq("id", id_confirmation)
            .eq("proprietaire_id", proprietaire_id)
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR recuperer_confirmation : {e}")
        return None
    if not lignes:
        return None
    ligne = lignes[0]
    expire_a = ligne.get("expire_a")
    if expire_a:
        try:
            if datetime.fromisoformat(expire_a.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                supprimer_confirmation(id_confirmation)
                return None
        except Exception:
            pass  # format de date inattendu : on ne bloque pas la confirmation pour ça
    return ligne


def supprimer_confirmation(id_confirmation: str) -> None:
    """Usage unique : supprimee juste apres consommation (approuvee ou non), ou a expiration."""
    try:
        _supabase.table(_TABLE).delete().eq("id", id_confirmation).execute()
    except Exception as e:
        logging.error(f"ERREUR supprimer_confirmation : {e}")
