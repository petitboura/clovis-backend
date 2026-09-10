"""
Audit synthétique hebdomadaire des corrections pédagogiques (Point 4,
Partie 8, 06/09/2026). Regroupe par tendance les signalements reçus par
un prof (voir core/corrections_pedagogiques.py) pour lui donner une vue
de synthèse plutôt qu'une liste brute, et déclenche une notification
systématique une fois par semaine, même s'il n'y a rien à signaler.

Fonctionne en DÉGRADÉ tant que la Partie 1 (structure de notions) n'existe
pas : le regroupement par tendance se fait alors uniquement par type
(A/B), faute d'un identifiant de notion à regrouper. Une fois la Partie 1
en place, notion_id (déjà présent dans le schéma, voir corrections_pedagogiques)
devient le regroupement principal sans migration supplémentaire.

Cadence : voir profs_dus_pour_audit ci-dessous, comparée à la table
audits_hebdomadaires_corrections (persistée, pas un minuteur en mémoire,
même principe que core/notifications_push.py pour les rappels).
"""

import logging
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from supabase import create_client, ClientOptions
from client_http_supabase import nouveau_client_http_supabase

from core.notifications import creer_notification

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET, options=ClientOptions(httpx_client=nouveau_client_http_supabase()))

DELAI_ENTRE_AUDITS = timedelta(days=7)


def calculer_audit_prof(prof_id: str) -> dict:
    """Synthèse par tendance des corrections pédagogiques reçues par ce
    prof : notions les plus en difficulté (si notion_id renseigné, voir
    Partie 1), sinon repli par type (A/B). Les signalements de type A
    non traités sont toujours listés individuellement (ce sont les
    actions concrètes attendues du prof)."""
    try:
        res = supabase.table("corrections_pedagogiques").select("*").eq("prof_id", prof_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (calcul audit prof {prof_id}) : {e}")
        return {"total": 0, "nouveaux_non_traites": [], "tendances_notions": [], "tendances_par_type": []}

    lignes = res.data or []

    nouveaux_non_traites = [
        {
            "id": l["id"],
            "type": l["type"],
            "question_texte": l.get("question_texte") or "",
            "reponse_texte": l.get("reponse_texte") or "",
            "created_at": l.get("created_at"),
        }
        for l in lignes
        if l["type"] == "A" and l.get("statut") == "nouveau"
    ]

    avec_notion = [l for l in lignes if l.get("notion_id")]
    compteur_notions = Counter(l["notion_id"] for l in avec_notion)
    tendances_notions = [
        {"notion_id": notion_id, "nombre": nombre}
        for notion_id, nombre in sorted(compteur_notions.items(), key=lambda x: x[1], reverse=True)
    ]

    compteur_types = Counter(l["type"] for l in lignes)
    tendances_par_type = [
        {"type": type_, "nombre": nombre}
        for type_, nombre in sorted(compteur_types.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "total": len(lignes),
        "nouveaux_non_traites": nouveaux_non_traites,
        "tendances_notions": tendances_notions,
        "tendances_par_type": tendances_par_type,
    }


def profs_dus_pour_audit() -> list[str]:
    """Profs ayant au moins un signalement reçu (corrections_pedagogiques.prof_id
    non nul), dont le dernier audit envoyé date de plus de 7 jours ou n'a
    jamais été envoyé. La notification est systématique une fois due, même
    si aucun nouveau signalement n'est apparu depuis le dernier envoi."""
    try:
        res = supabase.table("corrections_pedagogiques").select("prof_id").not_.is_("prof_id", "null").execute()
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
    l'envoi (toujours, même sans rien à signaler, voir la demande
    explicite de notification systématique)."""
    audit = calculer_audit_prof(prof_id)
    total_nouveau = len(audit["nouveaux_non_traites"])
    if total_nouveau == 0:
        titre = "Audit hebdomadaire : rien à signaler"
        contenu = "Aucun nouveau signalement cette semaine."
    else:
        titre = f"Audit hebdomadaire : {total_nouveau} signalement(s) à corriger"
        contenu = f"{total_nouveau} signalement(s) de type A en attente de correction."

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
