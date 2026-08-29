"""
Codes de partage (14/08/2026, demande Bourama) : remplace le système
"un code = une matière" (core/contenu_dynamique_matiere.py, jamais
branché sur Clovis -- voir historique) par un système plus riche et
générique. Un utilisateur peut créer PLUSIEURS codes (pour ne pas
mélanger "à qui j'envoie quoi"), chacun pouvant porter, tous optionnels
et combinables librement :
- des comportements (18/08/2026, demande Bourama : plus un texte tapé
  directement dans le code, mais une SÉLECTION parmi les comportements
  déjà créés dans "Mes comportements" -- plusieurs possibles par code,
  RÉFÉRENCE VIVANTE via la table de liaison codes_partage_comportements,
  jamais une copie : si le propriétaire modifie un comportement après
  coup, tous les codes qui le référencent suivent automatiquement la
  version à jour)
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
Modifiable après coup (Bourama, 14/08) : comportements (référence vivante,
voir plus haut)/texte_libre sont lus en direct à chaque fois, le
programme est une référence (donc toujours à jour), la bibliothèque se
met à jour au fil des ajouts -- rien n'est jamais figé en copie au
moment de l'entrée du code, SAUF les fichiers de bibliothèque eux-mêmes
(voir propager_ajout_bibliotheque : ceux-ci sont bien copiés
physiquement chez chaque receveur, comme demandé par Bourama).

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


_COLONNES_CODE = "id, code, nom, programme_id, partage_bibliotheque, texte_libre, actif, created_at, updated_at"


def _comportements_par_code(code_ids: list[str]) -> dict[str, list[dict]]:
    """{code_id: [{id, nom}, ...]} pour l'ensemble des code_ids donnés --
    lecture EN DIRECT sur comportements_etudiants (référence vivante,
    jamais de copie), via la table de liaison codes_partage_comportements.
    Utilisé aussi bien pour l'affichage propriétaire ("Mes codes") que
    pour la résolution côté receveur plus bas."""
    if not code_ids:
        return {}
    try:
        liaisons = (
            supabase.table("codes_partage_comportements")
            .select("code_id, comportement_id, comportements_etudiants(id, nom)")
            .in_("code_id", code_ids)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportements liés aux codes {code_ids}) : {e}")
        return {}
    resultat: dict[str, list[dict]] = {}
    for l in (liaisons.data or []):
        comportement = l.get("comportements_etudiants")
        if not comportement:
            continue  # comportement supprimé entre-temps -- liaison orpheline ignorée à l'affichage
        resultat.setdefault(l["code_id"], []).append({"id": comportement["id"], "nom": comportement.get("nom") or ""})
    return resultat


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
    codes = res.data or []
    comportements_par_code = _comportements_par_code([c["id"] for c in codes])
    for c in codes:
        c["comportements"] = comportements_par_code.get(c["id"], [])
    return codes


def _remplacer_comportements_du_code(code_id: str, proprietaire_id: str, comportement_ids: list[str]) -> None:
    """Remplace entièrement l'ensemble des comportements attachés à ce
    code par comportement_ids (vide -> plus aucun). Vérifie que chaque id
    appartient bien au propriétaire du code AVANT liaison (jamais de
    fuite : impossible d'attacher le comportement de quelqu'un d'autre à
    son propre code)."""
    comportement_ids = list(dict.fromkeys(i for i in (comportement_ids or []) if i))  # dédupliqué, ordre gardé
    if comportement_ids:
        try:
            valides = (
                supabase.table("comportements_etudiants")
                .select("id")
                .in_("id", comportement_ids)
                .eq("etudiant_id", proprietaire_id)
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification propriété comportements {comportement_ids}) : {e}")
            valides = None
        ids_valides = {l["id"] for l in (valides.data or [])} if valides else set()
        comportement_ids = [i for i in comportement_ids if i in ids_valides]

    try:
        supabase.table("codes_partage_comportements").delete().eq("code_id", code_id).execute()
        if comportement_ids:
            supabase.table("codes_partage_comportements").insert(
                [{"code_id": code_id, "comportement_id": cid} for cid in comportement_ids]
            ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liaison comportements <-> code {code_id}) : {e}")


def creer_code(
    proprietaire_id: str,
    nom: str | None = None,
    comportement_ids: list[str] | None = None,
    programme_id: str | None = None,
    partage_bibliotheque: bool = False,
    texte_libre: str | None = None,
) -> dict:
    ligne = {
        "proprietaire_id": proprietaire_id,
        "code": _generer_code_unique(),
        "nom": (nom or "").strip() or None,
        "programme_id": programme_id or None,
        "partage_bibliotheque": bool(partage_bibliotheque),
        "texte_libre": (texte_libre or "").strip() or None,
    }
    res = supabase.table("codes_partage").insert(ligne).execute()
    code = res.data[0]
    if comportement_ids:
        _remplacer_comportements_du_code(code["id"], proprietaire_id, comportement_ids)
    code["comportements"] = _comportements_par_code([code["id"]]).get(code["id"], [])
    return code


def modifier_code(
    code_id: str,
    proprietaire_id: str,
    nom: str | None = None,
    comportement_ids: list[str] | None = None,
    programme_id: str | None = None,
    partage_bibliotheque: bool | None = None,
    texte_libre: str | None = None,
) -> dict | None:
    """Modification partielle : seuls les champs explicitement fournis
    (non None) sont mis à jour -- permet à un appelant de ne changer que
    le comportement sans toucher au reste, par exemple. Pour vider un
    champ texte, l'appelant doit passer une chaîne vide, pas None.

    comportement_ids (18/08/2026) : None -> pas touché ; liste (même
    vide) -> remplace ENTIÈREMENT l'ensemble des comportements attachés
    (liste vide = tout détacher)."""
    patch: dict = {}
    if nom is not None:
        patch["nom"] = nom.strip() or None
    if programme_id is not None:
        patch["programme_id"] = programme_id or None
    if partage_bibliotheque is not None:
        patch["partage_bibliotheque"] = bool(partage_bibliotheque)
    if texte_libre is not None:
        patch["texte_libre"] = texte_libre.strip() or None

    if patch:
        res = (
            supabase.table("codes_partage")
            .update(patch)
            .eq("id", code_id)
            .eq("proprietaire_id", proprietaire_id)
            .execute()
        )
        if not res.data:
            return None
        code = res.data[0]
    else:
        res = (
            supabase.table("codes_partage").select(_COLONNES_CODE)
            .eq("id", code_id).eq("proprietaire_id", proprietaire_id).maybe_single().execute()
        )
        if not res or not res.data:
            return None
        code = res.data

    if comportement_ids is not None:
        _remplacer_comportements_du_code(code_id, proprietaire_id, comportement_ids)
    code["comportements"] = _comportements_par_code([code_id]).get(code_id, [])
    return code


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
            .select("id, created_at, codes_partage!inner(id, code, nom, programme_id, partage_bibliotheque, texte_libre, actif, proprietaire_id)")
            .eq("receveur_id", receveur_id)
            .eq("codes_partage.actif", True)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture rattachements de {receveur_id}) : {e}")
        return []

    lignes = res.data or []
    code_ids = [l["codes_partage"]["id"] for l in lignes if l.get("codes_partage")]
    comportements_par_code = _comportements_par_code(code_ids)
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

    # Résolution du nom de programme désactivée le 29/08/2026 (demande
    # Bourama, fonctionnalité "Programme" isolée) -- voir
    # _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md. programme_id
    # reste affiché tel quel (référence brute), programme_nom n'est plus
    # résolu.
    noms_programmes: dict[str, str] = {}

    resultat = []
    for l in lignes:
        cp = l.get("codes_partage")
        if not cp:
            continue
        comportements = comportements_par_code.get(cp["id"], [])
        resultat.append({
            "rattachement_id": l["id"],
            "code_id": cp["id"],
            "code": cp["code"],
            "nom_code": cp.get("nom"),
            "proprietaire_id": cp["proprietaire_id"],
            "proprietaire_nom": noms_proprietaires.get(cp["proprietaire_id"], "un autre utilisateur"),
            "a_comportement": bool(comportements),
            "comportements": comportements,  # [{id, nom}, ...] -- référence vivante, jamais figée
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
    """Comportements reçus via un ou plusieurs codes actifs, forme
    {id, description} compatible avec
    core/comportements_etudiants.py::lister_comportements (même clés
    utilisées par choisir_comportements_pertinents et par le rendu du
    prompt -- jamais de 'texte' ici, exprès : le texte complet d'un
    comportement reçu se lit à la demande via consulter_comportement,
    jamais injecté d'office, comme pour les comportements propres).

    id = 'recu:<comportement_id>' (18/08/2026 -- avant : 'recu:<code_id>',
    changé car un code peut désormais porter plusieurs comportements ;
    comportement_id est l'uuid réel dans comportements_etudiants, jamais
    de collision possible avec le préfixe 'recu:'). Description lue EN
    DIRECT sur comportements_etudiants -- référence vivante, jamais figée
    au moment de l'entrée du code. Si le même comportement est reçu via
    plusieurs codes actifs à la fois, il n'apparaît qu'une fois."""
    rattachements = lister_mes_rattachements(receveur_id)
    par_comportement: dict[str, str] = {}  # comportement_id -> nom du propriétaire (premier trouvé)
    for r in rattachements:
        for c in r["comportements"]:
            par_comportement.setdefault(c["id"], r["proprietaire_nom"])
    if not par_comportement:
        return []
    try:
        lignes = (
            supabase.table("comportements_etudiants")
            .select("id, description")
            .in_("id", list(par_comportement.keys()))
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (descriptions comportements reçus {list(par_comportement.keys())}) : {e}")
        return []
    return [
        {
            "id": f"recu:{l['id']}",
            "description": f"(reçu de {par_comportement.get(l['id'], 'un autre utilisateur')}) {l.get('description') or ''}".strip(),
        }
        for l in (lignes.data or [])
    ]


def obtenir_comportement_skill_recu(receveur_id: str, id_recu: str) -> str | None:
    """Skill complet (frontmatter + corps markdown) d'un comportement
    REÇU, à partir de l'id préfixé 'recu:<comportement_id>' -- utilisé
    par l'outil consulter_comportement quand il reçoit un id de cette
    forme plutôt qu'un id de comportements_etudiants classique. Vérifie
    qu'un rattachement actif donne bien accès à CE comportement précis
    (via un code qui le référence) avant de le lire -- jamais de fuite
    vers un comportement qu'on n'a pas/plus. Lecture EN DIRECT sur
    comportements_etudiants (référence vivante)."""
    if not id_recu.startswith("recu:"):
        return None
    comportement_id = id_recu[len("recu:"):]
    rattachements = lister_mes_rattachements(receveur_id)
    accessible = any(c["id"] == comportement_id for r in rattachements for c in r["comportements"])
    if not accessible:
        return None
    try:
        ligne = (
            supabase.table("comportements_etudiants")
            .select("skill_md")
            .eq("id", comportement_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture skill comportement reçu {comportement_id}) : {e}")
        return None
    if not ligne or not ligne.data:
        return None
    return ligne.data.get("skill_md")


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
