"""
Audit synthétique hebdomadaire des signalements pédagogiques (Partie 8,
mis à jour le 10/09/2026 suite à la refonte du système -- voir
core/signalements.py). Regroupe par notion les signalements reçus par
un prof pour lui donner une vue de synthèse plutôt qu'une liste brute,
et déclenche une notification systématique une fois par semaine, même
s'il n'y a rien à signaler.

Cadence : voir profs_dus_pour_audit ci-dessous, comparée à la table
audits_hebdomadaires_corrections (persistée, pas un minuteur en
mémoire, même principe que core/notifications_push.py pour les
rappels).
"""

import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from supabase import create_client

from core.notifications import creer_notification

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

DELAI_ENTRE_AUDITS = timedelta(days=7)


def calculer_audit_prof(prof_id: str) -> dict:
    """Synthèse par notion des signalements reçus par ce prof. Les
    signalements encore "nouveau" (jamais ouverts en discussion) sont
    toujours listés individuellement (ce sont les actions concrètes
    attendues du prof)."""
    try:
        res = supabase.table("signalements").select("*").eq("prof_id", prof_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (calcul audit prof {prof_id}) : {e}")
        return {"total": 0, "nouveaux_non_traites": [], "tendances_notions": []}

    lignes = res.data or []

    nouveaux = [
        {
            "id": l["id"],
            "question_texte": l.get("question_texte") or "",
            "reponse_texte": l.get("reponse_texte") or "",
            "probleme_observe": l.get("probleme_observe"),
            "created_at": l.get("created_at"),
        }
        for l in lignes
        if l.get("statut") == "nouveau"
    ]

    avec_notion = [l for l in lignes if l.get("notion_id")]
    compteur_notions = Counter(l["notion_id"] for l in avec_notion)
    tendances_notions = [
        {"notion_id": notion_id, "nombre": nombre}
        for notion_id, nombre in sorted(compteur_notions.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "total": len(lignes),
        "nouveaux_non_traites": nouveaux,
        "tendances_notions": tendances_notions,
    }


def profs_dus_pour_audit() -> list[str]:
    """Profs ayant au moins un signalement reçu (signalements.prof_id
    non nul), dont le dernier audit envoyé date de plus de 7 jours ou
    n'a jamais été envoyé. La notification est systématique une fois
    due, même si aucun nouveau signalement n'est apparu depuis le
    dernier envoi."""
    try:
        res = supabase.table("signalements").select("prof_id").not_.is_("prof_id", "null").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste profs avec signalements) : {e}")
        return []
    profs = {l["prof_id"] for l in (res.data or [])}
    if not profs:
        return []

    try:
        res_envois = supabase.table("audits_hebdomadaires_corrections").select("prof_id, envoye_le").in_("prof_id", list(profs)).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture derniers envois audit) : {e}")
        return []
    derniers_envois = {l["prof_id"]: l["envoye_le"] for l in (res_envois.data or [])}

    maintenant = datetime.now(timezone.utc)
    dus = []
    for prof_id in profs:
        envoye_le = derniers_envois.get(prof_id)
        if envoye_le is None:
            dus.append(prof_id)
            continue
        date_envoi = datetime.fromisoformat(envoye_le.replace("Z", "+00:00"))
        if maintenant - date_envoi >= DELAI_ENTRE_AUDITS:
            dus.append(prof_id)
    return dus


def envoyer_audit_prof(prof_id: str) -> None:
    """Calcule et envoie l'audit hebdomadaire d'un prof, puis marque
    l'envoi (toujours, même sans rien à signaler)."""
    audit = calculer_audit_prof(prof_id)
    total_nouveau = len(audit["nouveaux_non_traites"])
    if total_nouveau == 0:
        titre = "Audit hebdomadaire : rien à signaler"
        contenu = "Aucun nouveau signalement cette semaine."
    else:
        titre = f"Audit hebdomadaire : {total_nouveau} signalement(s) en attente"
        contenu = f"{total_nouveau} signalement(s) pas encore ouverts en discussion."

    creer_notification(prof_id, "audit_hebdomadaire_corrections", titre, contenu, lien="/bureau/audit")

    try:
        supabase.table("audits_hebdomadaires_corrections").upsert(
            {"prof_id": prof_id, "envoye_le": datetime.now(timezone.utc).isoformat()},
            on_conflict="prof_id",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement envoi audit {prof_id}) : {e}")


def verifier_audits_hebdomadaires() -> int:
    """Appelée périodiquement par le planificateur (voir api/main.py) :
    envoie l'audit à chaque prof dû, renvoie le nombre envoyé."""
    profs = profs_dus_pour_audit()
    for prof_id in profs:
        try:
            envoyer_audit_prof(prof_id)
        except Exception as e:
            logging.error(f"ERREUR envoi audit hebdomadaire prof {prof_id} : {e}")
    return len(profs)
