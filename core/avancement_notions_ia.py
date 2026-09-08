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

from supabase import create_client

from core.programme_notions import (
    STATUTS_VALIDES,
    code_appartient_a,
    lister_notions,
    creer_notion,
    changer_statut_notion,
)

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

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
    """Point d'entree cote eleve (voir Point 1, brique B/C du document de
    vision) : resout le code actif de l'eleve (`rattachement_id`, mode
    actif de la conversation si fourni -- 08/09/2026, sinon repli sur
    l'ancienne resolution, voir resoudre_code_actif_eleve), cherche la
    notion par nom dans ce code, renvoie (statut, regle_effective) ou un
    marqueur d'erreur explicite pour que l'outil MCP puisse repondre
    clairement plutot que de planter silencieusement."""
    code = resoudre_code_actif_eleve(receveur_id, rattachement_id)
    if code is None:
        return {"erreur": "aucun_code"}
    if isinstance(code, list):
        return {"erreur": "code_ambigu", "codes": code}
    toutes = toutes_notions_code(code["id"])
    cible = _normaliser(nom_notion)
    correspondances = [n for n in toutes if _normaliser(n.get("nom")) == cible]
    if len(correspondances) != 1:
        return {"erreur": "notion_introuvable"}
    notion = correspondances[0]
    return {
        "statut": notion["statut"],
        "regle": regle_effective_pour_notion(notion, toutes),
        "consigne": consigne_effective_pour_notion(notion, toutes),
        "code_nom": code.get("nom") or code.get("code"),
    }
