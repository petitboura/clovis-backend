"""
Endpoint de lecture de l'audit synthétique hebdomadaire des corrections
pédagogiques (Point 4, Partie 8, 06/09/2026). Séparé de
api/corrections_pedagogiques.py (CRUD des corrections elles-mêmes) :
brique de logique distincte, voir règle transversale du plan de travail
sur la taille des fichiers.
"""

from fastapi import APIRouter, Depends

from api.auth import utilisateur_courant
from core.audit_hebdomadaire_corrections import calculer_audit_prof

router = APIRouter(prefix="/api/audit-corrections", tags=["audit_hebdomadaire_corrections"])


@router.get("/mon-audit")
def mon_audit(utilisateur=Depends(utilisateur_courant)):
    """Synthèse par tendance des corrections pédagogiques reçues par le
    prof courant (voir Bureau > audit) : notions les plus en difficulté
    si disponibles, sinon repli par type A/B, et liste des signalements
    de type A encore non traités."""
    return calculer_audit_prof(utilisateur.id)
