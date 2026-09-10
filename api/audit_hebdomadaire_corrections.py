"""
Endpoint de lecture de l'audit synthétique hebdomadaire des
signalements pédagogiques (Partie 8, mis à jour le 10/09/2026). Séparé
de api/signalements_pedagogiques.py (CRUD des signalements eux-mêmes) :
brique de logique distincte, voir règle transversale du plan de travail
sur la taille des fichiers.
"""

from fastapi import APIRouter, Depends

from api.auth import utilisateur_courant
from core.audit_hebdomadaire_corrections import calculer_audit_prof

router = APIRouter(prefix="/api/audit-corrections", tags=["audit_hebdomadaire_corrections"])


@router.get("/mon-audit")
def mon_audit(utilisateur=Depends(utilisateur_courant)):
    """Synthèse par notion des signalements reçus par le prof courant
    (voir Bureau > audit), et liste des signalements encore "nouveau"
    (pas ouverts en discussion)."""
    return calculer_audit_prof(utilisateur.id)
