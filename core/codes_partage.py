"""
Codes de partage (14/08/2026, demande Bourama) : remplace le système
"un code = une matière" (core/contenu_dynamique_matiere.py, jamais
branché sur Clovis -- voir historique) par un système plus riche et
générique. Un utilisateur peut créer PLUSIEURS codes (pour ne pas
mélanger "à qui j'envoie quoi"), chacun pouvant porter, tous optionnels
et combinables librement :
- un comportement (même format que core/comportements_etudiants.py --
  description courte générée automatiquement, texte long lu à la
  demande via l'outil consulter_comportement)
- un programme (référence vers un programme DÉJÀ créé par le
  propriétaire dans "Programme" -- pas une copie, la structure reste
  gérée à un seul endroit)
- un partage de bibliothèque (voir propager_ajout_bibliotheque : chaque
  nouveau document/lien/note ajouté à la bibliothèque perso du
  propriétaire est automatiquement copié dans celle de chaque receveur)
- un texte libre (annonce simple, affichée dans une sous-section dédiée
  "Reçu de ..." côté receveur)

Toute personne qui a le code reçoit TOUT ce que porte ce code -- pas de
sélection destinataire par destinataire (c'est le sens même du code).
Modifiable après coup (Bourama, 14/08) : comportement/texte_libre sont
lus en direct depuis la ligne codes_partage à chaque fois, le programme
est une référence (donc toujours à jour), la bibliothèque se met à jour
au fil des ajouts -- rien n'est jamais figé en copie au moment de
l'entrée du code, SAUF les fichiers de bibliothèque eux-mêmes (voir
propager_ajout_bibliotheque : ceux-ci sont bien copiés physiquement
chez chaque receveur, comme demandé par Bourama).

Voir l'injection dans core/main.py::chat() (comportements/programmes
reçus fusionnés avec les siens propres) et les endpoints dans
api/codes_partage.py.
"""

import logging
import os
import secrets
import string
import sys
import tempfile

from supabase import create_client

sys.path.append(os.path.join(os.path.dirname(__file__)))
from comportements_etudiants import _generer_skill  # noqa: E402
from bibliotheque_fichiers import enregistrer_fichier, enregistrer_lien  # noqa: E402
from bibliotheque_rag import indexer_pdf_bibliotheque, indexer_texte_bibliotheque  # noqa: E402


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

# Même alphabet/longueur que l'ancien système matière (voir
# api/contenu_dynamique_matiere.py::_generer_code_unique) -- pas de
# raison de changer une convention qui marche déjà bien à l'usage.
_ALPHABET_CODE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LONGUEUR_CODE = 6
_TENTATIVES_MAX_CODE = 10


def _generer_code_unique() -> str:
    for _ in range(_TENTATIVES_MAX_CODE):
        code = "".join(secrets.choice(_ALPHABET_CODE) for _ in range(_LONGUEUR_CODE))
        try:
            existe = supabase.table("codes_partage").select("id").eq("code", code).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification unicité code partage) : {e}")
            continue
        if not existe or not existe.data:
            return code
    raise RuntimeError("Impossible de générer un code unique après plusieurs tentatives")


_COLONNES_CODE = "id, code, nom, comportement_texte, comportement_description, comportement_skill_md, programme_id, partage_bibliotheque, texte_libre, actif, created_at, updated_at"


def lister_mes_codes(proprietaire_id: str) -> list[dict]:
    try:
        res = (
            supabase.table("codes_partage")
            .select(_COLONNES_CODE)
            .eq("proprietaire_id", proprietaire_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture codes de {proprietaire_id}) : {e}")
        return []
    return res.data or []


def creer_code(
    proprietaire_id: str,
    nom: str | None = None,
    comportement_texte: str | None = None,
    programme_id: str | None = None,
    partage_bibliotheque: bool = False,
    texte_libre: str | None = None,
) -> dict:
    comportement_texte = (comportement_texte or "").strip() or None
    skill = _generer_skill(comportement_texte) if comportement_texte else None

    ligne = {
        "proprietaire_id": proprietaire_id,
        "code": _generer_code_unique(),
        "nom": (nom or "").strip() or None,
        "comportement_texte": comportement_texte,
        "comportement_description": skill["description"] if skill else None,
        "comportement_skill_md": skill["skill_md"] if skill else None,
        "programme_id": programme_id or None,
        "partage_bibliotheque": bool(partage_bibliotheque),
        "texte_libre": (texte_libre or "").strip() or None,
    }
    res = supabase.table("codes_partage").insert(ligne).execute()
    return res.data[0]


def modifier_code(
    code_id: str,
    proprietaire_id: str,
    nom: str | None = None,
    comportement_texte: str | None = None,
    programme_id: str | None = None,
    partage_bibliotheque: bool | None = None,
    texte_libre: str | None = None,
) -> dict | None:
    """Modification partielle : seuls les champs explicitement fournis
    (non None) sont mis à jour -- permet à un appelant de ne changer que
    le comportement sans toucher au reste, par exemple. Pour vider un
    champ texte, l'appelant doit passer une chaîne vide, pas None."""
    patch: dict = {}
    if nom is not None:
        patch["nom"] = nom.strip() or None
    if comportement_texte is not None:
        comportement_texte = comportement_texte.strip() or None
        patch["comportement_texte"] = comportement_texte
        skill = _generer_skill(comportement_texte) if comportement_texte else None
        patch["comportement_description"] = skill["description"] if skill else None
        patch["comportement_skill_md"] = skill["skill_md"] if skill else None
    if programme_id is not None:
        patch["programme_id"] = programme_id or None
    if partage_bibliotheque is not None:
        patch["partage_bibliotheque"] = bool(partage_bibliotheque)
    if texte_libre is not None:
        patch["texte_libre"] = texte_libre.strip() or None

    if not patch:
        res = (
            supabase.table("codes_partage").select(_COLONNES_CODE)
            .eq("id", code_id).eq("proprietaire_id", proprietaire_id).maybe_single().execute()
        )
        return res.data if res else None

    res = (
        supabase.table("codes_partage")
        .update(patch)
        .eq("id", code_id)
        .eq("proprietaire_id", proprietaire_id)
        .execute()
    )
    return res.data[0] if res.data else None


def activer_desactiver_code(code_id: str, proprietaire_id: str, actif: bool) -> dict | None:
    res = (
        supabase.table("codes_partage")
        .update({"actif": bool(actif)})
        .eq("id", code_id)
        .eq("proprietaire_id", proprietaire_id)
        .execute()
    )
    return res.data[0] if res.data else None


def supprimer_code(code_id: str, proprietaire_id: str) -> bool:
    res = (
        supabase.table("codes_partage")
        .delete()
        .eq("id", code_id)
        .eq("proprietaire_id", proprietaire_id)
        .execute()
    )
    return bool(res.data)


def entrer_code(code: str, receveur_id: str) -> dict | None:
    """Rattache receveur_id au code donné. None si le code n'existe pas
    ou n'est plus actif. Idempotent (unique(code_id, receveur_id)) :
    entrer deux fois le même code ne casse rien, renvoie le rattachement
    existant."""
    try:
        ligne_code = (
            supabase.table("codes_partage")
            .select("id, proprietaire_id, actif")
            .eq("code", code.strip().upper())
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche code partage {code}) : {e}")
        return None
    if not ligne_code or not ligne_code.data or not ligne_code.data.get("actif"):
        return None
    code_id = ligne_code.data["id"]
    if ligne_code.data["proprietaire_id"] == receveur_id:
        return None  # on ne s'auto-rattache pas à son propre code

    try:
        res = (
            supabase.table("rattachements_codes")
            .upsert({"code_id": code_id, "receveur_id": receveur_id}, on_conflict="code_id,receveur_id")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (rattachement code {code_id} / {receveur_id}) : {e}")
        return None
    return res.data[0] if res.data else None


def lister_mes_rattachements(receveur_id: str) -> list[dict]:
    """Codes que CE receveur a entrés, avec le nom du propriétaire pour
    affichage ("Reçu de X"). Ne renvoie que les codes encore actifs --
    si un propriétaire désactive son code, ses receveurs cessent de voir
    son contenu (mais le rattachement lui-même reste en base, pas
    supprimé, au cas où il le réactive)."""
    try:
        res = (
            supabase.table("rattachements_codes")
            .select("id, created_at, codes_partage!inner(id, code, nom, comportement_texte, comportement_description, programme_id, partage_bibliotheque, texte_libre, actif, proprietaire_id)")
            .eq("receveur_id", receveur_id)
            .eq("codes_partage.actif", True)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture rattachements de {receveur_id}) : {e}")
        return []

    lignes = res.data or []
    proprietaires_ids = list({l["codes_partage"]["proprietaire_id"] for l in lignes if l.get("codes_partage")})
    noms_proprietaires: dict[str, str] = {}
    if proprietaires_ids:
        try:
            profils = (
                supabase.table("profiles")
                .select("user_id, nom_affiche")
                .in_("user_id", proprietaires_ids)
                .execute()
            )
            for p in (profils.data or []):
                if p.get("nom_affiche"):
                    noms_proprietaires[p["user_id"]] = p["nom_affiche"]
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture noms propriétaires codes) : {e}")

    programmes_ids = list({l["codes_partage"]["programme_id"] for l in lignes if l.get("codes_partage") and l["codes_partage"].get("programme_id")})
    noms_programmes: dict[str, str] = {}
    if programmes_ids:
        try:
            progs = supabase.table("programmes").select("id, niveau, nom").in_("id", programmes_ids).execute()
            for p in (progs.data or []):
                noms_programmes[p["id"]] = p.get("nom") or p.get("niveau") or "programme"
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture noms programmes reçus) : {e}")

    resultat = []
    for l in lignes:
        cp = l.get("codes_partage")
        if not cp:
            continue
        resultat.append({
            "rattachement_id": l["id"],
            "code_id": cp["id"],
            "code": cp["code"],
            "nom_code": cp.get("nom"),
            "proprietaire_id": cp["proprietaire_id"],
            "proprietaire_nom": noms_proprietaires.get(cp["proprietaire_id"], "un autre utilisateur"),
            "a_comportement": bool(cp.get("comportement_texte")),
            "comportement_texte": cp.get("comportement_texte"),
            "a_programme": bool(cp.get("programme_id")),
            "programme_id": cp.get("programme_id"),
            "programme_nom": noms_programmes.get(cp.get("programme_id")),
            "partage_bibliotheque": bool(cp.get("partage_bibliotheque")),
            "texte_libre": cp.get("texte_libre"),
        })
    return resultat


def retirer_rattachement(rattachement_id: str, receveur_id: str) -> bool:
    res = (
        supabase.table("rattachements_codes")
        .delete()
        .eq("id", rattachement_id)
        .eq("receveur_id", receveur_id)
        .execute()
    )
    return bool(res.data)


# --- Injection côté chat (comportements/programmes reçus) -----------------

def lister_comportements_recus(receveur_id: str) -> list[dict]:
    """Comportements reçus via un code actif, forme {id, description}
    compatible avec core/comportements_etudiants.py::lister_comportements
    (même clés utilisées par choisir_comportements_pertinents et par le
    rendu du prompt -- jamais de 'texte' ici, exprès : le texte complet
    d'un comportement reçu se lit à la demande via consulter_comportement,
    jamais injecté d'office, comme pour les comportements propres).
    id = 'recu:<code_id>' (jamais de collision possible avec un id de
    comportements_etudiants, qui est un uuid nu) -- voir
    obtenir_comportement_skill_recu pour la résolution inverse."""
    rattachements = lister_mes_rattachements(receveur_id)
    code_ids = [r["code_id"] for r in rattachements if r["a_comportement"]]
    if not code_ids:
        return []
    try:
        lignes = (
            supabase.table("codes_partage")
            .select("id, comportement_description")
            .in_("id", code_ids)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (descriptions comportements reçus {code_ids}) : {e}")
        return []
    descriptions = {l["id"]: l.get("comportement_description") for l in (lignes.data or [])}
    noms = {r["code_id"]: r["proprietaire_nom"] for r in rattachements}
    return [
        {"id": f"recu:{code_id}", "description": f"(reçu de {noms.get(code_id, 'un autre utilisateur')}) {descriptions.get(code_id) or ''}".strip()}
        for code_id in code_ids
    ]


def obtenir_comportement_skill_recu(receveur_id: str, id_recu: str) -> str | None:
    """Skill complet (frontmatter + corps markdown) d'un comportement
    REÇU, à partir de l'id préfixé 'recu:<code_id>' -- utilisé par
    l'outil consulter_comportement quand il reçoit un id de cette forme
    plutôt qu'un id de comportements_etudiants classique. Vérifie que le
    rattachement existe bien et que le code est toujours actif (jamais
    de fuite vers un code qu'on n'a pas/plus)."""
    if not id_recu.startswith("recu:"):
        return None
    code_id = id_recu[len("recu:"):]
    try:
        rattachement = (
            supabase.table("rattachements_codes")
            .select("id")
            .eq("code_id", code_id)
            .eq("receveur_id", receveur_id)
            .maybe_single()
            .execute()
        )
        if not rattachement or not rattachement.data:
            return None
        ligne = (
            supabase.table("codes_partage")
            .select("comportement_skill_md")
            .eq("id", code_id)
            .eq("actif", True)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture skill comportement reçu {code_id}) : {e}")
        return None
    if not ligne or not ligne.data:
        return None
    return ligne.data.get("comportement_skill_md")


def lister_programmes_recus_legers(receveur_id: str) -> list[dict]:
    """Même forme que core/programme_llm.py::lister_mes_programmes_legers
    ({id, niveau, nom}) + proprietaire_nom, pour les programmes reçus via
    un code actif -- id ici est directement l'id du VRAI programme
    (table programmes), pas besoin de préfixe : consulter_programme
    accepte déjà n'importe quel programme_id, obtenir_structure_programme
    est simplement élargi ci-dessous pour aussi vérifier l'accès par
    code reçu, en plus de la propriété directe."""
    rattachements = lister_mes_rattachements(receveur_id)
    programmes_ids = [r["programme_id"] for r in rattachements if r["a_programme"]]
    if not programmes_ids:
        return []
    try:
        res = (
            supabase.table("programmes")
            .select("id, niveau, nom, proprietaire_id")
            .in_("id", programmes_ids)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programmes reçus {programmes_ids}) : {e}")
        return []
    noms_par_proprio = {r["proprietaire_id"]: r["proprietaire_nom"] for r in rattachements if r["a_programme"]}
    return [
        {
            "id": ligne["id"],
            "niveau": ligne["niveau"],
            "nom": f"(reçu de {noms_par_proprio.get(ligne.get('proprietaire_id'), 'un autre utilisateur')}) {ligne.get('nom') or ''}".strip(),
        }
        for ligne in (res.data or [])
    ]


def peut_acceder_programme_recu(receveur_id: str, programme_id: str) -> bool:
    """True si receveur_id a un rattachement actif donnant accès à ce
    programme_id précis -- utilisé par obtenir_structure_programme
    (core/programme_llm.py) en repli quand programme_id n'appartient pas
    directement à l'utilisateur."""
    rattachements = lister_mes_rattachements(receveur_id)
    return any(r["a_programme"] and r["programme_id"] == programme_id for r in rattachements)


# --- Propagation bibliothèque (copie à chaque ajout) -----------------------

def _receveurs_bibliotheque_de(proprietaire_id: str) -> list[str]:
    """Receveurs de TOUS les codes actifs du propriétaire ayant
    partage_bibliotheque=true (dédupliqués -- si un receveur a entré
    deux codes du même propriétaire avec le partage activé, il ne reçoit
    le fichier qu'une fois)."""
    try:
        codes = (
            supabase.table("codes_partage")
            .select("id")
            .eq("proprietaire_id", proprietaire_id)
            .eq("actif", True)
            .eq("partage_bibliotheque", True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture codes bibliothèque de {proprietaire_id}) : {e}")
        return []
    codes_ids = [c["id"] for c in (codes.data or [])]
    if not codes_ids:
        return []
    try:
        rattachements = (
            supabase.table("rattachements_codes")
            .select("receveur_id")
            .in_("code_id", codes_ids)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture receveurs bibliothèque de {proprietaire_id}) : {e}")
        return []
    return list({r["receveur_id"] for r in (rattachements.data or [])})


def propager_fichier_bibliotheque(proprietaire_id: str, contenu: bytes, nom_fichier: str, type_mime: str, description: str | None) -> None:
    """Copie ce fichier (déjà ajouté à la bibliothèque perso de
    proprietaire_id) dans la bibliothèque perso de chaque receveur d'un
    code actif à bibliothèque partagée. Non bloquant : une erreur ici ne
    doit jamais faire échouer l'ajout original (déjà réussi), juste être
    loguée -- même philosophie que la vectorisation PDF ailleurs dans le
    code."""
    receveurs = _receveurs_bibliotheque_de(proprietaire_id)
    for receveur_id in receveurs:
        try:
            ligne = enregistrer_fichier(
                contenu=contenu, nom_fichier=nom_fichier, type_mime=type_mime,
                niveau="utilisateur", uploade_par=proprietaire_id, user_id=receveur_id,
                description=description,
            )
        except Exception as e:
            logging.error(f"ERREUR propagation fichier bibliothèque ({proprietaire_id} -> {receveur_id}) : {e}")
            continue
        if type_mime == "application/pdf":
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(contenu)
                chemin_temp = tmp.name
            try:
                indexer_pdf_bibliotheque(chemin_temp, fichier_id=ligne["id"], user_id=receveur_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation PDF propagation ({proprietaire_id} -> {receveur_id}) : {e}")
            finally:
                try:
                    os.remove(chemin_temp)
                except OSError:
                    pass
        elif type_mime == "text/plain":
            try:
                indexer_texte_bibliotheque(contenu.decode("utf-8"), fichier_id=ligne["id"], user_id=receveur_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation texte propagation ({proprietaire_id} -> {receveur_id}) : {e}")


def propager_lien_bibliotheque(proprietaire_id: str, url: str, nom_fichier: str, description: str | None) -> None:
    receveurs = _receveurs_bibliotheque_de(proprietaire_id)
    for receveur_id in receveurs:
        try:
            enregistrer_lien(
                url=url, nom_fichier=nom_fichier, niveau="utilisateur",
                uploade_par=proprietaire_id, user_id=receveur_id, description=description,
            )
        except Exception as e:
            logging.error(f"ERREUR propagation lien bibliothèque ({proprietaire_id} -> {receveur_id}) : {e}")
