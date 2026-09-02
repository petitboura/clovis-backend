"""
Cree le 02/09/2026, Bourama : centre de notifications Clovis (bouton
cloche dans le header, web + mobile). Voir core/notifications.py pour la
creation (appelee depuis les 3 points d'accrochage automatiques :
rappels, actions IA en arriere-plan, documents recus par code) et
core/canal_temps_reel.py::notifier_utilisateur pour la diffusion en
direct via WebSocket.

Ne liste/modifie QUE les 4 nouveaux types Clovis (voir
core.notifications.TYPES_VALIDES) -- les anciens types de la table
(follow, comment, rating, ...) restent hors de ce centre pour l'instant
(decision explicite de Bourama, 02/09/2026).
"""

import logging

from fastapi import APIRouter, Depends

from api.auth import supabase, utilisateur_courant
from core.erreurs import erreur_api
from core.notifications import TYPES_VALIDES

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_TYPES_LISTE = list(TYPES_VALIDES)


@router.get("")
def lister_mes_notifications(utilisateur=Depends(utilisateur_courant)):
    """
    Les 50 plus recentes (recentes d'abord), tous types Clovis
    confondus. Pas de pagination pour ce premier lot -- un panneau de
    notifications n'a pas vocation a remonter un historique infini.
    """
    try:
        res = (
            supabase.table("notifications")
            .select("id, type, titre, contenu, lien, lu, created_at")
            .eq("user_id", utilisateur.id)
            .in_("type", _TYPES_LISTE)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lister_mes_notifications user={utilisateur.id}) : {e}")
        raise erreur_api(500, "NOTIFICATIONS_LECTURE_ECHEC")


@router.post("/{notification_id}/lu", status_code=204)
def marquer_lu(notification_id: int, utilisateur=Depends(utilisateur_courant)):
    try:
        supabase.table("notifications").update({"lu": True}).eq("id", notification_id).eq(
            "user_id", utilisateur.id
        ).in_("type", _TYPES_LISTE).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer_lu id={notification_id}, user={utilisateur.id}) : {e}")
        raise erreur_api(500, "NOTIFICATIONS_MAJ_ECHEC")


@router.post("/tout-lu", status_code=204)
def marquer_tout_lu(utilisateur=Depends(utilisateur_courant)):
    try:
        supabase.table("notifications").update({"lu": True}).eq("user_id", utilisateur.id).eq(
            "lu", False
        ).in_("type", _TYPES_LISTE).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer_tout_lu user={utilisateur.id}) : {e}")
        raise erreur_api(500, "NOTIFICATIONS_MAJ_ECHEC")
