"""
Ecritures MCP sur la structure programme (classe -> matiere -> chapitre) et
son contenu (documents, exercices, examens), 2026-08-14, demande Bourama :
"il faut que l'IA sache le modifier ... regarde la structure" -- l'IA doit
pouvoir ajouter/modifier des textes, des liens, des exercices par chapitre
ou par plusieurs chapitres (examens), en plus des comportements deja geres
par core/comportements_etudiants.py.

Choix de perimetre (confirme avec Bourama) :
- Suppression -> TOUJOURS via confirmation utilisateur avant execution
  (voir OUTILS_SENSIBLES dans registre_outils.py), jamais silencieuse.
- Ajout/modification -> execution directe, mais chaque ecriture enregistre
  l'etat "avant" dans historique_ecritures_programme (voir migration
  2026_08_14) pour permettre annuler_derniere_modification().
- Classements transversaux (semestre/annee/section) delibfrement HORS
  perimetre ici : pas mentionnes par Bourama, fonctionnalite annexe au
  squelette programme -- a ajouter plus tard si demande explicitement.

Ce fichier ne redefinit PAS les helpers de lecture/propriete deja ecrits
dans api/contenu_programme.py et api/programmes.py (_lire_chapitre,
_lire_matiere, _lire_programme, _proprietaire_du_chapitre, etc.) : il les
reimporte tels quels, pour ne jamais dupliquer la logique d'acces et ne
jamais risquer de desynchroniser les deux. Verification de propriete faite
ici via user_id direct (pas de FastAPI Request/Depends, ce module est
appele depuis les outils MCP -- voir core/serveur_mcp_generation.py).
"""

import logging

from api.auth import supabase
from api.contenu_programme import (
    _lire_chapitre,
    _lire_matiere,
    _lire_programme,
    _lire_document,
    _lire_exercice,
    _lire_examen,
    _proprietaire_du_chapitre,
    _nettoyer_classements_pour_cible,
)

logging.basicConfig(level=logging.INFO)

TABLES_PAR_TYPE = {
    "programme": "programmes",
    "matiere": "matieres",
    "chapitre": "chapitres",
    "document": "documents_programme",
    "exercice": "exercices_programme",
    "examen": "examens_programme",
    "comportement": "comportements_etudiants",
}


# ---------------------------------------------------------------------------
# Historique / annulation
# ---------------------------------------------------------------------------


def _enregistrer_historique(proprietaire_id: str, type_cible: str, cible_id: str, action: str, avant: dict | None) -> None:
    """Best-effort, comme journaliser() dans api/journal.py : un souci
    d'ecriture de l'historique ne doit jamais faire echouer l'action
    metier elle-meme (deja executee a ce stade)."""
    try:
        supabase.table("historique_ecritures_programme").insert({
            "proprietaire_id": proprietaire_id,
            "type_cible": type_cible,
            "cible_id": cible_id,
            "action": action,
            "avant": avant,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR historique_ecritures_programme (type={type_cible}, cible={cible_id}) : {e}")


def annuler_derniere_modification(proprietaire_id: str) -> dict | None:
    """
    Annule la derniere ecriture (ajout ou modification) de CET utilisateur,
    quel que soit son type. None si aucune ecriture annulable trouvee
    (jamais rien fait, ou deja tout annule).

    - action="cree" -> supprime la ligne creee.
    - action="modifie" -> restaure les champs stockes dans `avant`.

    Renvoie un petit dict descriptif (type_cible, cible_id, action) pour
    que l'outil MCP puisse annoncer clairement ce qui a ete annule, ou
    None si rien n'etait a annuler.
    """
    try:
        res = (
            supabase.table("historique_ecritures_programme")
            .select("id, type_cible, cible_id, action, avant")
            .eq("proprietaire_id", proprietaire_id)
            .eq("annule", False)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture derniere ecriture {proprietaire_id}) : {e}")
        return None

    lignes = res.data or []
    if not lignes:
        return None
    ligne = lignes[0]
    table = TABLES_PAR_TYPE.get(ligne["type_cible"])
    if not table:
        return None

    try:
        if ligne["action"] == "cree":
            supabase.table(table).delete().eq("id", ligne["cible_id"]).execute()
            if ligne["type_cible"] in ("document", "exercice", "examen"):
                _nettoyer_classements_pour_cible(ligne["type_cible"], ligne["cible_id"])
        elif ligne["action"] == "modifie" and ligne.get("avant"):
            avant = dict(ligne["avant"])
            # examen : chapitre_ids est gere a part (table de jointure
            # examen_chapitres), jamais une vraie colonne de examens_programme.
            chapitre_ids_avant = avant.pop("chapitre_ids", None)
            if avant:
                supabase.table(table).update(avant).eq("id", ligne["cible_id"]).execute()
            if chapitre_ids_avant is not None:
                supabase.table("examen_chapitres").delete().eq("examen_id", ligne["cible_id"]).execute()
                if chapitre_ids_avant:
                    supabase.table("examen_chapitres").insert(
                        [{"examen_id": ligne["cible_id"], "chapitre_id": cid} for cid in chapitre_ids_avant]
                    ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (annulation ecriture {ligne['id']}) : {e}")
        return None

    try:
        supabase.table("historique_ecritures_programme").update({"annule": True}).eq("id", ligne["id"]).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquage annule {ligne['id']}) : {e}")

    return {"type_cible": ligne["type_cible"], "cible_id": ligne["cible_id"], "action": ligne["action"]}


# ---------------------------------------------------------------------------
# Programmes
# ---------------------------------------------------------------------------


def ajouter_programme(proprietaire_id: str, niveau: str, nom: str | None = None) -> dict:
    niveau = niveau.strip()
    res = (
        supabase.table("programmes")
        .insert({"proprietaire_id": proprietaire_id, "niveau": niveau, "nom": (nom or "").strip() or None})
        .execute()
    )
    ligne = res.data[0]
    _enregistrer_historique(proprietaire_id, "programme", ligne["id"], "cree", None)
    return ligne


def modifier_programme(proprietaire_id: str, programme_id: str, niveau: str | None = None, nom: str | None = None) -> dict | None:
    programme = _lire_programme(programme_id)
    if not programme or programme["proprietaire_id"] != proprietaire_id:
        return None
    avant = {}
    maj = {}
    if niveau is not None:
        avant["niveau"] = programme.get("niveau")
        maj["niveau"] = niveau.strip()
    if nom is not None:
        avant["nom"] = programme.get("nom")
        maj["nom"] = nom.strip() or None
    if not maj:
        return programme
    maj["updated_at"] = "now()"
    res = supabase.table("programmes").update(maj).eq("id", programme_id).execute()
    _enregistrer_historique(proprietaire_id, "programme", programme_id, "modifie", avant)
    return res.data[0]


# ---------------------------------------------------------------------------
# Matieres
# ---------------------------------------------------------------------------


def ajouter_matiere(proprietaire_id: str, programme_id: str, nom: str, limites: str | None = None) -> dict | None:
    programme = _lire_programme(programme_id)
    if not programme or programme["proprietaire_id"] != proprietaire_id:
        return None
    res = (
        supabase.table("matieres")
        .insert({"programme_id": programme_id, "nom": nom.strip(), "limites": (limites or "").strip() or None})
        .execute()
    )
    ligne = res.data[0]
    _enregistrer_historique(proprietaire_id, "matiere", ligne["id"], "cree", None)
    return ligne


def _proprietaire_de_matiere(matiere_id: str) -> tuple[dict | None, str | None]:
    matiere = _lire_matiere(matiere_id)
    if not matiere:
        return None, None
    programme = _lire_programme(matiere["programme_id"])
    if not programme:
        return None, None
    return matiere, programme["proprietaire_id"]


def modifier_matiere(proprietaire_id: str, matiere_id: str, nom: str | None = None, limites: str | None = None) -> dict | None:
    matiere, reel_proprietaire = _proprietaire_de_matiere(matiere_id)
    if not matiere or reel_proprietaire != proprietaire_id:
        return None
    res_lecture = supabase.table("matieres").select("nom, limites").eq("id", matiere_id).maybe_single().execute()
    actuel = res_lecture.data or {}
    avant, maj = {}, {}
    if nom is not None:
        avant["nom"] = actuel.get("nom")
        maj["nom"] = nom.strip()
    if limites is not None:
        avant["limites"] = actuel.get("limites")
        maj["limites"] = limites.strip() or None
    if not maj:
        return actuel
    maj["updated_at"] = "now()"
    res = supabase.table("matieres").update(maj).eq("id", matiere_id).execute()
    _enregistrer_historique(proprietaire_id, "matiere", matiere_id, "modifie", avant)
    return res.data[0]


# ---------------------------------------------------------------------------
# Chapitres
# ---------------------------------------------------------------------------


def ajouter_chapitre(proprietaire_id: str, matiere_id: str, nom: str, ordre: int | None = None, limites: str | None = None) -> dict | None:
    matiere, reel_proprietaire = _proprietaire_de_matiere(matiere_id)
    if not matiere or reel_proprietaire != proprietaire_id:
        return None
    res = (
        supabase.table("chapitres")
        .insert({
            "matiere_id": matiere_id,
            "nom": nom.strip(),
            "ordre": ordre if ordre is not None else 0,
            "limites": (limites or "").strip() or None,
        })
        .execute()
    )
    ligne = res.data[0]
    _enregistrer_historique(proprietaire_id, "chapitre", ligne["id"], "cree", None)
    return ligne


def modifier_chapitre(proprietaire_id: str, chapitre_id: str, nom: str | None = None, ordre: int | None = None, limites: str | None = None) -> dict | None:
    reel_proprietaire = _proprietaire_du_chapitre(chapitre_id)
    if reel_proprietaire != proprietaire_id:
        return None
    actuel = supabase.table("chapitres").select("nom, ordre, limites").eq("id", chapitre_id).maybe_single().execute().data or {}
    avant, maj = {}, {}
    if nom is not None:
        avant["nom"] = actuel.get("nom")
        maj["nom"] = nom.strip()
    if ordre is not None:
        avant["ordre"] = actuel.get("ordre")
        maj["ordre"] = ordre
    if limites is not None:
        avant["limites"] = actuel.get("limites")
        maj["limites"] = limites.strip() or None
    if not maj:
        return actuel
    maj["updated_at"] = "now()"
    res = supabase.table("chapitres").update(maj).eq("id", chapitre_id).execute()
    _enregistrer_historique(proprietaire_id, "chapitre", chapitre_id, "modifie", avant)
    return res.data[0]


# ---------------------------------------------------------------------------
# Documents (textes ou liens rattaches a un chapitre)
# ---------------------------------------------------------------------------


def ajouter_document(proprietaire_id: str, chapitre_id: str, titre: str, url_ou_contenu: str) -> dict | None:
    reel_proprietaire = _proprietaire_du_chapitre(chapitre_id)
    if reel_proprietaire != proprietaire_id:
        return None
    res = (
        supabase.table("documents_programme")
        .insert({"chapitre_id": chapitre_id, "titre": titre.strip(), "url_ou_contenu": url_ou_contenu.strip()})
        .execute()
    )
    ligne = res.data[0]
    _enregistrer_historique(proprietaire_id, "document", ligne["id"], "cree", None)
    return ligne


def modifier_document(proprietaire_id: str, document_id: str, titre: str | None = None, url_ou_contenu: str | None = None) -> dict | None:
    document = _lire_document(document_id)
    if not document:
        return None
    reel_proprietaire = _proprietaire_du_chapitre(document["chapitre_id"])
    if reel_proprietaire != proprietaire_id:
        return None
    avant, maj = {}, {}
    if titre is not None:
        avant["titre"] = document.get("titre")
        maj["titre"] = titre.strip()
    if url_ou_contenu is not None:
        avant["url_ou_contenu"] = document.get("url_ou_contenu")
        maj["url_ou_contenu"] = url_ou_contenu.strip()
    if not maj:
        return document
    res = supabase.table("documents_programme").update(maj).eq("id", document_id).execute()
    _enregistrer_historique(proprietaire_id, "document", document_id, "modifie", avant)
    return res.data[0]


# ---------------------------------------------------------------------------
# Exercices (rattaches a un seul chapitre)
# ---------------------------------------------------------------------------


def ajouter_exercice(proprietaire_id: str, chapitre_id: str, enonce: str) -> dict | None:
    reel_proprietaire = _proprietaire_du_chapitre(chapitre_id)
    if reel_proprietaire != proprietaire_id:
        return None
    res = supabase.table("exercices_programme").insert({"chapitre_id": chapitre_id, "enonce": enonce.strip()}).execute()
    ligne = res.data[0]
    _enregistrer_historique(proprietaire_id, "exercice", ligne["id"], "cree", None)
    return ligne


def modifier_exercice(proprietaire_id: str, exercice_id: str, enonce: str) -> dict | None:
    exercice = _lire_exercice(exercice_id)
    if not exercice:
        return None
    reel_proprietaire = _proprietaire_du_chapitre(exercice["chapitre_id"])
    if reel_proprietaire != proprietaire_id:
        return None
    avant = {"enonce": exercice.get("enonce")}
    res = (
        supabase.table("exercices_programme")
        .update({"enonce": enonce.strip(), "updated_at": "now()"})
        .eq("id", exercice_id)
        .execute()
    )
    _enregistrer_historique(proprietaire_id, "exercice", exercice_id, "modifie", avant)
    return res.data[0]


# ---------------------------------------------------------------------------
# Examens / devoirs / problemes composites (peuvent couvrir PLUSIEURS
# chapitres -- table de jointure examen_chapitres)
# ---------------------------------------------------------------------------


def _chapitres_appartiennent_tous(chapitre_ids: list[str], proprietaire_id: str) -> bool:
    return all(_proprietaire_du_chapitre(cid) == proprietaire_id for cid in chapitre_ids)


def ajouter_examen(proprietaire_id: str, titre: str, type_: str, chapitre_ids: list[str]) -> dict | None:
    if not chapitre_ids or not _chapitres_appartiennent_tous(chapitre_ids, proprietaire_id):
        return None
    res = (
        supabase.table("examens_programme")
        .insert({"proprietaire_id": proprietaire_id, "titre": titre.strip(), "type": type_})
        .execute()
    )
    ligne = res.data[0]
    supabase.table("examen_chapitres").insert(
        [{"examen_id": ligne["id"], "chapitre_id": cid} for cid in chapitre_ids]
    ).execute()
    _enregistrer_historique(proprietaire_id, "examen", ligne["id"], "cree", None)
    ligne["chapitre_ids"] = chapitre_ids
    return ligne


def modifier_examen(proprietaire_id: str, examen_id: str, titre: str | None = None, type_: str | None = None, chapitre_ids: list[str] | None = None) -> dict | None:
    examen = _lire_examen(examen_id)
    if not examen or examen["proprietaire_id"] != proprietaire_id:
        return None
    if chapitre_ids is not None and not _chapitres_appartiennent_tous(chapitre_ids, proprietaire_id):
        return None

    avant, maj = {}, {}
    if titre is not None:
        avant["titre"] = examen.get("titre")
        maj["titre"] = titre.strip()
    if type_ is not None:
        avant["type"] = examen.get("type")
        maj["type"] = type_
    if chapitre_ids is not None:
        actuels = supabase.table("examen_chapitres").select("chapitre_id").eq("examen_id", examen_id).execute()
        avant["chapitre_ids"] = [l["chapitre_id"] for l in (actuels.data or [])]

    if maj:
        maj["updated_at"] = "now()"
        supabase.table("examens_programme").update(maj).eq("id", examen_id).execute()
    if chapitre_ids is not None:
        supabase.table("examen_chapitres").delete().eq("examen_id", examen_id).execute()
        supabase.table("examen_chapitres").insert(
            [{"examen_id": examen_id, "chapitre_id": cid} for cid in chapitre_ids]
        ).execute()
    if not maj and chapitre_ids is None:
        return examen

    _enregistrer_historique(proprietaire_id, "examen", examen_id, "modifie", avant)
    resultat = supabase.table("examens_programme").select("*").eq("id", examen_id).maybe_single().execute().data
    if resultat is not None and chapitre_ids is not None:
        resultat["chapitre_ids"] = chapitre_ids
    return resultat


# ---------------------------------------------------------------------------
# Suppressions -- reservees aux outils MCP marques SENSIBLES (voir
# OUTILS_SENSIBLES dans registre_outils.py) : le flux d'appel d'outil de
# core/main.py s'arrete AVANT execution et demande confirmation explicite a
# l'utilisateur, donc pas d'historique/annulation ici -- une suppression
# confirmee est volontaire, pas a annuler comme un ajout/modif automatique.
# ---------------------------------------------------------------------------


def supprimer_programme(proprietaire_id: str, programme_id: str) -> bool:
    programme = _lire_programme(programme_id)
    if not programme or programme["proprietaire_id"] != proprietaire_id:
        return False
    matieres = supabase.table("matieres").select("id").eq("programme_id", programme_id).execute().data or []
    matiere_ids = [m["id"] for m in matieres]
    chapitre_ids = []
    if matiere_ids:
        chapitres = supabase.table("chapitres").select("id").in_("matiere_id", matiere_ids).execute().data or []
        chapitre_ids = [c["id"] for c in chapitres]
    doc_ids, exercice_ids = [], []
    if chapitre_ids:
        docs = supabase.table("documents_programme").select("id").in_("chapitre_id", chapitre_ids).execute().data or []
        exs = supabase.table("exercices_programme").select("id").in_("chapitre_id", chapitre_ids).execute().data or []
        doc_ids, exercice_ids = [d["id"] for d in docs], [e["id"] for e in exs]
    supabase.table("programmes").delete().eq("id", programme_id).execute()
    for mid in matiere_ids:
        _nettoyer_classements_pour_cible("matiere", mid)
    for cid in chapitre_ids:
        _nettoyer_classements_pour_cible("chapitre", cid)
    for did in doc_ids:
        _nettoyer_classements_pour_cible("document", did)
    for eid in exercice_ids:
        _nettoyer_classements_pour_cible("exercice", eid)
    return True


def supprimer_matiere(proprietaire_id: str, matiere_id: str) -> bool:
    matiere, reel_proprietaire = _proprietaire_de_matiere(matiere_id)
    if not matiere or reel_proprietaire != proprietaire_id:
        return False
    chapitres = supabase.table("chapitres").select("id").eq("matiere_id", matiere_id).execute().data or []
    chapitre_ids = [c["id"] for c in chapitres]
    doc_ids, exercice_ids = [], []
    if chapitre_ids:
        docs = supabase.table("documents_programme").select("id").in_("chapitre_id", chapitre_ids).execute().data or []
        exs = supabase.table("exercices_programme").select("id").in_("chapitre_id", chapitre_ids).execute().data or []
        doc_ids, exercice_ids = [d["id"] for d in docs], [e["id"] for e in exs]
    supabase.table("matieres").delete().eq("id", matiere_id).execute()
    _nettoyer_classements_pour_cible("matiere", matiere_id)
    for cid in chapitre_ids:
        _nettoyer_classements_pour_cible("chapitre", cid)
    for did in doc_ids:
        _nettoyer_classements_pour_cible("document", did)
    for eid in exercice_ids:
        _nettoyer_classements_pour_cible("exercice", eid)
    return True


def supprimer_chapitre(proprietaire_id: str, chapitre_id: str) -> bool:
    if _proprietaire_du_chapitre(chapitre_id) != proprietaire_id:
        return False
    docs = supabase.table("documents_programme").select("id").eq("chapitre_id", chapitre_id).execute().data or []
    exs = supabase.table("exercices_programme").select("id").eq("chapitre_id", chapitre_id).execute().data or []
    supabase.table("chapitres").delete().eq("id", chapitre_id).execute()
    _nettoyer_classements_pour_cible("chapitre", chapitre_id)
    for d in docs:
        _nettoyer_classements_pour_cible("document", d["id"])
    for e in exs:
        _nettoyer_classements_pour_cible("exercice", e["id"])
    return True


def supprimer_document(proprietaire_id: str, document_id: str) -> bool:
    document = _lire_document(document_id)
    if not document or _proprietaire_du_chapitre(document["chapitre_id"]) != proprietaire_id:
        return False
    supabase.table("documents_programme").delete().eq("id", document_id).execute()
    _nettoyer_classements_pour_cible("document", document_id)
    return True


def supprimer_exercice(proprietaire_id: str, exercice_id: str) -> bool:
    exercice = _lire_exercice(exercice_id)
    if not exercice or _proprietaire_du_chapitre(exercice["chapitre_id"]) != proprietaire_id:
        return False
    supabase.table("exercices_programme").delete().eq("id", exercice_id).execute()
    _nettoyer_classements_pour_cible("exercice", exercice_id)
    return True


def supprimer_examen(proprietaire_id: str, examen_id: str) -> bool:
    examen = _lire_examen(examen_id)
    if not examen or examen["proprietaire_id"] != proprietaire_id:
        return False
    supabase.table("examens_programme").delete().eq("id", examen_id).execute()
    _nettoyer_classements_pour_cible("examen", examen_id)
    return True
