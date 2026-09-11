"""
Logique de la Partie 3 du chantier "confiance pedagogique" (voir
clovis-plan-travail-10-parties.md) : mise a jour conversationnelle de
l'avancement des notions (Partie 1) et regle de comportement de l'IA
face a une notion pas encore vue.

Fichier separe de core/programme_notions.py (CRUD de base, Partie 1)
comme prevu par le plan de travail : cette couche ajoute la resolution
"en langage naturel" (retrouver un code par son nom, une notion par son
nom, creer a la volee si absente) au-dessus du CRUD strict de la
Partie 1, sans jamais dupliquer sa logique -- toutes les ecritures
passent par ses fonctions existantes.
"""

import logging
import os

from supabase import create_client, ClientOptions
from client_http_supabase import nouveau_client_http_supabase

from core.embeddings import est_en_pause_quota_gemini, est_erreur_quota_gemini, vectoriser
from core.programme_notions import (
    STATUTS_VALIDES,
    code_appartient_a,
    lister_notions,
    creer_notion,
    changer_statut_notion,
)

SEUIL_SIMILARITE_NOTIONS = 0.5
MATCH_COUNT_NOTIONS = 3

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET, options=ClientOptions(httpx_client=nouveau_client_http_supabase()))

REGLES_VALIDES = {"bloquer", "contourner", "signaler"}


def _normaliser(texte: str) -> str:
    return (texte or "").strip().lower()


def resoudre_code_par_nom(proprietaire_id: str, nom_ou_code: str) -> dict | None:
    """Retrouve UN des codes de proprietaire_id par son nom affiche ou son
    code exact (insensible a la casse pour le nom). None si aucun ou si
    plusieurs codes partagent le meme nom (ambiguite -- l'appelant doit
    alors demander de preciser, jamais deviner lequel)."""
    try:
        res = (
            supabase.table("codes_partage")
            .select("id, code, nom")
            .eq("proprietaire_id", proprietaire_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (resolution code par nom, {proprietaire_id}) : {e}")
        return None
    codes = res.data or []
    cible = _normaliser(nom_ou_code)
    # Le code exact (ex. "ABC123") est unique par construction -- priorite
    # a cette correspondance avant de chercher par nom, qui peut lui
    # etre duplique par le meme proprietaire.
    for c in codes:
        if _normaliser(c.get("code")) == cible:
            return c
    correspondances = [c for c in codes if _normaliser(c.get("nom")) == cible]
    if len(correspondances) == 1:
        return correspondances[0]
    return None


def toutes_notions_code(code_id: str) -> list[dict]:
    try:
        res = (
            supabase.table("notions")
            .select("id, code_id, notion_parent_id, nom, statut, ordre, regle_comportement, consigne_llm")
            .eq("code_id", code_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture notions du code {code_id}) : {e}")
        return []
    return res.data or []


def rechercher_notions_semantique(
    code_id: str,
    texte_requete: str,
    match_count: int = MATCH_COUNT_NOTIONS,
    seuil: float = SEUIL_SIMILARITE_NOTIONS,
) -> list[dict]:
    """Recherche vectorielle des notions d'un code (09/09/2026, demande
    Bourama : remplace le matching texte strict cote consultation eleve,
    voir migrations/2026_09_09_recherche_semantique_notions.sql). Tolere
    une reformulation, une faute de frappe ou un synonyme, contrairement
    a l'ancien matching par egalite stricte sur le nom (bug 4/5 du
    08/09/2026).

    Chaque resultat contient id/nom/statut/notion_parent_id/
    regle_comportement/consigne_llm/similarite, triee par pertinence
    decroissante. Liste vide si aucune notion vectorisee du code ne
    depasse `seuil`, si le code n'a aucune notion, ou si la recherche
    echoue (Gemini en pause quota, erreur reseau) : jamais d'exception
    remontee a l'appelant, toujours loggue pour rester traçable
    (contrairement au silence total de l'ancien systeme)."""
    if est_en_pause_quota_gemini():
        logging.warning(f"Recherche semantique notions ignoree pour code {code_id} (pause quota Gemini en cours).")
        return []
    try:
        vecteur = vectoriser(texte_requete, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        if est_erreur_quota_gemini(str(e)):
            logging.error(f"QUOTA GEMINI épuisé (recherche notions, code {code_id}).")
        else:
            logging.error(f"ERREUR VECTORISATION recherche notions (Gemini, code {code_id}) : {e}")
        return []
    try:
        resultats = supabase.rpc(
            "recherche_notions",
            {"query_embedding": vecteur, "match_count": match_count, "p_code_id": code_id, "p_seuil_similarite": seuil},
        ).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE RPC recherche_notions (code {code_id}) : {e}")
        return []
    if resultats:
        logging.info(
            f"Recherche semantique notions (code {code_id}) : requete=\"{texte_requete[:80]}\" "
            f"-> {len(resultats)} candidat(s), meilleur score={resultats[0]['similarite']:.3f}."
        )
    else:
        logging.info(
            f"Recherche semantique notions (code {code_id}) : requete=\"{texte_requete[:80]}\" "
            f"-> aucun candidat au-dessus du seuil {seuil}."
        )
    return resultats


def trouver_notion_par_nom(code_id: str, nom_notion: str) -> dict | None:
    """Cherche une notion par son nom EXACT (insensible a la casse) dans
    TOUT l'arbre du code, quel que soit son niveau de profondeur. None si
    aucune ou si plusieurs notions partagent le meme nom dans ce code
    (ambiguite -- jamais devinee, voir docstring des outils)."""
    cible = _normaliser(nom_notion)
    correspondances = [n for n in toutes_notions_code(code_id) if _normaliser(n.get("nom")) == cible]
    if len(correspondances) == 1:
        return correspondances[0]
    return None


def mettre_a_jour_ou_creer_avancement(
    proprietaire_id: str,
    code_id: str,
    nom_notion: str,
    nouveau_statut: str,
    notion_parent_nom: str | None = None,
) -> dict | None:
    """Met a jour le statut de la notion nom_notion dans code_id, en la
    creant a la volee (racine, ou sous notion_parent_nom si fourni et
    trouve) si elle n'existe pas encore -- c'est la capacite demandee
    explicitement par la Partie 3 ("pas seulement avancer un statut
    existant"). None si le code n'appartient pas a proprietaire_id, si
    nouveau_statut est invalide, ou si notion_parent_nom est fourni mais
    introuvable/ambigu (jamais rattache au mauvais parent par
    supposition)."""
    if nouveau_statut not in STATUTS_VALIDES or not code_appartient_a(code_id, proprietaire_id):
        return None
    notion = trouver_notion_par_nom(code_id, nom_notion)
    if notion is None:
        parent_id = None
        if notion_parent_nom:
            parent = trouver_notion_par_nom(code_id, notion_parent_nom)
            if parent is None:
                return None
            parent_id = parent["id"]
        notion = creer_notion(code_id, proprietaire_id, nom_notion, parent_id)
        if notion is None:
            return None
    return changer_statut_notion(notion["id"], code_id, proprietaire_id, nouveau_statut)


def definir_regle_comportement(
    proprietaire_id: str,
    code_id: str,
    nom_notion: str,
    regle: str | None,
) -> dict | None:
    """Definit (ou retire, si regle est None) la regle de comportement
    d'une notion EXISTANTE -- contrairement a l'avancement, une regle ne
    cree jamais de notion a la volee (rattacher une regle a une notion
    qui n'existe pas encore n'a pas de sens). None si le code n'appartient
    pas a proprietaire_id, si la notion est introuvable/ambigue, ou si
    regle n'est ni valide ni None."""
    if regle is not None and regle not in REGLES_VALIDES:
        return None
    if not code_appartient_a(code_id, proprietaire_id):
        return None
    notion = trouver_notion_par_nom(code_id, nom_notion)
    if notion is None:
        return None
    try:
        res = (
            supabase.table("notions")
            .update({"regle_comportement": regle})
            .eq("id", notion["id"])
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (definition regle notion {notion['id']}) : {e}")
        return None
    return res.data[0] if res.data else None


def definir_consigne_llm(
    proprietaire_id: str,
    code_id: str,
    nom_notion: str,
    consigne: str | None,
) -> dict | None:
    """Definit (ou retire, si consigne est None/vide) la consigne texte
    libre a destination de l'IA sur une notion EXISTANTE -- 08/09/2026,
    demande Bourama : contrairement a regle_comportement (bloquer/
    contourner/signaler, uniquement pour une notion pas encore vue),
    cette consigne est un texte libre, applicable quel que soit le
    statut de la notion (vue ou non), et coexiste avec regle_comportement
    (les deux peuvent etre definis en meme temps sur la meme notion).
    Meme regle d'heritage que regle_comportement (voir
    consigne_effective_pour_notion) : une consigne posee sur un niveau
    s'applique a tout ce qui est dessous, sauf override plus precis.
    None si le code n'appartient pas a proprietaire_id ou si la notion
    est introuvable/ambigue."""
    if not code_appartient_a(code_id, proprietaire_id):
        return None
    notion = trouver_notion_par_nom(code_id, nom_notion)
    if notion is None:
        return None
    valeur = (consigne or "").strip() or None
    try:
        res = (
            supabase.table("notions")
            .update({"consigne_llm": valeur})
            .eq("id", notion["id"])
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (definition consigne notion {notion['id']}) : {e}")
        return None
    return res.data[0] if res.data else None


def consigne_effective_pour_notion(notion: dict, toutes_notions: list[dict]) -> str | None:
    """Remonte la chaine des parents jusqu'a trouver une consigne_llm
    definie -- meme logique d'heritage que regle_effective_pour_notion
    (une consigne posee sur un chapitre s'applique a toutes ses
    sous-notions qui n'ont pas leur propre consigne plus precise). None
    si aucun ancetre (ni la notion elle-meme) n'a de consigne."""
    par_id = {n["id"]: n for n in toutes_notions}
    courante = notion
    vus: set[str] = set()
    while courante is not None and courante["id"] not in vus:
        vus.add(courante["id"])
        if courante.get("consigne_llm"):
            return courante["consigne_llm"]
        parent_id = courante.get("notion_parent_id")
        courante = par_id.get(parent_id) if parent_id else None
    return None


def regle_effective_pour_notion(notion: dict, toutes_notions: list[dict]) -> str | None:
    """Remonte la chaine des parents jusqu'a trouver une regle_comportement
    definie -- une regle posee sur une notion "chapitre" s'applique donc
    a toutes ses sous-notions qui n'ont pas leur propre regle plus
    precise. None si aucun ancetre (ni la notion elle-meme) n'a de regle."""
    par_id = {n["id"]: n for n in toutes_notions}
    courante = notion
    vus: set[str] = set()
    while courante is not None and courante["id"] not in vus:
        vus.add(courante["id"])
        if courante.get("regle_comportement"):
            return courante["regle_comportement"]
        parent_id = courante.get("notion_parent_id")
        courante = par_id.get(parent_id) if parent_id else None
    return None


def formatter_arborescence(notions: list[dict]) -> str:
    """Affichage texte indente par profondeur, notions triees par ordre au
    sein d'un meme parent -- pense pour etre lu par le grand modele, pas
    pour du rendu visuel (voir Partie 2, frontend, pour l'affichage
    utilisateur)."""
    if not notions:
        return "Aucune notion pour ce code pour l'instant."
    par_parent: dict[str | None, list[dict]] = {}
    for n in notions:
        par_parent.setdefault(n.get("notion_parent_id"), []).append(n)
    for enfants in par_parent.values():
        enfants.sort(key=lambda n: n.get("ordre", 0))

    lignes = []

    def _ajouter(parent_id, profondeur):
        for n in par_parent.get(parent_id, []):
            indentation = "  " * profondeur
            regle = f", regle: {n['regle_comportement']}" if n.get("regle_comportement") else ""
            consigne = f", consigne: \"{n['consigne_llm']}\"" if n.get("consigne_llm") else ""
            lignes.append(f"{indentation}- {n['nom']} [statut: {n['statut']}{regle}{consigne}] (id: {n['id']})")
            _ajouter(n["id"], profondeur + 1)

    _ajouter(None, 0)
    return "\n".join(lignes)


def resoudre_code_actif_eleve(receveur_id: str, rattachement_id: str | None = None) -> dict | None | list[dict]:
    """Retrouve le code sur lequel receveur_id est rattache.

    `rattachement_id` (08/09/2026, mode actif -- demande Bourama, corrige
    le fait que cette fonction ignorait totalement le mode actif choisi
    pour une conversation) : si fourni (voir
    core/mode_actif_conversation.py), résout DIRECTEMENT le code de ce
    rattachement précis, sans jamais deviner ni tomber dans l'ambiguïté,
    même si receveur_id a plusieurs codes. None si ce rattachement
    n'appartient pas (ou plus) à receveur_id -- jamais résolu à la place
    par un autre rattachement.

    Sans `rattachement_id` (mode actif pas choisi, ou pas de conversation
    connue) : comportement inchangé -- renvoie le code (dict id/nom) si
    un seul rattachement existe, None si aucun, ou la LISTE des codes
    candidats si plusieurs (ambiguïté, laissée telle quelle à
    l'appelant)."""
    if rattachement_id:
        try:
            res = (
                supabase.table("rattachements_codes")
                .select("codes_partage(id, nom, code)")
                .eq("id", rattachement_id)
                .eq("receveur_id", receveur_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (resolution code actif via mode actif, rattachement {rattachement_id}) : {e}")
            return None
        if not res or not res.data or not res.data.get("codes_partage"):
            return None
        return res.data["codes_partage"]
    try:
        res = (
            supabase.table("rattachements_codes")
            .select("code_id, codes_partage(id, nom, code)")
            .eq("receveur_id", receveur_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (resolution code actif eleve {receveur_id}) : {e}")
        return None
    lignes = res.data or []
    codes = [ligne["codes_partage"] for ligne in lignes if ligne.get("codes_partage")]
    if not codes:
        return None
    if len(codes) == 1:
        return codes[0]
    return codes


def consulter_progres_notion_pour_eleve(receveur_id: str, nom_notion: str, rattachement_id: str | None = None):
    """Point d'entree cote eleve pour l'outil MCP consulter_avancement_notion,
    garde comme option secondaire (09/09/2026, demande Bourama) en plus
    de l'injection automatique de notions_pertinentes_pour_eleve. Resout
    le code actif de l'eleve (`rattachement_id`, mode actif de la
    conversation si fourni, sinon repli sur l'ancienne resolution, voir
    resoudre_code_actif_eleve), cherche la notion par RECHERCHE
    SEMANTIQUE (09/09/2026, remplace le matching texte strict, voir
    rechercher_notions_semantique) plutot que par egalite stricte sur le
    nom devine par le LLM, renvoie (statut, regle_effective) ou un
    marqueur d'erreur explicite pour que l'outil MCP puisse repondre
    clairement plutot que de planter silencieusement. Chaque appel,
    succes ou echec, est loggue (voir rechercher_notions_semantique et
    resoudre_code_actif_eleve), contrairement a l'ancien systeme."""
    code = resoudre_code_actif_eleve(receveur_id, rattachement_id)
    if code is None:
        logging.info(f"Consultation notion ignoree : eleve {receveur_id} sans code rattache.")
        return {"erreur": "aucun_code"}
    if isinstance(code, list):
        logging.warning(f"Consultation notion ambigue : eleve {receveur_id} rattache a plusieurs codes sans mode actif choisi.")
        return {"erreur": "code_ambigu", "codes": code}
    candidats = rechercher_notions_semantique(code["id"], nom_notion, match_count=1)
    if not candidats:
        return {"erreur": "notion_introuvable"}
    toutes = toutes_notions_code(code["id"])
    par_id = {n["id"]: n for n in toutes}
    notion = par_id.get(candidats[0]["id"]) or candidats[0]
    return {
        "statut": notion["statut"],
        "regle": regle_effective_pour_notion(notion, toutes),
        "consigne": consigne_effective_pour_notion(notion, toutes),
        "code_nom": code.get("nom") or code.get("code"),
        "nom_trouve": notion["nom"],
        "similarite": candidats[0]["similarite"],
    }


def notions_pertinentes_pour_eleve(receveur_id: str, message: str, rattachement_id: str | None = None) -> list[dict]:
    """Point d'entree pour l'injection AUTOMATIQUE et OBLIGATOIRE dans le
    prompt systeme (09/09/2026, demande Bourama, corrige le bug 7 du
    08/09/2026 : "la consultation du programme n'est jamais obligatoire,
    c'est une consigne de comportement que le LLM peut simplement ne pas
    suivre"). Calculee de facon DETERMINISTE a chaque message d'un
    eleve (voir core/main.py), independamment de tout choix du LLM,
    contrairement a l'outil MCP consulter_avancement_notion qui reste
    une option secondaire (voir consulter_progres_notion_pour_eleve).

    Renvoie jusqu'a MATCH_COUNT_NOTIONS candidats (nom, statut, regle
    effective, consigne effective, similarite), tries par pertinence
    decroissante, pour que le grand modele choisisse lui-meme laquelle
    s'applique reellement a la question plutot que de deviner un nom
    unique en amont (demande Bourama, 09/09/2026). Liste vide si l'eleve
    n'a aucun code rattache, si son mode actif est ambigu, ou si aucune
    notion vectorisee ne depasse le seuil de similarite, jamais
    d'exception remontee (voir rechercher_notions_semantique)."""
    code = resoudre_code_actif_eleve(receveur_id, rattachement_id)
    if code is None:
        return []
    if isinstance(code, list):
        logging.info(f"Notions pertinentes ignorees : eleve {receveur_id} rattache a plusieurs codes sans mode actif choisi.")
        return []
    candidats = rechercher_notions_semantique(code["id"], message)
    if not candidats:
        return []
    toutes = toutes_notions_code(code["id"])
    par_id = {n["id"]: n for n in toutes}
    resultat = []
    for c in candidats:
        notion = par_id.get(c["id"]) or c
        resultat.append({
            "id": notion["id"],
            "nom": notion["nom"],
            "statut": notion["statut"],
            "regle": regle_effective_pour_notion(notion, toutes),
            "consigne": consigne_effective_pour_notion(notion, toutes),
            "similarite": c["similarite"],
        })
    return resultat
