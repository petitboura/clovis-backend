"""
Connexion IA <-> structure programme (13/08/2026, demande Bourama, sur le
modèle exact de core/comportements_etudiants.py) : le programme
classe/matière/chapitre qu'un étudiant construit dans "Mon espace" existait
déjà côté API (api/programmes.py, api/contenu_programme.py) mais restait
invisible au LLM -- ni pré-injecté, ni consultable via un outil.

Cinq niveaux (15/08/2026 : matières et chapitres séparés en deux appels
distincts, décision Bourama -- consulter_programme renvoyait TOUT le
programme (toutes les matières + tous leurs chapitres) en un seul appel,
ce qui alourdissait trop le message de relance vers le modèle après le
premier appel d'outil -- 413 Payload Too Large systématique sur les
modèles Groq -- et allait à l'encontre du principe "naviguer comme
l'étudiant" : voir une matière, décider d'y entrer ou non, seulement
alors voir ses chapitres) :
- lister_mes_programmes_legers : id/niveau/nom SEULEMENT, pour que le
  modèle sache qu'un programme existe et propose d'y accéder -- coût
  quasi nul, pas besoin d'un routeur (contrairement aux comportements
  où le texte est long : ici la liste légère est déjà minuscule).
- obtenir_structure_programme : matières SEULEMENT (nom + limites),
  SANS leurs chapitres, demandée par le modèle via l'outil
  consulter_programme (voir core/serveur_mcp_generation.py) quand il a
  choisi un programme précis.
- obtenir_chapitres_matiere : chapitres (+ limites) d'UNE matière
  précise, SANS leur contenu (documents/exercices), demandée via
  l'outil consulter_matiere_programme quand le modèle a choisi une
  matière précise dans la liste obtenue à l'étape précédente.
- obtenir_contenu_chapitre : documents + exercices d'UN chapitre précis,
  demandée via l'outil consulter_chapitre_programme quand le modèle a
  choisi un chapitre précis dans les chapitres obtenus à l'étape
  précédente.
- obtenir_examens_programme : examens/devoirs d'UN programme précis
  (titre, type, chapitres couverts -- ce sont les seules données qui
  existent pour un examen, voir examens_programme dans migrations/
  2026_08_12_contenu_pratique_programme.sql, aucun champ de contenu/
  énoncé). Rattachée au NIVEAU PROGRAMME, pas au niveau matière/chapitre,
  comme côté frontend (SectionExamensDuProgramme dans
  EspaceProgrammeContenu.tsx) : un examen peut couvrir plusieurs
  chapitres (et même plusieurs matières) à la fois. Demandée via l'outil
  consulter_examens_programme (14/08).

Voir l'injection dans core/main.py::_construire_system_prompt.
"""

import logging
import os

from supabase import create_client


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)


def lister_mes_programmes_legers(user_id: str) -> list[dict]:
    """Liste légère (id, niveau, nom) des programmes de cet utilisateur --
    jamais la structure complète (voir obtenir_structure_programme). Liste
    vide si rien d'enregistré ou si user_id est vide -- jamais None, pour
    simplifier l'appelant (injection prompt)."""
    if not user_id:
        return []
    try:
        res = (
            supabase.table("programmes")
            .select("id, niveau, nom")
            .eq("proprietaire_id", user_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programmes légers, user_id={user_id}) : {e}")
        return []
    return [
        {"id": ligne["id"], "niveau": ligne["niveau"], "nom": ligne.get("nom") or ""}
        for ligne in (res.data or [])
    ]


def obtenir_structure_programme(user_id: str, programme_id: str) -> str | None:
    """
    Matières d'UN programme précis (nom + limites de cadre officiel si
    renseignées), SANS leurs chapitres -- utilisée par l'outil
    consulter_programme (core/serveur_mcp_generation.py). Autorisé si cet
    user_id est le propriétaire direct, OU s'il a reçu ce programme via
    un code de partage actif (14/08, voir core/codes_partage.py::
    peut_acceder_programme_recu -- import différé ici pour éviter un
    import circulaire, programme_llm et codes_partage ne dépendant sinon
    l'un de l'autre dans aucun sens). Retourne un texte déjà formaté,
    prêt à être renvoyé tel quel au modèle, avec l'id de chaque matière
    pour permettre l'étape suivante (consulter_matiere_programme). None
    si introuvable ou sans accès (jamais de fuite entre utilisateurs).
    """
    if not user_id or not programme_id:
        return None
    try:
        programme_res = (
            supabase.table("programmes")
            .select("id, niveau, nom, proprietaire_id")
            .eq("id", programme_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme {programme_id}) : {e}")
        return None
    if not programme_res or not programme_res.data:
        return None
    programme = programme_res.data
    if programme["proprietaire_id"] != user_id:
        from codes_partage import peut_acceder_programme_recu
        if not peut_acceder_programme_recu(user_id, programme_id):
            return None

    try:
        matieres = (
            supabase.table("matieres")
            .select("id, nom, limites")
            .eq("programme_id", programme_id)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture matières programme {programme_id}) : {e}")
        return None

    lignes = [f"Programme : {programme['niveau']}" + (f" ({programme['nom']})" if programme.get("nom") else "")]
    if not matieres:
        lignes.append("(aucune matière créée pour l'instant)")
    for m in matieres:
        ligne = f"- id={m['id']} — {m['nom']}"
        if m.get("limites"):
            ligne += f" -- cadre/limites : {m['limites']}"
        lignes.append(ligne)
    lignes.append(
        "\nPour voir les chapitres d'une matière précise, appelle consulter_matiere_programme avec son id."
    )

    return "\n".join(lignes)


def obtenir_chapitres_matiere(user_id: str, matiere_id: str) -> str | None:
    """
    Chapitres (+ limites de cadre officiel si renseignées) d'UNE matière
    précise, SANS leur contenu (documents/exercices), utilisée par
    l'outil consulter_matiere_programme (core/serveur_mcp_generation.py)
    -- niveau intermédiaire entre obtenir_structure_programme (choisir
    une matière) et obtenir_contenu_chapitre (choisir un chapitre dans
    cette liste). Vérifiée comme appartenant bien à cet user_id via la
    chaîne matière -> programme (pas de RLS sur ces tables, vérification
    manuelle comme partout ailleurs dans cette API). Retourne un texte
    déjà formaté, prêt à être renvoyé tel quel au modèle, avec l'id de
    chaque chapitre pour permettre l'étape suivante
    (consulter_chapitre_programme). None si introuvable ou n'appartenant
    pas à cet utilisateur.
    """
    if not user_id or not matiere_id:
        return None
    try:
        matiere_res = (
            supabase.table("matieres")
            .select("id, nom, programme_id")
            .eq("id", matiere_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture matière {matiere_id}) : {e}")
        return None
    if not matiere_res or not matiere_res.data:
        return None
    matiere = matiere_res.data

    try:
        programme_res = (
            supabase.table("programmes")
            .select("id, proprietaire_id")
            .eq("id", matiere["programme_id"])
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme {matiere['programme_id']}) : {e}")
        return None
    if not programme_res or not programme_res.data:
        return None
    if programme_res.data["proprietaire_id"] != user_id:
        from codes_partage import peut_acceder_programme_recu
        if not peut_acceder_programme_recu(user_id, matiere["programme_id"]):
            return None

    try:
        chapitres = (
            supabase.table("chapitres")
            .select("id, nom, ordre, limites")
            .eq("matiere_id", matiere_id)
            .order("ordre")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture chapitres matière {matiere_id}) : {e}")
        return None

    lignes = [f"Matière : {matiere['nom']}"]
    if not chapitres:
        lignes.append("(aucun chapitre créé pour l'instant)")
    for c in chapitres:
        ligne = f"- id={c['id']} — {c['nom']}"
        if c.get("limites"):
            ligne += f" -- cadre/limites : {c['limites']}"
        lignes.append(ligne)
    lignes.append(
        "\nPour voir le contenu (documents/exercices) d'un chapitre précis, appelle "
        "consulter_chapitre_programme avec son id."
    )

    return "\n".join(lignes)


def obtenir_contenu_chapitre(user_id: str, chapitre_id: str) -> str | None:
    """
    Contenu réel d'UN chapitre précis (documents + exercices), utilisée
    par l'outil consulter_chapitre_programme (core/serveur_mcp_
    generation.py) -- dernier niveau de navigation, après lister_mes_
    programmes_legers (choisir un programme), obtenir_structure_
    programme (choisir une matière) et obtenir_chapitres_matiere
    (choisir un chapitre dans cette matière). Autorisé si cet user_id
    est propriétaire du programme (via la chaîne chapitre -> matière ->
    programme, pas de RLS sur ces tables, vérification manuelle comme
    partout ailleurs dans cette API), OU s'il a reçu ce programme via un
    code de partage actif (14/08, voir core/codes_partage.py::
    peut_acceder_programme_recu). Retourne un texte déjà formaté, prêt à
    être renvoyé tel quel au modèle. None si introuvable ou sans accès
    (jamais de fuite entre utilisateurs).
    """
    if not user_id or not chapitre_id:
        return None
    try:
        chapitre_res = (
            supabase.table("chapitres")
            .select("id, nom, limites, matiere_id")
            .eq("id", chapitre_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture chapitre {chapitre_id}) : {e}")
        return None
    if not chapitre_res or not chapitre_res.data:
        return None
    chapitre = chapitre_res.data

    try:
        matiere_res = (
            supabase.table("matieres")
            .select("id, nom, programme_id")
            .eq("id", chapitre["matiere_id"])
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture matière {chapitre['matiere_id']}) : {e}")
        return None
    if not matiere_res or not matiere_res.data:
        return None
    matiere = matiere_res.data

    try:
        programme_res = (
            supabase.table("programmes")
            .select("id, proprietaire_id")
            .eq("id", matiere["programme_id"])
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme {matiere['programme_id']}) : {e}")
        return None
    if not programme_res or not programme_res.data:
        return None
    programme = programme_res.data
    if programme["proprietaire_id"] != user_id:
        from codes_partage import peut_acceder_programme_recu
        if not peut_acceder_programme_recu(user_id, programme["id"]):
            return None

    try:
        documents = (
            supabase.table("documents_programme")
            .select("titre, url_ou_contenu")
            .eq("chapitre_id", chapitre_id)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture documents chapitre {chapitre_id}) : {e}")
        documents = []

    # 17/08, Bourama : deux systèmes coexistent pour "ajouter un
    # document à un chapitre" -- l'ancien ci-dessus (titre + lien/texte
    # tapé à la main, table documents_programme), et le classement d'un
    # document de la Bibliothèque personnelle (bouton "classer", table
    # bibliotheque_emplacements_programme, voir core/bibliotheque_
    # programme.py::lister_documents_emplacement). Avant cette
    # correction, cette fonction ne lisait QUE l'ancien système : un
    # PDF/image/audio/vidéo classé dans ce chapitre via la Bibliothèque
    # restait invisible pour l'IA quand elle consultait "le contenu de
    # ce chapitre" (sauf coup de chance via consulter_bibliotheque, qui
    # cherche par pertinence de contenu, pas par emplacement). Les deux
    # listes sont fusionnées ici, sans rien retirer à l'ancien système.
    try:
        from bibliotheque_programme import lister_documents_emplacement
        docs_bibliotheque = lister_documents_emplacement("chapitre", chapitre_id)
    except Exception as e:
        logging.error(f"ERREUR (lecture documents bibliothèque classés chapitre {chapitre_id}) : {e}")
        docs_bibliotheque = []

    try:
        exercices = (
            supabase.table("exercices_programme")
            .select("enonce")
            .eq("chapitre_id", chapitre_id)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture exercices chapitre {chapitre_id}) : {e}")
        exercices = []

    lignes = [f"Chapitre : {matiere['nom']} — {chapitre['nom']}"]
    if chapitre.get("limites"):
        lignes.append(f"Cadre/limites : {chapitre['limites']}")

    lignes.append("\nDocuments :")
    if not documents and not docs_bibliotheque:
        lignes.append("  (aucun document pour l'instant)")
    for d in documents:
        lignes.append(f"  - {d['titre']} : {d['url_ou_contenu']}")
    for d in docs_bibliotheque:
        etiquette = d.get("description") or d["nom_fichier"]
        lignes.append(f"  - {etiquette} : {d['url_publique']}")
    if docs_bibliotheque:
        lignes.append(
            "  (si tu juges utile de montrer un de ces documents en entier plutôt que "
            "de le décrire, inclus son lien dans ta réponse -- ![...](url) pour une "
            "image, [...](url) pour les autres types -- il s'affichera alors "
            "correctement selon son type)"
        )

    lignes.append("\nExercices :")
    if not exercices:
        lignes.append("  (aucun exercice pour l'instant)")
    for i, ex in enumerate(exercices, start=1):
        lignes.append(f"  {i}. {ex['enonce']}")

    return "\n".join(lignes)


def obtenir_examens_programme(user_id: str, programme_id: str) -> str | None:
    """
    Examens/devoirs d'UN programme précis (titre, type, chapitres
    couverts -- ce sont les seules données qui existent pour un examen,
    aucun champ de contenu/énoncé côté base). Utilisée par l'outil
    consulter_examens_programme (core/serveur_mcp_generation.py), au
    NIVEAU PROGRAMME comme côté frontend (SectionExamensDuProgramme) --
    un examen peut couvrir plusieurs chapitres à la fois, contrairement à
    obtenir_contenu_chapitre qui est scopée à UN chapitre. Autorisé si
    cet user_id est propriétaire du programme, OU s'il l'a reçu via un
    code de partage actif (14/08, voir
    core/codes_partage.py::peut_acceder_programme_recu). Retourne un
    texte déjà formaté, prêt à être renvoyé tel quel au modèle. None si
    introuvable ou sans accès.
    """
    if not user_id or not programme_id:
        return None
    try:
        programme_res = (
            supabase.table("programmes")
            .select("id, niveau, nom, proprietaire_id")
            .eq("id", programme_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme {programme_id}) : {e}")
        return None
    if not programme_res or not programme_res.data:
        return None
    programme = programme_res.data
    if programme["proprietaire_id"] != user_id:
        from codes_partage import peut_acceder_programme_recu
        if not peut_acceder_programme_recu(user_id, programme_id):
            return None

    try:
        matieres = (
            supabase.table("matieres")
            .select("id, nom")
            .eq("programme_id", programme_id)
            .execute()
            .data
            or []
        )
        chapitres = (
            supabase.table("chapitres")
            .select("id, matiere_id, nom")
            .in_("matiere_id", [m["id"] for m in matieres])
            .execute()
            .data
            or []
            if matieres
            else []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture matières/chapitres programme {programme_id}) : {e}")
        return None

    chapitre_ids = [c["id"] for c in chapitres]
    noms_chapitres = {
        c["id"]: f"{next((m['nom'] for m in matieres if m['id'] == c['matiere_id']), '?')} — {c['nom']}"
        for c in chapitres
    }

    lignes = [f"Examens/devoirs : {programme['niveau']}" + (f" ({programme['nom']})" if programme.get("nom") else "")]

    if not chapitre_ids:
        lignes.append("(aucun chapitre créé pour l'instant, donc aucun examen possible)")
        return "\n".join(lignes)

    try:
        liens = (
            supabase.table("examen_chapitres")
            .select("examen_id, chapitre_id")
            .in_("chapitre_id", chapitre_ids)
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture examen_chapitres programme {programme_id}) : {e}")
        return None

    examen_ids = sorted({l["examen_id"] for l in liens})
    if not examen_ids:
        lignes.append("(aucun examen pour l'instant)")
        return "\n".join(lignes)

    chapitres_par_examen: dict[str, list[str]] = {}
    for l in liens:
        chapitres_par_examen.setdefault(l["examen_id"], []).append(l["chapitre_id"])

    try:
        examens = (
            supabase.table("examens_programme")
            .select("id, titre, type")
            .in_("id", examen_ids)
            .eq("proprietaire_id", programme["proprietaire_id"])
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture examens_programme {programme_id}) : {e}")
        return None

    for e in examens:
        chs = [noms_chapitres.get(cid, "?") for cid in chapitres_par_examen.get(e["id"], [])]
        lignes.append(f"- [{e['type']}] {e['titre']} -- chapitres couverts : {', '.join(chs) or '?'}")

    return "\n".join(lignes)
