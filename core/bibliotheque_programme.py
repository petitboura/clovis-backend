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

Étendu le 17/08 (demande Bourama : "il faut même en ajouter si ça ne
suffit pas") aux types "exercice" et "examen" -- voir migration
2026_08_17_extension_emplacements_bibliotheque_exercice_examen.sql.
Un exercice/examen peut donc désormais recevoir un document de la
bibliothèque exactement comme un programme/matière/chapitre.
"""

import logging

from api.auth import supabase
from api.contenu_programme import (
    _lire_chapitre,
    _lire_examen,
    _lire_exercice,
    _lire_matiere,
    _lire_programme,
    _proprietaire_du_chapitre,
)

logging.basicConfig(level=logging.INFO)

TYPES_EMPLACEMENT_BIBLIOTHEQUE = ("programme", "matiere", "chapitre", "exercice", "examen")


def proprietaire_emplacement(type_cible: str, cible_id: str) -> str | None:
    """
    Renvoie l'id du propriétaire (utilisateur) d'un emplacement du
    programme, quel que soit son niveau ("programme"/"matiere"/
    "chapitre"/"exercice"/"examen"). None si l'emplacement n'existe pas.
    Fonction commune utilisée pour vérifier la propriété avant toute
    liaison (bibliothèque ou comportement) -- jamais de confiance dans
    un cible_id fourni tel quel par l'appelant.
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
    if type_cible == "exercice":
        exercice = _lire_exercice(cible_id)
        return _proprietaire_du_chapitre(exercice["chapitre_id"]) if exercice else None
    if type_cible == "examen":
        examen = _lire_examen(cible_id)
        return examen["proprietaire_id"] if examen else None
    return None


def programme_de_emplacement(type_cible: str, cible_id: str) -> str | None:
    """
    Renvoie l'id du PROGRAMME (pas son propriétaire) qui contient cet
    emplacement. Ajouté le 20/08 pour le plugin public "contribution
    libre". Ne couvre PAS "examen" : un examen peut être transverse et
    toucher plusieurs programmes à la fois (voir examen_chapitres +
    migrations/2026_08_14_plugin_examens_transverses.sql), il n'y a donc
    pas UN programme unique à renvoyer -- voir programmes_de_examen et
    emplacement_couvert_par_plugin_public ci-dessous.
    """
    if type_cible == "programme":
        return cible_id
    if type_cible == "matiere":
        matiere = _lire_matiere(cible_id)
        return matiere["programme_id"] if matiere else None
    if type_cible == "chapitre":
        chapitre = _lire_chapitre(cible_id)
        if not chapitre:
            return None
        matiere = _lire_matiere(chapitre["matiere_id"])
        return matiere["programme_id"] if matiere else None
    if type_cible == "exercice":
        exercice = _lire_exercice(cible_id)
        if not exercice:
            return None
        chapitre = _lire_chapitre(exercice["chapitre_id"])
        if not chapitre:
            return None
        matiere = _lire_matiere(chapitre["matiere_id"])
        return matiere["programme_id"] if matiere else None
    return None


def programmes_de_examen(examen_id: str) -> list[str]:
    """
    Tous les programmes touchés par un examen (potentiellement plusieurs
    -- un examen transverse peut couvrir des chapitres de programmes
    différents, voir examen_chapitres). 20/08, contribution libre.
    """
    try:
        chapitre_ids = [
            l["chapitre_id"]
            for l in (
                supabase.table("examen_chapitres").select("chapitre_id").eq("examen_id", examen_id).execute().data
                or []
            )
        ]
        if not chapitre_ids:
            return []
        chapitres = supabase.table("chapitres").select("id, matiere_id").in_("id", chapitre_ids).execute().data or []
        matiere_ids = list({c["matiere_id"] for c in chapitres})
        if not matiere_ids:
            return []
        matieres = supabase.table("matieres").select("id, programme_id").in_("id", matiere_ids).execute().data or []
        return list({m["programme_id"] for m in matieres})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (programmes_de_examen {examen_id}) : {e}")
        return []


def plugin_ouvert_a_la_contribution(programme_id: str) -> bool:
    """
    True si `programme_id` est le programme source d'au moins un plugin
    publié avec contribution_libre=true (20/08/2026, demande Bourama :
    "un plugin public que nous on publie et tout le monde peut y ajouter
    des pdf" -- n'importe quel chapitre existant de la structure, jamais
    de nouvelle matière/chapitre créée par un contributeur, voir
    migrations/2026_08_20_plugin_bibliotheque_publique.sql).
    """
    try:
        res = (
            supabase.table("plugins_programme")
            .select("id")
            .eq("programme_source_id", programme_id)
            .eq("contribution_libre", True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérif plugin contribution_libre, programme {programme_id}) : {e}")
        return False
    return bool(res.data)


def emplacement_couvert_par_plugin_public(type_cible: str, cible_id: str) -> bool:
    """
    True si N'IMPORTE QUI (pas seulement le propriétaire) doit pouvoir
    lire/contribuer à cet emplacement, parce qu'il fait partie d'un
    plugin contribution_libre. Fonction UNIQUE à utiliser partout où
    cette vérification est nécessaire (classer_document, lecture des
    documents, navigation matières/chapitres) -- gère le cas particulier
    des examens transverses (20/08, voir programmes_de_examen : il
    suffit qu'UN SEUL des programmes touchés par l'examen soit
    contribution_libre pour autoriser l'accès à CET examen).
    """
    if type_cible == "examen":
        return any(plugin_ouvert_a_la_contribution(pid) for pid in programmes_de_examen(cible_id))
    programme_id = programme_de_emplacement(type_cible, cible_id)
    return bool(programme_id and plugin_ouvert_a_la_contribution(programme_id))


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
        if type_cible == "exercice":
            res = supabase.table("exercices_programme").select("enonce").eq("id", cible_id).maybe_single().execute()
            if not res or not res.data:
                return None
            enonce = (res.data.get("enonce") or "").strip()
            return (enonce[:60] + "…") if len(enonce) > 60 else (enonce or "Exercice")
        if type_cible == "examen":
            res = supabase.table("examens_programme").select("titre").eq("id", cible_id).maybe_single().execute()
            return res.data["titre"] if res and res.data else None
        if type_cible == "document":
            res = supabase.table("documents_programme").select("titre").eq("id", cible_id).maybe_single().execute()
            return res.data["titre"] if res and res.data else None
        if type_cible == "section":
            res = (
                supabase.table("classements_transversaux")
                .select("label, type")
                .eq("id", cible_id)
                .maybe_single()
                .execute()
            )
            if not res or not res.data:
                return None
            return res.data.get("label") or res.data.get("type") or "Section"
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

    est_proprietaire = proprietaire_emplacement(type_cible, cible_id) == user_id
    if not est_proprietaire:
        # Pas propriétaire de cet emplacement : autorisé quand même si
        # cet emplacement (programme/matière/chapitre/exercice/examen,
        # y compris un examen transverse touchant plusieurs programmes)
        # fait partie d'un plugin en contribution_libre (20/08, demande
        # Bourama confirmée -- exercices ET examens inclus, pas
        # seulement la structure programme/matière/chapitre comme prévu
        # initialement).
        if not emplacement_couvert_par_plugin_public(type_cible, cible_id):
            return {"ok": False, "erreur": "Cet emplacement du programme est introuvable ou ne t'appartient pas."}

    try:
        supabase.table("bibliotheque_emplacements_programme").upsert(
            {"fichier_id": fichier_id, "type_cible": type_cible, "cible_id": cible_id, "ajoute_par": user_id},
            on_conflict="fichier_id,type_cible,cible_id",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (classement document {fichier_id} -> {type_cible}={cible_id}) : {e}")
        return {"ok": False, "erreur": "Erreur lors du classement, réessaie."}
    return {"ok": True}


def declasser_document(user_id: str, fichier_id: str, type_cible: str, cible_id: str) -> dict:
    """Retire un document d'un emplacement du programme (le document lui
    reste dans la bibliothèque, seul le lien disparaît). Autorisé si
    user_id est le propriétaire du FICHIER (comme avant) -- ou, ajouté
    le 20/08 pour la contribution libre, si user_id est celui qui a
    classé ce document à cet emplacement précis (ajoute_par) : un
    contributeur d'un plugin public peut retirer sa propre contribution,
    même s'il ne possède ni le fichier ni l'emplacement."""
    if _proprietaire_fichier(fichier_id) != user_id:
        try:
            lien = (
                supabase.table("bibliotheque_emplacements_programme")
                .select("ajoute_par")
                .eq("fichier_id", fichier_id)
                .eq("type_cible", type_cible)
                .eq("cible_id", cible_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture ajoute_par {fichier_id}) : {e}")
            return {"ok": False, "erreur": "Ce document est introuvable ou ne t'appartient pas."}
        if not lien or not lien.data or lien.data.get("ajoute_par") != user_id:
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


def fichiers_des_plugins_publics(niveaux: list[str]) -> list[str]:
    """
    Tous les fichier_id classés (programme/matière/chapitre) dans un
    plugin en contribution_libre dont le niveau est dans `niveaux`
    (20/08/2026, pour l'outil de chat consulter_bibliotheque_publique --
    voir core/serveur_mcp_generation.py). Si `niveaux` est vide, renvoie
    tous les plugins publics tous niveaux confondus (repli volontaire,
    mieux vaut chercher large que ne rien trouver).
    """
    try:
        requete = supabase.table("plugins_programme").select("programme_source_id").eq("contribution_libre", True)
        if niveaux:
            requete = requete.in_("niveau", niveaux)
        plugins = requete.execute().data or []
        programme_ids = list({p["programme_source_id"] for p in plugins})
        if not programme_ids:
            return []

        matieres = supabase.table("matieres").select("id").in_("programme_id", programme_ids).execute().data or []
        matiere_ids = [m["id"] for m in matieres]
        chapitres = (
            supabase.table("chapitres").select("id").in_("matiere_id", matiere_ids).execute().data
            if matiere_ids
            else []
        ) or []
        chapitre_ids = [c["id"] for c in chapitres]

        cibles = [("programme", programme_ids), ("matiere", matiere_ids), ("chapitre", chapitre_ids)]
        fichier_ids: set[str] = set()
        for type_cible, ids in cibles:
            if not ids:
                continue
            liens = (
                supabase.table("bibliotheque_emplacements_programme")
                .select("fichier_id")
                .eq("type_cible", type_cible)
                .in_("cible_id", ids)
                .execute()
                .data
                or []
            )
            fichier_ids.update(l["fichier_id"] for l in liens)
        return list(fichier_ids)
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (fichiers_des_plugins_publics, niveaux={niveaux}) : {e}")
        return []


# --- Comportements <-> programme ----------------------------------------

TYPES_LIEN_COMPORTEMENT = ("programme", "matiere", "chapitre", "document", "exercice", "examen", "section")


def proprietaire_lien_comportement(type_cible: str, cible_id: str) -> str | None:
    """
    Comme proprietaire_emplacement, mais couvre en plus document/
    exercice/examen/section (types possibles pour un lien de
    comportement, pas pour un classement bibliothèque -- voir docstring
    en tête de fichier). Réutilise _proprietaire_du_chapitre de
    api/contenu_programme.py pour document/exercice (rattachés à un
    seul chapitre), et une résolution directe pour examen/section
    (rattachés à un proprietaire_id, pas à un chapitre -- voir
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
    if type_cible == "section":
        try:
            res = (
                supabase.table("classements_transversaux")
                .select("proprietaire_id")
                .eq("id", cible_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture propriétaire section {cible_id}) : {e}")
            return None
        return res.data["proprietaire_id"] if res and res.data else None
    return None
