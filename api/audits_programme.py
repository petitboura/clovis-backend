"""
Lecture des audits IA -- désormais trois niveaux (26/08/2026, chantier
"Audits" complet, récap Bourama) : chapitre, matière, programme, plus un
niveau programme lui-même. Écriture réservée à core/audit_programme.py
(boucle planificatrice du lundi, voir api/main.py) -- ce fichier reste
volontairement en LECTURE SEULE pour les 3 GET, pas de PATCH/DELETE :
l'audit est réécrit en place chaque lundi par l'IA, l'étudiant peut le
consulter mais ne le modifie pas directement (voir discussion Bourama
12/08 -- toute modification serait de toute façon écrasée au lundi
suivant).

Seule exception à "lecture seule" : POST .../audits/executer, ajouté pour
pouvoir tester la cascade sans attendre le lundi suivant -- déclenche la
même fonction que la boucle planificatrice, mais pour CE programme
seulement.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from core.audit_programme import auditer_programme_complet
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router_audits_programme = APIRouter(prefix="/api/programmes", tags=["programmes"])


class AuditMatiere(BaseModel):
    matiere_id: str
    matiere_nom: str
    texte: str | None = None
    derniere_execution: str | None = None


class AuditChapitre(BaseModel):
    chapitre_id: str
    chapitre_nom: str
    matiere_id: str
    texte: str | None = None
    derniere_execution: str | None = None


class AuditProgrammeGlobal(BaseModel):
    texte: str | None = None
    derniere_execution: str | None = None


def _verifier_programme(programme_id: str, proprietaire_id: str) -> None:
    try:
        res = (
            supabase.table("programmes")
            .select("id")
            .eq("id", programme_id)
            .eq("proprietaire_id", proprietaire_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification programme {programme_id} pour audits) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if not res or not res.data:
        raise erreur_api(404, "PROGRAMME_INTROUVABLE")


@router_audits_programme.get("/{programme_id}/audits", response_model=list[AuditMatiere])
def lister_audits_programme(programme_id: str, utilisateur=Depends(utilisateur_courant)):
    """Un audit par matière du programme -- `texte`/`derniere_execution`
    sont None si la matière n'a encore jamais été auditée (pas encore de
    contenu à analyser, ou pas encore le premier lundi passé)."""
    _verifier_programme(programme_id, utilisateur.id)

    try:
        matieres = (
            supabase.table("matieres").select("id, nom").eq("programme_id", programme_id).order("created_at").execute().data
            or []
        )
        audits = (
            supabase.table("audits_matiere")
            .select("matiere_id, texte, derniere_execution")
            .eq("proprietaire_id", utilisateur.id)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture audits programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    audits_par_matiere = {a["matiere_id"]: a for a in audits}
    resultat = []
    for matiere in matieres:
        audit = audits_par_matiere.get(matiere["id"])
        resultat.append(
            AuditMatiere(
                matiere_id=matiere["id"],
                matiere_nom=matiere["nom"],
                texte=audit["texte"] if audit else None,
                derniere_execution=audit["derniere_execution"] if audit else None,
            )
        )
    return resultat


@router_audits_programme.get("/{programme_id}/audits/chapitres", response_model=list[AuditChapitre])
def lister_audits_chapitres(programme_id: str, utilisateur=Depends(utilisateur_courant)):
    """Un audit par chapitre de TOUT le programme (toutes matières
    confondues) -- le frontend regroupe par matiere_id pour l'affichage
    imbriqué. Même logique de None que lister_audits_programme si un
    chapitre n'a encore jamais été audité."""
    _verifier_programme(programme_id, utilisateur.id)

    try:
        matieres = (
            supabase.table("matieres").select("id").eq("programme_id", programme_id).execute().data or []
        )
        matiere_ids = [m["id"] for m in matieres]
        chapitres = (
            supabase.table("chapitres")
            .select("id, nom, matiere_id")
            .in_("matiere_id", matiere_ids)
            .order("ordre")
            .execute()
            .data
            if matiere_ids
            else []
        ) or []
        chapitre_ids = [c["id"] for c in chapitres]
        audits = (
            supabase.table("audits_chapitre")
            .select("chapitre_id, texte, derniere_execution")
            .in_("chapitre_id", chapitre_ids)
            .execute()
            .data
            if chapitre_ids
            else []
        ) or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture audits chapitres programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    audits_par_chapitre = {a["chapitre_id"]: a for a in audits}
    resultat = []
    for chapitre in chapitres:
        audit = audits_par_chapitre.get(chapitre["id"])
        resultat.append(
            AuditChapitre(
                chapitre_id=chapitre["id"],
                chapitre_nom=chapitre["nom"],
                matiere_id=chapitre["matiere_id"],
                texte=audit["texte"] if audit else None,
                derniere_execution=audit["derniere_execution"] if audit else None,
            )
        )
    return resultat


@router_audits_programme.get("/{programme_id}/audits/programme", response_model=AuditProgrammeGlobal)
def lire_audit_programme_global(programme_id: str, utilisateur=Depends(utilisateur_courant)):
    """Audit du programme entier (niveau le plus haut de la cascade) --
    texte/derniere_execution à None si jamais encore audité."""
    _verifier_programme(programme_id, utilisateur.id)

    try:
        res = (
            supabase.table("audits_programme")
            .select("texte, derniere_execution")
            .eq("programme_id", programme_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture audit global programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    data = res.data if res else None
    return AuditProgrammeGlobal(
        texte=data["texte"] if data else None,
        derniere_execution=data["derniere_execution"] if data else None,
    )


@router_audits_programme.post("/{programme_id}/audits/executer")
def executer_audits_programme(programme_id: str, utilisateur=Depends(utilisateur_courant)):
    """Déclenchement manuel de la cascade complète pour CE programme --
    pensé pour tester sans attendre le lundi suivant (la boucle
    planificatrice, voir api/main.py, fait la même chose pour TOUS les
    programmes de TOUS les utilisateurs). forcer=True : contrairement à
    la boucle du lundi, un déclenchement manuel explicite doit tout
    régénérer, pas seulement ce qui a changé -- sinon rien ne se passerait
    visiblement lors d'un premier test juste après le lundi automatique."""
    _verifier_programme(programme_id, utilisateur.id)
    try:
        auditer_programme_complet(programme_id, utilisateur.id, forcer=True)
    except Exception as e:
        logging.error(f"ERREUR exécution manuelle audits programme {programme_id} : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return {"statut": "termine"}
