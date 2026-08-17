"""
Système de "plugins" pour le programme étudiant (lot 3/5, 2026-08-12).
Voir chantier-programme-etudiant.md, partie 1, section "Système de
plugins" : un plugin = l'export en bloc d'un programme complet (matières,
chapitres, documents, exercices), recherchable par niveau ou par nom de
créateur, téléchargeable par un autre utilisateur qui obtient sa propre
copie modifiable -- l'original partagé n'est jamais touché.

Dépend des tables `programmes`/`matieres`/`chapitres` (lot 1) et
`documents_programme`/`exercices_programme` (lot 2), utilisées ici
uniquement en lecture pour la publication/le clone -- jamais créées ni
modifiées par ce module.

Récompense "1 an de gratuité" au plugin le plus téléchargé (voir doc
source, "Modèle économique des plugins") : ce module fournit uniquement
le compteur et le classement (GET /api/plugins/classement).
L'attribution effective de la récompense n'est PAS automatisée --
vérifié le 12/08 (grep sur abonnement/premium/facturation dans ce
dépôt) : il n'existe aucun système de facturation/abonnement réel,
seulement un déblocage premium rempli à la main en base par Bourama.
Reste donc une action manuelle de sa part à la fin de la période de
lancement, à faire via le classement retourné par cet endpoint.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
router_programmes = APIRouter(prefix="/api/programmes", tags=["plugins"])


# ---------------------------------------------------------------------------
# Schémas
# ---------------------------------------------------------------------------

class PublierPluginPayload(BaseModel):
    nom: str
    examens_transverses_inclus: List[str] = []


class ExamenTransverseReponse(BaseModel):
    id: str
    titre: str
    type: str


class PluginReponse(BaseModel):
    id: str
    programme_source_id: str
    auteur_id: str
    auteur_nom: Optional[str] = None
    niveau: str
    nom: str
    gratuit: bool
    telechargements_count: int
    created_at: str


class TelechargerReponse(BaseModel):
    programme_id: str


# ---------------------------------------------------------------------------
# Aide interne
# ---------------------------------------------------------------------------

def _nom_affiche_ou_repli(user_id: str) -> str:
    try:
        res = (
            supabase.table("profiles")
            .select("nom_affiche")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (nom_affiche {user_id}) : {e}")
        res = None
    return ((res.data or {}).get("nom_affiche") if res else None) or "Sans nom"


def _plugin_vers_reponse(ligne: dict, noms_par_auteur: dict) -> PluginReponse:
    return PluginReponse(
        id=ligne["id"],
        programme_source_id=ligne["programme_source_id"],
        auteur_id=ligne["auteur_id"],
        auteur_nom=noms_par_auteur.get(ligne["auteur_id"]),
        niveau=ligne["niveau"],
        nom=ligne["nom"],
        gratuit=ligne["gratuit"],
        telechargements_count=ligne["telechargements_count"],
        created_at=ligne["created_at"],
    )


def _chapitre_ids_du_programme(programme_id: str) -> List[str]:
    try:
        matieres = supabase.table("matieres").select("id").eq("programme_id", programme_id).execute()
        matiere_ids = [m["id"] for m in (matieres.data or [])]
        if not matiere_ids:
            return []
        chapitres = supabase.table("chapitres").select("id").in_("matiere_id", matiere_ids).execute()
        return [c["id"] for c in (chapitres.data or [])]
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (chapitres du programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")


def _examens_transverses_du_programme(programme_id: str, utilisateur_id: str) -> list[dict]:
    """
    Examens qui touchent CE programme mais aussi au moins un chapitre d'un
    AUTRE programme -- un examen n'est pas contraint à un seul programme
    (voir api/contenu_programme.py::_verifier_chapitres_pour_examen).
    Utilisée à la publication d'un plugin (endpoint GET .../examens-
    transverses) pour proposer à l'auteur d'inclure ou non chacun de ces
    examens dans la copie -- voir migrations/2026_08_14_plugin_examens_
    transverses.sql et PublierPluginPayload.examens_transverses_inclus.
    """
    chapitre_ids_programme = set(_chapitre_ids_du_programme(programme_id))
    if not chapitre_ids_programme:
        return []
    try:
        liens = (
            supabase.table("examen_chapitres")
            .select("examen_id")
            .in_("chapitre_id", list(chapitre_ids_programme))
            .execute()
        )
        examen_ids = sorted({l["examen_id"] for l in (liens.data or [])})
        if not examen_ids:
            return []

        tous_liens = (
            supabase.table("examen_chapitres")
            .select("examen_id, chapitre_id")
            .in_("examen_id", examen_ids)
            .execute()
        )
        chapitres_par_examen: dict[str, set] = {}
        for l in (tous_liens.data or []):
            chapitres_par_examen.setdefault(l["examen_id"], set()).add(l["chapitre_id"])

        ids_transverses = [
            eid for eid, chs in chapitres_par_examen.items() if not chs.issubset(chapitre_ids_programme)
        ]
        if not ids_transverses:
            return []

        examens = (
            supabase.table("examens_programme")
            .select("id, titre, type")
            .in_("id", ids_transverses)
            .eq("proprietaire_id", utilisateur_id)
            .execute()
        )
        return examens.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (examens transverses du programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")


def _cloner_programme(
    programme_source_id: str,
    nouveau_proprietaire_id: str,
    nom_copie: str,
    examens_transverses_inclus: Optional[List[str]] = None,
) -> str:
    """
    Clone un programme complet (matières, chapitres, documents, exercices,
    examens, classements transversaux) en une copie indépendante
    appartenant à `nouveau_proprietaire_id`. Ne touche jamais au programme
    source. Retourne l'id du nouveau programme.

    Examens (14/08/2026, décision Bourama) : un examen peut couvrir des
    chapitres de plusieurs programmes différents. Les examens dont TOUS
    les chapitres appartiennent à ce programme sont toujours clonés. Les
    examens "transverses" (qui touchent aussi un autre programme) ne sont
    clonés que si leur id figure dans `examens_transverses_inclus` (choix
    fait par l'auteur à la publication du plugin, voir
    _examens_transverses_du_programme) -- et dans ce cas, seuls les
    chapitres appartenant à CE programme sont repris dans la copie, les
    liens vers l'autre programme sont perdus.

    Classements transversaux (14/08/2026, décision Bourama) : un
    classement (ex. "Semestre 1") peut lui aussi contenir des éléments de
    plusieurs programmes. Même principe : le classement est cloné mais ne
    garde que les éléments (matière/chapitre/document/exercice/examen)
    qui appartiennent à CE programme -- les éléments d'autres programmes
    sont perdus dans la copie. Un classement sans aucun élément clonable
    n'est pas recréé.

    Les tables `documents_programme`/`exercices_programme` sont du lot 2 :
    si elles n'existent pas encore côté base au moment où cette fonction
    tourne, leur lecture échoue proprement (liste vide, jamais une
    exception qui casse le clone du programme/matières/chapitres).
    """
    examens_transverses_inclus = set(examens_transverses_inclus or [])

    try:
        programme_source = (
            supabase.table("programmes")
            .select("id, niveau, nom, proprietaire_id")
            .eq("id", programme_source_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme source {programme_source_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    if not programme_source or not programme_source.data:
        raise erreur_api(404, "PROGRAMME_INTROUVABLE")

    niveau_source = programme_source.data["niveau"]
    auteur_source_id = programme_source.data["proprietaire_id"]

    try:
        nouveau_programme = (
            supabase.table("programmes")
            .insert({
                "proprietaire_id": nouveau_proprietaire_id,
                "niveau": niveau_source,
                "nom": nom_copie,
            })
            .execute()
        )
        nouveau_programme_id = nouveau_programme.data[0]["id"]

        matieres = (
            supabase.table("matieres")
            .select("id, nom, limites")
            .eq("programme_id", programme_source_id)
            .execute()
        )
        correspondance_matieres = {}
        for matiere in (matieres.data or []):
            nouvelle_matiere = (
                supabase.table("matieres")
                .insert({
                    "programme_id": nouveau_programme_id,
                    "nom": matiere["nom"],
                    "limites": matiere.get("limites"),
                })
                .execute()
            )
            correspondance_matieres[matiere["id"]] = nouvelle_matiere.data[0]["id"]

        if correspondance_matieres:
            chapitres = (
                supabase.table("chapitres")
                .select("id, matiere_id, nom, ordre, limites")
                .in_("matiere_id", list(correspondance_matieres.keys()))
                .execute()
            )
        else:
            chapitres = None

        correspondance_chapitres = {}
        for chapitre in ((chapitres.data if chapitres else None) or []):
            nouveau_chapitre = (
                supabase.table("chapitres")
                .insert({
                    "matiere_id": correspondance_matieres[chapitre["matiere_id"]],
                    "nom": chapitre["nom"],
                    "ordre": chapitre.get("ordre", 0),
                    "limites": chapitre.get("limites"),
                })
                .execute()
            )
            correspondance_chapitres[chapitre["id"]] = nouveau_chapitre.data[0]["id"]

        correspondance_documents: dict[str, str] = {}
        correspondance_exercices: dict[str, str] = {}

        if correspondance_chapitres:
            # Documents et exercices (lot 2) : lecture best-effort, une
            # table encore absente ne doit jamais faire échouer le clone
            # du squelette programme/matières/chapitres ci-dessus.
            try:
                documents = (
                    supabase.table("documents_programme")
                    .select("id, chapitre_id, titre, url_ou_contenu")
                    .in_("chapitre_id", list(correspondance_chapitres.keys()))
                    .execute()
                )
                for doc in (documents.data or []):
                    nouveau_doc = (
                        supabase.table("documents_programme")
                        .insert({
                            "chapitre_id": correspondance_chapitres[doc["chapitre_id"]],
                            "titre": doc["titre"],
                            "url_ou_contenu": doc["url_ou_contenu"],
                        })
                        .execute()
                    )
                    correspondance_documents[doc["id"]] = nouveau_doc.data[0]["id"]
            except Exception as e:
                logging.error(f"ERREUR clone documents_programme (source {programme_source_id}) : {e}")

            try:
                exercices = (
                    supabase.table("exercices_programme")
                    .select("id, chapitre_id, enonce")
                    .in_("chapitre_id", list(correspondance_chapitres.keys()))
                    .execute()
                )
                for ex in (exercices.data or []):
                    nouvel_exercice = (
                        supabase.table("exercices_programme")
                        .insert({
                            "chapitre_id": correspondance_chapitres[ex["chapitre_id"]],
                            "enonce": ex["enonce"],
                        })
                        .execute()
                    )
                    correspondance_exercices[ex["id"]] = nouvel_exercice.data[0]["id"]
            except Exception as e:
                logging.error(f"ERREUR clone exercices_programme (source {programme_source_id}) : {e}")

        # -------------------- Examens (14/08/2026) --------------------
        correspondance_examens: dict[str, str] = {}
        if correspondance_chapitres:
            try:
                liens = (
                    supabase.table("examen_chapitres")
                    .select("examen_id, chapitre_id")
                    .in_("chapitre_id", list(correspondance_chapitres.keys()))
                    .execute()
                )
                examen_ids_source = sorted({l["examen_id"] for l in (liens.data or [])})

                if examen_ids_source:
                    tous_liens = (
                        supabase.table("examen_chapitres")
                        .select("examen_id, chapitre_id")
                        .in_("examen_id", examen_ids_source)
                        .execute()
                    )
                    chapitres_par_examen: dict[str, set] = {}
                    for l in (tous_liens.data or []):
                        chapitres_par_examen.setdefault(l["examen_id"], set()).add(l["chapitre_id"])

                    chapitre_ids_programme = set(correspondance_chapitres.keys())
                    examens_source = (
                        supabase.table("examens_programme")
                        .select("id, titre, type")
                        .in_("id", examen_ids_source)
                        .execute()
                    )
                    for examen in (examens_source.data or []):
                        chs_examen = chapitres_par_examen.get(examen["id"], set())
                        est_transverse = not chs_examen.issubset(chapitre_ids_programme)
                        if est_transverse and examen["id"] not in examens_transverses_inclus:
                            continue

                        nouvel_examen = (
                            supabase.table("examens_programme")
                            .insert({
                                "proprietaire_id": nouveau_proprietaire_id,
                                "titre": examen["titre"],
                                "type": examen["type"],
                            })
                            .execute()
                        )
                        nouvel_examen_id = nouvel_examen.data[0]["id"]
                        correspondance_examens[examen["id"]] = nouvel_examen_id

                        chapitres_a_lier = [
                            correspondance_chapitres[cid] for cid in chs_examen if cid in correspondance_chapitres
                        ]
                        for nouveau_chapitre_id in chapitres_a_lier:
                            supabase.table("examen_chapitres").insert({
                                "examen_id": nouvel_examen_id,
                                "chapitre_id": nouveau_chapitre_id,
                            }).execute()
            except Exception as e:
                logging.error(f"ERREUR clone examens_programme (source {programme_source_id}) : {e}")

        # ------------- Classements transversaux (14/08/2026) -------------
        try:
            cibles_clonables: dict[str, dict[str, str]] = {
                "matiere": correspondance_matieres,
                "chapitre": correspondance_chapitres,
                "document": correspondance_documents,
                "exercice": correspondance_exercices,
                "examen": correspondance_examens,
            }
            tous_ids_clonables: List[str] = [
                cid for correspondance in cibles_clonables.values() for cid in correspondance.keys()
            ]
            if tous_ids_clonables:
                items = (
                    supabase.table("classement_transversal_items")
                    .select("classement_id, cible_type, cible_id")
                    .in_("cible_id", tous_ids_clonables)
                    .execute()
                )
                classements_ids_concernes = sorted({i["classement_id"] for i in (items.data or [])})
                if classements_ids_concernes:
                    classements_source = (
                        supabase.table("classements_transversaux")
                        .select("id, type, label")
                        .in_("id", classements_ids_concernes)
                        .eq("proprietaire_id", auteur_source_id)
                        .execute()
                    )
                    items_par_classement: dict[str, list] = {}
                    for i in (items.data or []):
                        items_par_classement.setdefault(i["classement_id"], []).append(i)

                    for classement in (classements_source.data or []):
                        items_a_cloner = [
                            i
                            for i in items_par_classement.get(classement["id"], [])
                            if i["cible_id"] in cibles_clonables.get(i["cible_type"], {})
                        ]
                        if not items_a_cloner:
                            continue

                        nouveau_classement = (
                            supabase.table("classements_transversaux")
                            .insert({
                                "proprietaire_id": nouveau_proprietaire_id,
                                "type": classement["type"],
                                "label": classement["label"],
                            })
                            .execute()
                        )
                        nouveau_classement_id = nouveau_classement.data[0]["id"]
                        for item in items_a_cloner:
                            nouvelle_cible_id = cibles_clonables[item["cible_type"]][item["cible_id"]]
                            supabase.table("classement_transversal_items").insert({
                                "classement_id": nouveau_classement_id,
                                "cible_type": item["cible_type"],
                                "cible_id": nouvelle_cible_id,
                            }).execute()
        except Exception as e:
            logging.error(f"ERREUR clone classements_transversaux (source {programme_source_id}) : {e}")

        # --------- Documents bibliothèque classés (17/08/2026) ---------
        # Jusqu'ici absents du clone : un programme publié comme plugin
        # perdait silencieusement tous les documents classés via la
        # bibliothèque (voir core/bibliotheque_programme.py, 16/08).
        # Best-effort comme le reste de cette fonction -- ne fait
        # jamais échouer le clone du squelette si ça rate. On ne
        # duplique PAS le fichier lui-même (fichiers_uploades reste la
        # propriété de l'auteur source) : seul le lien de classement
        # est recréé, comme pour classement_transversal_items ci-dessus.
        try:
            cibles_clonables["programme"] = {programme_source_id: nouveau_programme_id}
            tous_ids_emplacements = [
                cid for correspondance in cibles_clonables.values() for cid in correspondance.keys()
            ]
            if tous_ids_emplacements:
                emplacements = (
                    supabase.table("bibliotheque_emplacements_programme")
                    .select("fichier_id, type_cible, cible_id")
                    .in_("cible_id", tous_ids_emplacements)
                    .execute()
                )
                for emplacement in (emplacements.data or []):
                    correspondance = cibles_clonables.get(emplacement["type_cible"], {})
                    nouvelle_cible_id = correspondance.get(emplacement["cible_id"])
                    if nouvelle_cible_id is None:
                        continue
                    supabase.table("bibliotheque_emplacements_programme").insert({
                        "fichier_id": emplacement["fichier_id"],
                        "type_cible": emplacement["type_cible"],
                        "cible_id": nouvelle_cible_id,
                    }).execute()
        except Exception as e:
            logging.error(f"ERREUR clone bibliotheque_emplacements_programme (source {programme_source_id}) : {e}")

    except Exception as e:
        logging.error(f"ERREUR SUPABASE (clone programme {programme_source_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    return nouveau_programme_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router_programmes.get("/{programme_id}/examens-transverses", response_model=List[ExamenTransverseReponse])
def examens_transverses_du_programme(programme_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Examens de ce programme qui touchent AUSSI au moins un chapitre d'un
    autre programme -- appelé par le frontend avant de publier un plugin,
    pour proposer à l'auteur de les inclure ou non dans la copie (voir
    _examens_transverses_du_programme et PublierPluginPayload.
    examens_transverses_inclus). Liste vide si aucun cas de ce genre.
    """
    try:
        programme = (
            supabase.table("programmes")
            .select("id, proprietaire_id")
            .eq("id", programme_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if not programme or not programme.data:
        raise erreur_api(404, "PROGRAMME_INTROUVABLE")
    if programme.data["proprietaire_id"] != utilisateur.id:
        raise erreur_api(403, "PAS_LE_DROIT_SUR_CE_PROGRAMME")

    return _examens_transverses_du_programme(programme_id, utilisateur.id)


@router_programmes.post("/{programme_id}/publier-plugin", response_model=PluginReponse, status_code=201)
def publier_plugin(
    programme_id: str,
    payload: PublierPluginPayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    if not (payload.nom or "").strip():
        raise erreur_api(400, "LE_NOM_DE_L_AGENT_EST")

    try:
        programme = (
            supabase.table("programmes")
            .select("id, proprietaire_id, niveau")
            .eq("id", programme_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    if not programme or not programme.data:
        raise erreur_api(404, "PROGRAMME_INTROUVABLE")
    if programme.data["proprietaire_id"] != utilisateur.id:
        raise erreur_api(403, "PAS_LE_DROIT_SUR_CE_PROGRAMME")

    # Le choix de l'auteur (examensTransversesInclus) ne peut porter que sur
    # des examens réellement transverses de CE programme -- jamais un id
    # arbitraire fourni par le client (voir _examens_transverses_du_programme).
    examens_transverses_choisis = [eid.strip() for eid in payload.examens_transverses_inclus if eid.strip()]
    if examens_transverses_choisis:
        ids_eligibles = {e["id"] for e in _examens_transverses_du_programme(programme_id, utilisateur.id)}
        ids_invalides = set(examens_transverses_choisis) - ids_eligibles
        if ids_invalides:
            raise erreur_api(400, "EXAMEN_TRANSVERSE_INVALIDE_POUR_CE")

    try:
        nouveau = (
            supabase.table("plugins_programme")
            .insert({
                "programme_source_id": programme_id,
                "auteur_id": utilisateur.id,
                "niveau": programme.data["niveau"],
                "nom": payload.nom.strip(),
                "examens_transverses_inclus": examens_transverses_choisis,
            })
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (publication plugin, programme {programme_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    ligne = nouveau.data[0]

    journaliser(
        action="plugin.publie",
        user_id=utilisateur.id,
        cible_type="plugin_programme",
        cible_id=ligne["id"],
        details={"programme_source_id": programme_id, "nom": payload.nom.strip()},
        request=request,
    )

    return _plugin_vers_reponse(ligne, {utilisateur.id: _nom_affiche_ou_repli(utilisateur.id)})


@router.get("", response_model=List[PluginReponse])
def rechercher_plugins(
    q: Optional[str] = Query(default=None),
):
    """
    Liste unique de tous les plugins publiés (fusion de l'ancienne
    recherche niveau/auteur et de l'ancien classement -- décision du
    2026-08-14 : plus de section "recherche" séparée côté front, une
    seule liste avec un champ de recherche libre intégré).

    Toujours triée par nombre de téléchargements décroissant, avec ou
    sans recherche -- c'est ce même tri qui sert à repérer le plugin
    gagnant de la mécanique de lancement (voir docstring en tête de
    fichier et GET /api/plugins/classement, conservé pour compatibilité
    mais désormais redondant avec cet endpoint sans `q`).

    Si `q` est fourni, filtre en "OU" sur : le nom du plugin, le niveau,
    et le nom affiché de l'auteur (recherche approchante sur chacun).
    """
    requete = supabase.table("plugins_programme").select("*")

    mot_cle = (q or "").strip()
    if mot_cle:
        ids_auteurs: List[str] = []
        try:
            profils = (
                supabase.table("profiles")
                .select("user_id, nom_affiche")
                .ilike("nom_affiche", f"%{mot_cle}%")
                .execute()
            )
            ids_auteurs = [p["user_id"] for p in (profils.data or [])]
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (recherche auteurs '{mot_cle}') : {e}")
            raise erreur_api(500, "RECHERCHE_INDISPONIBLE")

        conditions = [f"nom.ilike.%{mot_cle}%", f"niveau.ilike.%{mot_cle}%"]
        if ids_auteurs:
            conditions.append(f"auteur_id.in.({','.join(ids_auteurs)})")
        requete = requete.or_(",".join(conditions))

    try:
        res = requete.order("telechargements_count", desc=True).limit(100).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche plugins q={mot_cle}) : {e}")
        raise erreur_api(500, "RECHERCHE_INDISPONIBLE")

    lignes = res.data or []
    noms_par_auteur = {uid: _nom_affiche_ou_repli(uid) for uid in {ligne["auteur_id"] for ligne in lignes}}
    return [_plugin_vers_reponse(ligne, noms_par_auteur) for ligne in lignes]


@router.get("/classement", response_model=List[PluginReponse])
def classement_plugins():
    """
    Classement par nombre de téléchargements décroissant -- pour
    identifier le plugin gagnant de la mécanique de lancement (voir doc
    source). L'attribution de la récompense reste manuelle, voir
    docstring en tête de fichier.
    """
    try:
        res = (
            supabase.table("plugins_programme")
            .select("*")
            .order("telechargements_count", desc=True)
            .limit(100)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (classement plugins) : {e}")
        raise erreur_api(500, "RECHERCHE_INDISPONIBLE")

    lignes = res.data or []
    noms_par_auteur = {uid: _nom_affiche_ou_repli(uid) for uid in {ligne["auteur_id"] for ligne in lignes}}
    return [_plugin_vers_reponse(ligne, noms_par_auteur) for ligne in lignes]


@router.post("/{plugin_id}/telecharger", response_model=TelechargerReponse, status_code=201)
def telecharger_plugin(plugin_id: str, request: Request, utilisateur=Depends(utilisateur_courant)):
    try:
        plugin = (
            supabase.table("plugins_programme")
            .select("id, programme_source_id, nom, examens_transverses_inclus")
            .eq("id", plugin_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture plugin {plugin_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    if not plugin or not plugin.data:
        raise erreur_api(404, "PLUGIN_INTROUVABLE")

    # Déjà téléchargé par cet utilisateur : ne recompte pas, mais ne
    # recrée pas non plus une deuxième copie -- renvoie la copie existante.
    try:
        deja = (
            supabase.table("plugin_telechargements")
            .select("programme_copie_id")
            .eq("plugin_id", plugin_id)
            .eq("telecharge_par", utilisateur.id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification téléchargement existant {plugin_id}) : {e}")
        deja = None

    if deja and deja.data and deja.data.get("programme_copie_id"):
        return TelechargerReponse(programme_id=deja.data["programme_copie_id"])

    nouveau_programme_id = _cloner_programme(
        programme_source_id=plugin.data["programme_source_id"],
        nouveau_proprietaire_id=utilisateur.id,
        nom_copie=plugin.data["nom"],
        examens_transverses_inclus=plugin.data.get("examens_transverses_inclus") or [],
    )

    try:
        supabase.table("plugin_telechargements").insert({
            "plugin_id": plugin_id,
            "telecharge_par": utilisateur.id,
            "programme_copie_id": nouveau_programme_id,
        }).execute()

        # Incrément atomique côté base impossible sans RPC dédiée -- lu
        # puis réécrit ici, cohérent avec le reste du dépôt (pas de RPC
        # d'incrément trouvée pour un cas équivalent). Fenêtre de course
        # improbable (deux téléchargements simultanés du même
        # utilisateur), sans conséquence grave si elle se produit
        # (compteur en retard d'une unité, jamais faux positif de
        # sécurité).
        plugin_actuel = (
            supabase.table("plugins_programme")
            .select("telechargements_count")
            .eq("id", plugin_id)
            .single()
            .execute()
        )
        supabase.table("plugins_programme").update({
            "telechargements_count": plugin_actuel.data["telechargements_count"] + 1
        }).eq("id", plugin_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement téléchargement plugin {plugin_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    journaliser(
        action="plugin.telecharge",
        user_id=utilisateur.id,
        cible_type="plugin_programme",
        cible_id=plugin_id,
        details={"programme_copie_id": nouveau_programme_id},
        request=request,
    )

    return TelechargerReponse(programme_id=nouveau_programme_id)
