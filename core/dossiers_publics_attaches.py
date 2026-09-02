"""
Attachement d'un dossier du catalogue public à la bibliothèque
personnelle (02/09/2026, demande Bourama : "ajouter un dossier public à
sa bibliothèque perso, peu importe la contribution, et pouvoir librement
le nourrir depuis sa bibliothèque personnelle" -- PUIS correction du même
jour après un premier essai en simple référence/liaison, jugé insuffisant :
"il doit y être comme si c'est toi qui l'a mis, juste une différenciation
visuelle" -- donc une VRAIE copie physique dans fichiers_uploades/
dossiers_bibliotheque, pas une vue en direct sur la table publique).

Repose sur EXACTEMENT le même mécanisme de "dossier miroir" que le
partage de dossiers privés entre utilisateurs (voir core/codes_partage.py :
_obtenir_ou_creer_miroir / _chemin_miroir / propager_*), adapté pour une
source publique (dossiers_catalogue_public / bibliotheque_publique) au
lieu d'une source privée (dossiers_bibliotheque / fichiers_uploades) --
d'où une table de miroirs séparée, miroirs_dossiers_publics (la
contrainte de clé étrangère de miroirs_dossiers_partages pointe
spécifiquement vers dossiers_bibliotheque comme source, incompatible ici).

Confirmé par Bourama (02/09) : la synchronisation reste ACTIVE après
l'attachement -- tout nouveau fichier rangé dans le dossier public
d'origine (par n'importe qui, selon les règles habituelles de
contribution_libre/privee, voir core/dossiers_catalogue_public.py)
apparaît automatiquement, en copie réelle, dans le dossier miroir de
CHAQUE utilisateur qui a attaché ce dossier -- voir
propager_fichier_public_range_dossier, appelée depuis les points d'ajout
d'un fichier au catalogue public (api/dossiers_catalogue_public.py::ranger
et api/bibliotheque_publique.py::_classer_si_autorise).

Détacher (detacher_dossier) arrête uniquement la synchronisation future
(suppression de la ligne dans dossiers_publics_attaches) -- la copie déjà
faite dans la bibliothèque perso n'est JAMAIS supprimée automatiquement,
cohérent avec le principe "comme si c'est toi qui l'a mis" : une fois
copié, le fichier est réellement à l'utilisateur, à lui de le supprimer
manuellement s'il le souhaite (même logique que la suppression d'un
dossier public qui ne supprime jamais ses documents, voir
core/dossiers_catalogue_public.py::supprimer_dossier).
"""

import logging
import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__)))
from bibliotheque_fichiers import enregistrer_fichier, enregistrer_lien, supabase, BUCKET  # noqa: E402
from dossiers_bibliotheque import creer_dossier, lister_dossiers, lister_fichiers_ids_dossier, ranger_fichier  # noqa: E402
from dossiers_catalogue_public import _dossier, lister_dossiers as lister_dossiers_publics, lister_fichiers_ids_dossier as lister_fichiers_ids_dossier_public  # noqa: E402
from file_attente_vectorisation import necessite_vectorisation_fichier_privee  # noqa: E402

logging.basicConfig(level=logging.INFO)


def _sous_arbre_dossiers_publics(dossier_racine_id: str, tous_dossiers: list[dict]) -> list[str]:
    """Même principe que codes_partage.py::_sous_arbre_dossiers, sur les dossiers du catalogue public."""
    ids = {dossier_racine_id}
    changement = True
    while changement:
        changement = False
        for d in tous_dossiers:
            if d.get("dossier_parent_id") in ids and d["id"] not in ids:
                ids.add(d["id"])
                changement = True
    return list(ids)


def _obtenir_ou_creer_miroir(dossier_source_id: str, receveur_id: str, nom: str, parent_miroir_id: str | None) -> str:
    try:
        existant = (
            supabase.table("miroirs_dossiers_publics")
            .select("dossier_miroir_id")
            .eq("dossier_source_id", dossier_source_id)
            .eq("receveur_id", receveur_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture miroir dossier public {dossier_source_id} / {receveur_id}) : {e}")
        existant = None
    if existant and existant.data:
        return existant.data["dossier_miroir_id"]

    nouveau = creer_dossier(receveur_id, nom, parent_miroir_id)
    try:
        supabase.table("miroirs_dossiers_publics").insert({
            "dossier_source_id": dossier_source_id,
            "receveur_id": receveur_id,
            "dossier_miroir_id": nouveau["id"],
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement miroir dossier public {dossier_source_id} / {receveur_id}) : {e}")
    return nouveau["id"]


def _chemin_miroir(dossier_id: str, dossier_racine_id: str, par_id: dict, receveur_id: str) -> str:
    """Reconstruit (ou réutilise) chez receveur_id toute la chaîne de
    dossiers miroirs depuis dossier_racine_id jusqu'à dossier_id inclus,
    renvoie l'id du dossier miroir final (celui où ranger le fichier)."""
    chaine = []
    courant = dossier_id
    while courant and courant != dossier_racine_id:
        chaine.append(par_id[courant])
        courant = par_id[courant].get("dossier_parent_id")
    chaine.append(par_id[dossier_racine_id])
    chaine.reverse()  # [racine, ..., dossier_id]

    parent_miroir_id = None
    dossier_miroir_id = None
    for d in chaine:
        dossier_miroir_id = _obtenir_ou_creer_miroir(d["id"], receveur_id, d["nom"], parent_miroir_id)
        parent_miroir_id = dossier_miroir_id
    return dossier_miroir_id


def _copier_fichier_public_pour_receveur(fichier_id: str, receveur_id: str) -> str | None:
    """Copie fichier_id (bibliotheque_publique) chez receveur_id -- lien
    (enregistrer_lien) ou fichier réel (enregistrer_fichier, avec la même
    file d'attente de vectorisation qu'un upload normal). Renvoie le
    nouvel id chez le receveur, ou None si erreur/doublon (cas attendu,
    silencieusement ignoré comme dans copier_depuis_bibliotheque_publique)."""
    try:
        ligne = (
            supabase.table("bibliotheque_publique")
            .select("nom, description, nom_fichier, type_mime, chemin_stockage, statut")
            .eq("id", fichier_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture fichier public {fichier_id} avant copie miroir) : {e}")
        return None
    if not ligne or not ligne.data or ligne.data["statut"] != "publie":
        return None
    f = ligne.data
    description_finale = (f.get("description") or "").strip() or f["nom"]

    if f["type_mime"] == "text/uri-list":
        try:
            nouvelle = enregistrer_lien(
                url=f["chemin_stockage"],
                nom_fichier=f["nom_fichier"] or f["nom"],
                niveau="utilisateur",
                uploade_par=receveur_id,
                user_id=receveur_id,
                description=description_finale,
            )
        except Exception as e:
            logging.error(f"ERREUR copie miroir (lien public {fichier_id} -> {receveur_id}) : {e}")
            return None
        return nouvelle["id"]

    try:
        contenu = supabase.storage.from_(BUCKET).download(f["chemin_stockage"])
    except Exception as e:
        logging.error(f"ERREUR téléchargement fichier public {fichier_id} (copie miroir -> {receveur_id}) : {e}")
        return None
    if not contenu:
        return None

    try:
        nouvelle = enregistrer_fichier(
            contenu=contenu,
            nom_fichier=f["nom_fichier"] or f["nom"],
            type_mime=f["type_mime"],
            niveau="utilisateur",
            uploade_par=receveur_id,
            user_id=receveur_id,
            description=description_finale,
            statut_vectorisation="en_attente" if necessite_vectorisation_fichier_privee(f["type_mime"]) else "pret",
        )
    except Exception as e:
        logging.error(f"ERREUR copie miroir (fichier public {fichier_id} -> {receveur_id}) : {e}")
        return None
    return nouvelle["id"]


def attacher_dossier(dossier_racine_id: str, receveur_id: str) -> dict | None:
    """
    Attache dossier_racine_id (catalogue public) à la bibliothèque perso
    de receveur_id : enregistre l'attachement (idempotent) PUIS copie
    rétroactivement tout le contenu ACTUEL du dossier et de ses
    sous-dossiers dans des dossiers miroirs chez receveur_id. Renvoie le
    dossier public attaché (via _dossier), ou None s'il n'existe pas.
    Non bloquant : chaque erreur individuelle est juste loguée, jamais de
    blocage sur un fichier cassé (même principe que
    codes_partage.py::propager_dossier_vers_receveur).
    """
    dossier = _dossier(dossier_racine_id)
    if not dossier:
        return None

    supabase.table("dossiers_publics_attaches").upsert({
        "user_id": receveur_id,
        "dossier_public_id": dossier_racine_id,
    }).execute()

    tous_dossiers = lister_dossiers_publics()
    par_id = {d["id"]: d for d in tous_dossiers}
    for dossier_id in _sous_arbre_dossiers_publics(dossier_racine_id, tous_dossiers):
        try:
            dossier_miroir_id = _chemin_miroir(dossier_id, dossier_racine_id, par_id, receveur_id)
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (construction miroir dossier public {dossier_id} / {receveur_id}) : {e}")
            continue
        for fichier_id in lister_fichiers_ids_dossier_public(dossier_id):
            nouvel_id = _copier_fichier_public_pour_receveur(fichier_id, receveur_id)
            if nouvel_id:
                ranger_fichier(nouvel_id, dossier_miroir_id)

    return dossier


def detacher_dossier(dossier_racine_id: str, receveur_id: str) -> None:
    """Arrête la synchronisation future -- la copie déjà faite dans la
    bibliothèque perso n'est JAMAIS supprimée automatiquement, voir
    docstring du module."""
    supabase.table("dossiers_publics_attaches").delete().eq("user_id", receveur_id).eq("dossier_public_id", dossier_racine_id).execute()


def _racines_attachees(dossier_id: str) -> list[str]:
    """Renvoie les dossier_public_id RACINE (parmi les ancêtres de
    dossier_id, lui-même inclus) qui sont attachés par au moins un
    utilisateur -- même principe que codes_partage.py::_ancetres_partages."""
    tous_dossiers = lister_dossiers_publics()
    par_id = {d["id"]: d for d in tous_dossiers}
    if dossier_id not in par_id:
        return []
    chaine = []
    courant = dossier_id
    while courant:
        chaine.append(courant)
        courant = par_id.get(courant, {}).get("dossier_parent_id")

    try:
        liaisons = (
            supabase.table("dossiers_publics_attaches")
            .select("dossier_public_id")
            .in_("dossier_public_id", chaine)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture racines attachées ancêtres de {dossier_id}) : {e}")
        return []
    return list({l["dossier_public_id"] for l in (liaisons.data or [])})


def propager_fichier_public_range_dossier(fichier_id: str, dossier_id: str) -> None:
    """
    Appelée à chaque fichier rangé dans un dossier du catalogue public
    (api/dossiers_catalogue_public.py::ranger et
    api/bibliotheque_publique.py::_classer_si_autorise). Si dossier_id
    (ou un de ses ancêtres) est attaché par un ou plusieurs utilisateurs,
    copie ce fichier chez chacun, rangé dans son dossier miroir
    correspondant. Non bloquant.
    """
    tous_dossiers = lister_dossiers_publics()
    par_id = {d["id"]: d for d in tous_dossiers}
    for dossier_racine_id in _racines_attachees(dossier_id):
        try:
            receveurs = supabase.table("dossiers_publics_attaches").select("user_id").eq("dossier_public_id", dossier_racine_id).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture utilisateurs ayant attaché {dossier_racine_id}) : {e}")
            continue
        for r in (receveurs.data or []):
            receveur_id = r["user_id"]
            try:
                dossier_miroir_id = _chemin_miroir(dossier_id, dossier_racine_id, par_id, receveur_id)
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (construction miroir dossier public {dossier_id} / {receveur_id}) : {e}")
                continue
            nouvel_id = _copier_fichier_public_pour_receveur(fichier_id, receveur_id)
            if nouvel_id:
                ranger_fichier(nouvel_id, dossier_miroir_id)


def lister_dossiers_attaches(user_id: str) -> list:
    """
    Liste les dossiers publics (racines) attachés par user_id, avec pour
    chacun l'id de son dossier miroir personnel (dossier_bibliotheque_id)
    -- utilisé côté frontend pour afficher le badge "origine : dossier
    public X" sur le dossier perso correspondant, et pour proposer le
    détachement.
    """
    try:
        liaisons = (
            supabase.table("dossiers_publics_attaches")
            .select("dossier_public_id, dossiers_catalogue_public(id, nom, statut, cree_par)")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture dossiers publics attachés par {user_id}) : {e}")
        return []
    resultat = []
    for l in (liaisons.data or []):
        dossier = l.get("dossiers_catalogue_public")
        if not dossier:
            continue  # dossier public supprimé entre-temps -- ligne orpheline ignorée
        try:
            miroir = (
                supabase.table("miroirs_dossiers_publics")
                .select("dossier_miroir_id")
                .eq("dossier_source_id", dossier["id"])
                .eq("receveur_id", user_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture miroir racine {dossier['id']} / {user_id}) : {e}")
            miroir = None
        dossier["dossier_bibliotheque_id"] = miroir.data["dossier_miroir_id"] if miroir and miroir.data else None
        resultat.append(dossier)
    return resultat
