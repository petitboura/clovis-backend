"""
Signalements de contenu (bibliothèque publique + documents publics de
programme). 22/08/2026, chantier "rendre la bibliothèque plus
sérieuse" (voir guide Notion "Guide pour droit d'auteur", Phase 1 :
formulaire de signalement + procédure de retrait).

Mode de modération choisi par Bourama : publication immédiate, retrait
a posteriori sur signalement traité par un admin (_est_admin, voir
api/permissions_hierarchie.py), pas de validation a priori.

Création ouverte à TOUT LE MONDE, y compris sans compte (un ayant
droit externe (éditeur, auteur) n'a aucune raison d'avoir un compte
Djiguignè) : utilisateur_optionnel, jamais utilisateur_courant, sur la
route POST. Lecture/traitement réservés à l'admin.
"""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant, utilisateur_optionnel, supabase
from api.journal import journaliser
from api.permissions_hierarchie import _est_admin
from core.erreurs import erreur_api
# Fonctionnalité "Programme" désactivée et isolée le 29/08/2026 (demande
# Bourama) -- voir _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md.
# from core.bibliotheque_programme import declasser_document_admin

router = APIRouter(prefix="/api/signalements", tags=["signalements"])

# "document_programme" retiré des types valides le 29/08/2026 (fonctionnalité
# "Programme" désactivée) -- plus aucun nouveau signalement de ce type ne
# peut être créé. D'éventuels signalements déjà en base restent visibles à
# l'admin mais ne peuvent plus être "retirés" via cet endpoint (voir plus bas).
TYPES_SIGNALEMENT = ("bibliotheque_publique",)


def _exige_admin(utilisateur):
    if not _est_admin(utilisateur.id):
        raise erreur_api(403, "PAS_LE_DROIT_ADMIN")


class CreerSignalementPayload(BaseModel):
    type_signalement: str
    bibliotheque_publique_id: str | None = None
    fichier_id: str | None = None
    type_emplacement: str | None = None
    emplacement_id: str | None = None
    lien_document: str
    motif: str
    plaignant_nom: str
    plaignant_email: str
    plaignant_organisation: str | None = None
    declaration_honneur: bool = False


class SignalementReponse(BaseModel):
    id: str
    type_signalement: str
    bibliotheque_publique_id: str | None = None
    fichier_id: str | None = None
    type_emplacement: str | None = None
    emplacement_id: str | None = None
    lien_document: str
    motif: str
    plaignant_nom: str
    plaignant_email: str
    plaignant_organisation: str | None = None
    declaration_honneur: bool
    statut: str
    action: str | None = None
    notes_admin: str | None = None
    created_at: str
    traite_le: str | None = None


@router.post("", response_model=SignalementReponse, status_code=201)
def creer_signalement(
    payload: CreerSignalementPayload,
    request: Request,
    utilisateur=Depends(utilisateur_optionnel),
):
    if payload.type_signalement not in TYPES_SIGNALEMENT:
        raise erreur_api(422, "TYPE_DE_SIGNALEMENT_INVALIDE")
    if payload.type_signalement == "bibliotheque_publique" and not (payload.bibliotheque_publique_id or "").strip():
        raise erreur_api(400, "CIBLE_DU_SIGNALEMENT_MANQUANTE")
    if payload.type_signalement == "document_programme" and not (
        (payload.fichier_id or "").strip() and (payload.type_emplacement or "").strip() and (payload.emplacement_id or "").strip()
    ):
        raise erreur_api(400, "CIBLE_DU_SIGNALEMENT_MANQUANTE")
    if not payload.motif.strip():
        raise erreur_api(400, "MOTIF_REQUIS")
    if not payload.plaignant_nom.strip() or not payload.plaignant_email.strip():
        raise erreur_api(400, "COORDONNEES_PLAIGNANT_REQUISES")
    if not payload.declaration_honneur:
        raise erreur_api(400, "DECLARATION_SUR_L_HONNEUR_REQUISE")

    ligne = {
        "type_signalement": payload.type_signalement,
        "bibliotheque_publique_id": payload.bibliotheque_publique_id,
        "fichier_id": payload.fichier_id,
        "type_emplacement": payload.type_emplacement,
        "emplacement_id": payload.emplacement_id,
        "lien_document": payload.lien_document.strip(),
        "motif": payload.motif.strip(),
        "plaignant_nom": payload.plaignant_nom.strip(),
        "plaignant_email": payload.plaignant_email.strip(),
        "plaignant_organisation": (payload.plaignant_organisation or "").strip() or None,
        "declaration_honneur": payload.declaration_honneur,
    }

    try:
        res = supabase.table("signalements_bibliotheque").insert(ligne).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création signalement, {payload.type_signalement}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    journaliser(
        action="signalement.cree",
        user_id=utilisateur.id if utilisateur else None,
        cible_type=payload.type_signalement,
        cible_id=payload.bibliotheque_publique_id or payload.fichier_id,
        details={"motif": payload.motif.strip()},
        request=request,
    )

    return res.data[0]


@router.get("", response_model=list[SignalementReponse])
def lister_signalements(statut: str | None = None, utilisateur=Depends(utilisateur_courant)):
    _exige_admin(utilisateur)
    requete = supabase.table("signalements_bibliotheque").select("*")
    if statut in ("en_attente", "traite"):
        requete = requete.eq("statut", statut)
    res = requete.order("created_at", desc=True).limit(200).execute()
    return res.data or []


class TraiterSignalementPayload(BaseModel):
    action: str
    notes_admin: str | None = None


@router.post("/{signalement_id}/traiter", response_model=SignalementReponse)
def traiter_signalement(
    signalement_id: str,
    payload: TraiterSignalementPayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    _exige_admin(utilisateur)
    if payload.action not in ("retire", "rejete"):
        raise erreur_api(422, "ACTION_INVALIDE")

    res = supabase.table("signalements_bibliotheque").select("*").eq("id", signalement_id).maybe_single().execute()
    if not res or not res.data:
        raise erreur_api(404, "SIGNALEMENT_INTROUVABLE")
    signalement = res.data

    if signalement["statut"] == "traite":
        raise erreur_api(409, "SIGNALEMENT_DEJA_TRAITE")

    if payload.action == "retire":
        if signalement["type_signalement"] == "bibliotheque_publique":
            try:
                supabase.table("bibliotheque_publique").update({
                    "statut": "retire",
                    "retire_motif": payload.notes_admin or signalement["motif"],
                    "retire_le": "now()",
                }).eq("id", signalement["bibliotheque_publique_id"]).execute()
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (retrait bibliotheque_publique {signalement['bibliotheque_publique_id']}) : {e}")
                raise erreur_api(500, "ERREUR_INCONNUE")
        else:
            # "document_programme" désactivé le 29/08/2026 -- ne peut plus
            # être retiré via cet endpoint (voir
            # _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md).
            raise erreur_api(409, "TYPE_SIGNALEMENT_DESACTIVE", message="Ce type de signalement n'est plus traitable.")

    try:
        maj = supabase.table("signalements_bibliotheque").update({
            "statut": "traite",
            "action": payload.action,
            "notes_admin": payload.notes_admin,
            "traite_par": utilisateur.id,
            "traite_le": "now()",
        }).eq("id", signalement_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (traitement signalement {signalement_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    journaliser(
        action="signalement.traite",
        user_id=utilisateur.id,
        cible_type=signalement["type_signalement"],
        cible_id=signalement_id,
        details={"action": payload.action},
        request=request,
    )

    return maj.data[0]
