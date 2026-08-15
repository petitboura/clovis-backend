"""
Connexion IA <-> structure programme (13/08/2026, demande Bourama, sur le
modèle exact de core/comportements_etudiants.py) : le programme
classe/matière/chapitre qu'un étudiant construit dans "Mon espace" existait
déjà côté API (api/programmes.py, api/contenu_programme.py) mais restait
invisible au LLM -- ni pré-injecté, ni consultable via un outil.

Trois niveaux (14/08/2026 : ajout du 3e niveau, décision Bourama -- l'IA
doit se naviguer dans le programme comme l'étudiant lui-même, jamais tout
obtenir d'un coup) :
- lister_mes_programmes_legers : id/niveau/nom SEULEMENT, pour que le
  modèle sache qu'un programme existe et propose d'y accéder -- coût
  quasi nul, pas besoin d'un routeur (contrairement aux comportements
  où le texte est long : ici la liste légère est déjà minuscule).
- obtenir_structure_programme : matières -> chapitres (+ limites), SANS
  leur contenu (documents/exercices), demandée par le modèle via l'outil
  consulter_programme (voir core/serveur_mcp_generation.py) quand il a
  choisi un programme précis.
- obtenir_contenu_chapitre : documents + exercices d'UN chapitre précis,
  demandée via l'outil consulter_chapitre_programme quand le modèle a
  choisi un chapitre précis dans la structure obtenue à l'étape
  précédente.
- obtenir_examens_programme : examens/devoirs d'UN programme précis
  (titre, type, chapitres couverts -- ce sont les seules données qui
  existent pour un examen, voir examens_programme dans migrations/
  2026_08_12_contenu_pratique_programme.sql, aucun champ de contenu/
  énoncé). Rattachée au NIVEAU PROGRAMME, pas au niveau chapitre, comme
  côté frontend (SectionExamensDuProgramme dans EspaceProgrammeContenu.
  tsx) : un examen peut couvrir plusieurs chapitres à la fois, donc n'a
  pas sa place dans la navigation par chapitre. Demandée via l'outil
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
    Structure complète d'UN programme précis (matières -> chapitres,
    avec leurs limites de cadre officiel si renseignées), utilisée par
    l'outil consulter_programme (core/serveur_mcp_generation.py).
    Autorisé si cet user_id est le propriétaire direct, OU s'il a reçu
    ce programme via un code de partage actif (14/08, voir
    core/codes_partage.py::peut_acceder_programme_recu -- import différé
    ici pour éviter un import circulaire, programme_llm et codes_partage
    ne dépendant sinon l'un de l'autre dans aucun sens). Retourne un
    texte déjà formaté, prêt à être renvoyé tel quel au modèle. None si
    introuvable ou sans accès (jamais de fuite entre utilisateurs).
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
        chapitres = (
            supabase.table("chapitres")
            .select("id, matiere_id, nom, ordre, limites")
            .in_("matiere_id", [m["id"] for m in matieres])
            .order("ordre")
            .execute()
            .data
            or []
            if matieres
            else []
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture matières/chapitres programme {programme_id}) : {e}")
        return None

    chapitres_par_matiere: dict[str, list[dict]] = {}
    for c in chapitres:
        chapitres_par_matiere.setdefault(c["matiere_id"], []).append(c)

    lignes = [f"Programme : {programme['niveau']}" + (f" ({programme['nom']})" if programme.get("nom") else "")]
    if not matieres:
        lignes.append("(aucune matière créée pour l'instant)")
    for m in matieres:
        lignes.append(f"\nMatière : {m['nom']}" + (f" -- cadre/limites : {m['limites']}" if m.get("limites") else ""))
        chs = chapitres_par_matiere.get(m["id"], [])
        if not chs:
            lignes.append("  (aucun chapitre créé pour l'instant)")
        for c in chs:
            ligne = f"  - {c['nom']}"
            if c.get("limites"):
                ligne += f" -- cadre/limites : {c['limites']}"
            lignes.append(ligne)

    return "\n".join(lignes)


def obtenir_contenu_chapitre(user_id: str, chapitre_id: str) -> str | None:
    """
    Contenu réel d'UN chapitre précis (documents + exercices), utilisée
    par l'outil consulter_chapitre_programme (core/serveur_mcp_
    generation.py) -- 3e niveau de navigation, après lister_mes_
    programmes_legers (choisir un programme) et obtenir_structure_
    programme (choisir un chapitre dans sa structure). Autorisé si cet
    user_id est propriétaire du programme (via la chaîne chapitre ->
    matière -> programme, pas de RLS sur ces tables, vérification
    manuelle comme partout ailleurs dans cette API), OU s'il a reçu ce
    programme via un code de partage actif (14/08, voir
    core/codes_partage.py::peut_acceder_programme_recu). Retourne un
    texte déjà formaté, prêt à être renvoyé tel quel au modèle. None si
    introuvable ou sans accès (jamais de fuite entre utilisateurs).
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
    if not documents:
        lignes.append("  (aucun document pour l'instant)")
    for d in documents:
        lignes.append(f"  - {d['titre']} : {d['url_ou_contenu']}")

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
