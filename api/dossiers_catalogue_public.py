"""
Routes REST pour les dossiers du catalogue public (28/08/2026, demande
Bourama). Toute la logique vit dans core/dossiers_catalogue_public.py,
voir sa docstring pour les règles contribution_libre/privee.
"""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from api.auth import utilisateur_courant, utilisateur_optionnel
from core.erreurs import erreur_api
from core.dossiers_catalogue_public import (
    _dossier,
    confirmer_demande,
    creer_demande,
    creer_dossier,
    deplacerait_en_boucle,
    deplacer_dossier,
    lister_demandes_en_attente,
    lister_dossiers,
    lister_fichiers_ids_dossier,
    peut_ajouter_contenu,
    peut_retirer_contenu,
    ranger_fichier,
    refuser_demande,
    renommer_dossier,
    retirer_fichier,
    supprimer_dossier,
)
from core.dossiers_publics_attaches import (
    attacher_dossier as _attacher_dossier_public,
    detacher_dossier as _detacher_dossier_public,
    lister_dossiers_attaches,
    propager_fichier_public_range_dossier,
)
from core.geolocalisation_pays import pays_utilisateur
from core.listes_bibliotheque_publique import normaliser_et_enregistrer

router = APIRouter(prefix="/api/bibliotheque-publique/dossiers", tags=["dossiers-catalogue-public"])


class CreerDossierPayload(BaseModel):
    nom: str = ""
    description: str = ""
    statut: str = "contribution_libre"
    dossier_parent_id: str | None = None
    pays: str = ""
    niveau: str = ""
    categorie: str = ""
    classe: str = ""
    specialite: str = ""


class RenommerDossierPayload(BaseModel):
    nom: str


class RangerFichierPayload(BaseModel):
    fichier_id: str


class DeplacerFichierPayload(BaseModel):
    dossier_destination_id: str


class DeplacerDossierPayload(BaseModel):
    dossier_destination_id: str | None = None


@router.get("")
def lister(request: Request, utilisateur=Depends(utilisateur_optionnel)):
    # 10/09/2026, chantier "Clovis ouvert" (Lot F, sitemap) : utilisateur_courant
    # -> utilisateur_optionnel. Signale au Lot E, corrige ici : cette liste ne
    # filtre déjà rien par utilisateur (lister_dossiers() le documente,
    # "visible par tout le monde"), l'auth obligatoire n'apportait donc
    # aucune protection réelle -- elle empêchait juste le générateur de
    # sitemap (sans session) de trouver les dossiers à indexer.
    # 08/09/2026, demande Bourama : les dossiers du pays détecté de
    # l'utilisateur remontent en tête de liste (voir core/geolocalisation_pays.py).
    dossiers = lister_dossiers(pays_prioritaire=pays_utilisateur(request))
    for d in dossiers:
        d["fichier_ids"] = lister_fichiers_ids_dossier(d["id"])
    return dossiers


@router.post("", status_code=201)
def creer(payload: CreerDossierPayload, utilisateur=Depends(utilisateur_courant)):
    if payload.statut not in ("contribution_libre", "privee"):
        raise erreur_api(400, "STATUT_INVALIDE")
    # Nom optionnel (28/08, demande Bourama : "nom et description optionnels même pour dossier") -- repli sur "Nouveau dossier".
    nom = (payload.nom or "").strip() or "Nouveau dossier"
    return creer_dossier(
        utilisateur.id, nom, payload.statut, payload.dossier_parent_id,
        pays=normaliser_et_enregistrer("pays", payload.pays),
        niveau=normaliser_et_enregistrer("niveau", payload.niveau),
        categorie=normaliser_et_enregistrer("categorie", payload.categorie),
        classe=normaliser_et_enregistrer("classe", payload.classe),
        specialite=normaliser_et_enregistrer("specialite", payload.specialite),
        # 08/09/2026, demande Bourama : dossiers = même logique que les fichiers, description optionnelle.
        description=(payload.description or "").strip(),
    )


# --- Demandes en attente de confirmation (09/09/2026, demande Bourama) ----
# Placées AVANT les routes "/{dossier_id}" ci-dessous pour ne jamais
# risquer qu'un futur "/{dossier_id}" en GET n'intercepte "/demandes".


@router.get("/demandes")
def lister_mes_demandes(utilisateur=Depends(utilisateur_courant)):
    return lister_demandes_en_attente(utilisateur.id)


@router.post("/demandes/{demande_id}/confirmer")
def confirmer(demande_id: str, utilisateur=Depends(utilisateur_courant)):
    return confirmer_demande(demande_id, utilisateur.id)


@router.post("/demandes/{demande_id}/refuser")
def refuser(demande_id: str, utilisateur=Depends(utilisateur_courant)):
    return refuser_demande(demande_id, utilisateur.id)


@router.patch("/{dossier_id}")
def renommer(dossier_id: str, payload: RenommerDossierPayload, utilisateur=Depends(utilisateur_courant)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if dossier["cree_par"] != utilisateur.id:
        raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")
    nouveau_nom = (payload.nom or "").strip() or "Nouveau dossier"
    renommer_dossier(dossier_id, nouveau_nom)
    return {"id": dossier_id, "nom": nouveau_nom}


@router.delete("/{dossier_id}")
def supprimer(dossier_id: str, response: Response, utilisateur=Depends(utilisateur_courant)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if dossier["cree_par"] == utilisateur.id:
        supprimer_dossier(dossier_id)
        response.status_code = 204
        return None
    # 09/09/2026, demande Bourama : un SOUS-dossier (dossier_parent_id
    # non nul) à contribution libre peut être proposé à la suppression
    # par n'importe qui -- bloqué tant que son propre créateur n'a pas
    # confirmé. Le dossier racine lui-même reste strictement réservé au
    # créateur, comportement inchangé (403 ci-dessous).
    if dossier["dossier_parent_id"] and dossier["statut"] == "contribution_libre":
        demande = creer_demande(
            action="supprimer_dossier",
            createur_id=dossier["cree_par"],
            demandeur_id=utilisateur.id,
            dossier_id=dossier_id,
        )
        response.status_code = 202
        return demande
    raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")


@router.post("/{dossier_id}/deplacer")
def deplacer(dossier_id: str, payload: DeplacerDossierPayload, response: Response, utilisateur=Depends(utilisateur_courant)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if payload.dossier_destination_id and not _dossier(payload.dossier_destination_id):
        raise erreur_api(404, "DOSSIER_DESTINATION_INTROUVABLE")
    if deplacerait_en_boucle(dossier_id, payload.dossier_destination_id):
        raise erreur_api(400, "DEPLACEMENT_CREERAIT_UNE_BOUCLE")

    if dossier["cree_par"] == utilisateur.id:
        deplacer_dossier(dossier_id, payload.dossier_destination_id)
        response.status_code = 200
        return {"id": dossier_id, "dossier_parent_id": payload.dossier_destination_id}
    if dossier["dossier_parent_id"] and dossier["statut"] == "contribution_libre":
        demande = creer_demande(
            action="deplacer_dossier",
            createur_id=dossier["cree_par"],
            demandeur_id=utilisateur.id,
            dossier_id=dossier_id,
            dossier_destination_id=payload.dossier_destination_id,
        )
        response.status_code = 202
        return demande
    raise erreur_api(403, "CE_DOSSIER_NE_T_APPARTIENT_PAS")


@router.post("/{dossier_id}/fichiers", status_code=201)
def ranger(dossier_id: str, payload: RangerFichierPayload, utilisateur=Depends(utilisateur_courant)):
    if not _dossier(dossier_id):
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if not peut_ajouter_contenu(dossier_id, utilisateur.id):
        raise erreur_api(403, "CE_DOSSIER_EST_PRIVE_A_SON_CREATEUR")
    ranger_fichier(payload.fichier_id, dossier_id)
    propager_fichier_public_range_dossier(payload.fichier_id, dossier_id)
    return {"dossier_id": dossier_id, "fichier_id": payload.fichier_id}


@router.delete("/{dossier_id}/fichiers/{fichier_id}")
def retirer(dossier_id: str, fichier_id: str, response: Response, utilisateur=Depends(utilisateur_courant)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    # 28/08, correctif Bourama : retirer reste immédiat uniquement pour
    # le créateur du dossier -- ÉVOLUTION 09/09/2026 (demande Bourama,
    # "confirmation contributeurs") : en contribution_libre, un autre
    # contributeur peut désormais le PROPOSER, bloqué tant que ce
    # créateur n'a pas confirmé (voir core/dossiers_catalogue_public.py).
    if peut_retirer_contenu(dossier_id, utilisateur.id):
        retirer_fichier(fichier_id, dossier_id)
        response.status_code = 204
        return None
    if dossier["statut"] == "contribution_libre":
        demande = creer_demande(
            action="supprimer_fichier",
            createur_id=dossier["cree_par"],
            demandeur_id=utilisateur.id,
            dossier_id=dossier_id,
            fichier_id=fichier_id,
        )
        response.status_code = 202
        return demande
    raise erreur_api(403, "SEUL_LE_CREATEUR_DU_DOSSIER_PEUT_EN_RETIRER_UN_FICHIER")


@router.post("/{dossier_id}/fichiers/{fichier_id}/deplacer")
def deplacer_fichier_endpoint(
    dossier_id: str, fichier_id: str, payload: DeplacerFichierPayload, response: Response,
    utilisateur=Depends(utilisateur_courant),
):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    if not _dossier(payload.dossier_destination_id):
        raise erreur_api(404, "DOSSIER_DESTINATION_INTROUVABLE")
    # Ranger dans la destination reste immédiat pour tout le monde (même
    # règle que POST .../fichiers ci-dessus) -- seul le retrait de la
    # source peut nécessiter une confirmation, juste en-dessous.
    if not peut_ajouter_contenu(payload.dossier_destination_id, utilisateur.id):
        raise erreur_api(403, "CE_DOSSIER_EST_PRIVE_A_SON_CREATEUR")

    if peut_retirer_contenu(dossier_id, utilisateur.id):
        retirer_fichier(fichier_id, dossier_id)
        ranger_fichier(fichier_id, payload.dossier_destination_id)
        response.status_code = 200
        return {"fichier_id": fichier_id, "dossier_id": payload.dossier_destination_id}
    if dossier["statut"] == "contribution_libre":
        demande = creer_demande(
            action="deplacer_fichier",
            createur_id=dossier["cree_par"],
            demandeur_id=utilisateur.id,
            dossier_id=dossier_id,
            fichier_id=fichier_id,
            dossier_destination_id=payload.dossier_destination_id,
        )
        response.status_code = 202
        return demande
    raise erreur_api(403, "SEUL_LE_CREATEUR_DU_DOSSIER_PEUT_EN_RETIRER_UN_FICHIER")


# --- Attachement à la bibliothèque perso (02/09/2026, demande Bourama) -
# Copie réelle dans la bibliothèque perso (dossier miroir), synchronisée
# en continu -- voir docstring de core/dossiers_publics_attaches.py.
# Attacher est libre pour n'importe quel dossier public quel que soit
# son statut.


@router.get("/attaches")
def lister_attaches(utilisateur=Depends(utilisateur_courant)):
    return lister_dossiers_attaches(utilisateur.id)


@router.post("/{dossier_id}/attacher", status_code=201)
def attacher(dossier_id: str, utilisateur=Depends(utilisateur_courant)):
    dossier = _attacher_dossier_public(dossier_id, utilisateur.id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    return dossier


@router.delete("/{dossier_id}/attacher", status_code=204)
def detacher(dossier_id: str, utilisateur=Depends(utilisateur_courant)):
    _detacher_dossier_public(dossier_id, utilisateur.id)


# 10/09/2026, chantier "Clovis ouvert" (Lot E, demande Bourama : chaque
# dossier du catalogue public retrouvable par son nom, avec son propre
# lien). Déclarée en tout dernier, après TOUTE route statique à un seul
# segment ("/demandes", "/attaches") -- sinon "/{dossier_id}" les
# intercepterait (même piège que documenté plus haut pour "/demandes").
#
# Pas de filtre sur `statut` : lister_dossiers() ci-dessus le documente
# déjà, "contribution_libre"/"privee" ne concerne QUE le droit d'ajouter
# du contenu (voir peut_ajouter_contenu), tous les dossiers sont
# "visible[s] par tout le monde" -- ce endpoint ne fait qu'exposer
# publiquement (sans exiger de compte, contrairement à lister() ci-dessus)
# ce qui l'était déjà pour un utilisateur connecté.
@router.get("/{dossier_id}")
def obtenir_dossier_public(dossier_id: str, utilisateur=Depends(utilisateur_optionnel)):
    dossier = _dossier(dossier_id)
    if not dossier:
        raise erreur_api(404, "DOSSIER_INTROUVABLE")
    dossier["fichier_ids"] = lister_fichiers_ids_dossier(dossier_id)
    return dossier
