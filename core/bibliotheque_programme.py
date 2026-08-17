"""
Lie la bibliothèque personnelle et les comportements aux emplacements du
programme (classe -> matière -> chapitre), 2026-08-16, demande explicite
de Bourama : "les documents dans un programme n'importe où dans le
programme seront dans la bibliothèque avec un libellé et en ajoutant
depuis la bibliothèque, on peut la classer dans un lieu du programme" +
"les comportements peuvent être liés à un programme, un chapitre, ou
quoi que ce soit dans le programme". Option confirmée : un même document
peut être classé à PLUSIEURS emplacements à la fois (many-to-many).

Convention reprise de core/programme_ecriture.py (docstring en tête de
ce fichier) : les helpers de lecture/propriété du squelette programme
(_lire_programme/_lire_matiere/_lire_chapitre) sont réimportés depuis
api/contenu_programme.py, jamais redéfinis ici -- pour ne jamais
désynchroniser les deux. Ce fichier N'EST PAS un serveur MCP
(core/serveur_mcp_*.py) : la convention "ne jamais importer api/*.py"
qui s'applique à ces serveurs-là ne s'applique pas ici, exactement
comme programme_ecriture.py qui suit déjà ce même choix.

Ne touche PAS à documents_programme (ancien mécanisme titre+lien
rattaché uniquement à un chapitre) : coexistence assumée, voir
migration 2026_08_16_liens_bibliotheque_comportements_programme.sql.
"""

import logging

from api.auth import supabase
from api.contenu_programme import _lire_chapitre, _lire_matiere, _lire_programme

logging.basicConfig(level=logging.INFO)

TYPES_EMPLACEMENT_BIBLIOTHEQUE = ("programme", "matiere", "chapitre")


def proprietaire_emplacement(type_cible: str, cible_id: str) -> str | None:
    """
    Renvoie l'id du propriétaire (utilisateur) d'un emplacement du
    programme, quel que soit son niveau ("programme"/"matiere"/
    "chapitre"). None si l'emplacement n'existe pas. Fonction commune
    utilisée pour vérifier la propriété avant toute liaison (bibliothèque
    ou comportement) -- jamais de confiance dans un cible_id fourni tel
    quel par l'appelant.
    """
    if type_cible == "programme":
        programme = _lire_programme(cible_id)
        return programme["proprietaire_id"] if programme else None
    if type_cible == "matiere":
        matiere = _lire_matiere(cible_id)
        if not matiere:
            return None
        programme = _lire_programme(matiere["programme_id"])
        return programme["proprietaire_id"] if programme else None
    if type_cible == "chapitre":
        chapitre = _lire_chapitre(cible_id)
        if not chapitre:
            return None
        matiere = _lire_matiere(chapitre["matiere_id"])
        if not matiere:
            return None
        programme = _lire_programme(matiere["programme_id"])
        return programme["proprietaire_id"] if programme else None
    return None


def libelle_emplacement(type_cible: str, cible_id: str) -> str | None:
    """Libellé lisible d'un emplacement, pour affichage (bibliothèque et
    comportements) -- None si l'emplacement n'existe plus (orphelin)."""
    try:
        if type_cible == "programme":
            res = supabase.table("programmes").select("nom, niveau").eq("id", cible_id).maybe_single().execute()
            if not res or not res.data:
                return None
            return res.data.get("nom") or res.data.get("niveau") or "Programme"
        if type_cible == "matiere":
            res = supabase.table("matieres").select("nom").eq("id", cible_id).maybe_single().execute()
            return res.data["nom"] if res and res.data else None
        if type_cible == "chapitre":
            res = supabase.table("chapitres").select("nom").eq("id", cible_id).maybe_single().execute()
            return res.data["nom"] if res and res.data else None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (libellé emplacement {type_cible}={cible_id}) : {e}")
        return None
    return None


# --- Bibliothèque <-> programme ----------------------------------------


def _proprietaire_fichier(fichier_id: str) -> str | None:
    try:
        res = supabase.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture propriétaire fichier {fichier_id}) : {e}")
        return None
    return res.data["user_id"] if res and res.data else None


def classer_document(user_id: str, fichier_id: str, type_cible: str, cible_id: str) -> dict:
    """
    Classe un document de la bibliothèque à un emplacement du programme
    (many-to-many : un même document peut être classé à plusieurs
    emplacements, un même emplacement peut contenir plusieurs
    documents). Vérifie que le fichier ET l'emplacement appartiennent
    bien à user_id. Idempotent (unique déjà en base) -- reclasser au
    même endroit ne crée pas de doublon.
    """
    if type_cible not in TYPES_EMPLACEMENT_BIBLIOTHEQUE:
        return {"ok": False, "erreur": f"Type d'emplacement invalide : {type_cible}."}
    if _proprietaire_fichier(fichier_id) != user_id:
        return {"ok": False, "erreur": "Ce document est introuvable ou ne t'appartient pas."}
    if proprietaire_emplacement(type_cible, cible_id) != user_id:
        return {"ok": False, "erreur": "Cet emplacement du programme est introuvable ou ne t'appartient pas."}
    try:
        supabase.table("bibliotheque_emplacements_programme").upsert(
            {"fichier_id": fichier_id, "type_cible": type_cible, "cible_id": cible_id},
            on_conflict="fichier_id,type_cible,cible_id",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (classement document {fichier_id} -> {type_cible}={cible_id}) : {e}")
        return {"ok": False, "erreur": "Erreur lors du classement, réessaie."}
    return {"ok": True}


def declasser_document(user_id: str, fichier_id: str, type_cible: str, cible_id: str) -> dict:
    """Retire un document d'un emplacement du programme (le document lui
    reste dans la bibliothèque, seul le lien disparaît)."""
    if _proprietaire_fichier(fichier_id) != user_id:
        return {"ok": False, "erreur": "Ce document est introuvable ou ne t'appartient pas."}
    try:
        supabase.table("bibliotheque_emplacements_programme").delete().eq("fichier_id", fichier_id).eq(
            "type_cible", type_cible
        ).eq("cible_id", cible_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (déclassement document {fichier_id}) : {e}")
        return {"ok": False, "erreur": "Erreur lors du déclassement, réessaie."}
    return {"ok": True}


def lister_emplacements_document(fichier_id: str) -> list[dict]:
    """Tous les emplacements où ce document est classé, avec leur
    libellé résolu. Emplacements orphelins (cible supprimée depuis)
    ignorés silencieusement, comme classement_transversal_items."""
    try:
        res = (
            supabase.table("bibliotheque_emplacements_programme")
            .select("type_cible, cible_id")
            .eq("fichier_id", fichier_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture emplacements document {fichier_id}) : {e}")
        return []
    resultats = []
    for ligne in res.data or []:
        libelle = libelle_emplacement(ligne["type_cible"], ligne["cible_id"])
        if libelle is not None:
            resultats.append({"type_cible": ligne["type_cible"], "cible_id": ligne["cible_id"], "libelle": libelle})
    return resultats


def lister_documents_emplacement(type_cible: str, cible_id: str) -> list[dict]:
    """Tous les documents de la bibliothèque classés à cet emplacement
    précis (utilisé pour afficher les documents d'un chapitre/matière/
    programme donné, ex. dans l'éditeur de programme)."""
    try:
        liens = (
            supabase.table("bibliotheque_emplacements_programme")
            .select("fichier_id")
            .eq("type_cible", type_cible)
            .eq("cible_id", cible_id)
            .execute()
        ).data or []
        if not liens:
            return []
        ids = [l["fichier_id"] for l in liens]
        fichiers = (
            supabase.table("fichiers_uploades")
            .select("id, nom_fichier, description, type_mime, url_publique, created_at")
            .in_("id", ids)
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture documents emplacement {type_cible}={cible_id}) : {e}")
        return []
    return fichiers


# --- Comportements <-> programme ----------------------------------------

TYPES_LIEN_COMPORTEMENT = ("programme", "matiere", "chapitre", "document", "exercice", "examen")


def proprietaire_lien_comportement(type_cible: str, cible_id: str) -> str | None:
    """
    Comme proprietaire_emplacement, mais couvre en plus document/
    exercice/examen (types possibles pour un lien de comportement, pas
    pour un classement bibliothèque -- voir docstring en tête de
    fichier). Réutilise _proprietaire_du_chapitre de
    api/contenu_programme.py pour document/exercice (rattachés à un
    seul chapitre), et une résolution directe pour examen (rattaché à
    un proprietaire_id, pas à un chapitre -- voir
    2026_08_12_contenu_pratique_programme.sql).
    """
    if type_cible in ("programme", "matiere", "chapitre"):
        return proprietaire_emplacement(type_cible, cible_id)

    from api.contenu_programme import _lire_document, _lire_exercice, _lire_examen, _proprietaire_du_chapitre

    if type_cible == "document":
        document = _lire_document(cible_id)
        return _proprietaire_du_chapitre(document["chapitre_id"]) if document else None
    if type_cible == "exercice":
        exercice = _lire_exercice(cible_id)
        return _proprietaire_du_chapitre(exercice["chapitre_id"]) if exercice else None
    if type_cible == "examen":
        examen = _lire_examen(cible_id)
        return examen["proprietaire_id"] if examen else None
    return None
