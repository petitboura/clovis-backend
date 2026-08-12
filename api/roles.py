"""
Hiérarchie de rôles (nous/établissement/enseignant/étudiant), ajoutée le
2026-08-04 (demande Bourama). Voir migrations/2026_08_04_roles_hierarchie.sql
pour le schéma et api/permissions_hierarchie.py pour les vérifications de
droits réutilisées par api/agents.py.

Rattachement choisi UNE FOIS par l'utilisateur à l'inscription (menu
déroulant, pas d'invitation -- décision Bourama), jamais modifiable
ensuite via cet endpoint (repli : à corriger à la main en base par
Bourama en cas d'erreur, cas rare).

Pas d'UI vitrine pour cette fonctionnalité (demande Bourama) : purement
fonctionnel, branché dans l'espace connecté de l'app.
"""

import os
import sys
import logging
import tempfile
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from api.permissions_hierarchie import _lire_profil_role
from core.erreurs import erreur_api

# Même sys.path que api/agents.py (l'import de api.agents ci-dessous
# suffirait déjà à le garantir vu l'ordre d'import dans main.py, mais on
# le refait ici pour que ce module reste indépendant de cet ordre --
# DOIT précéder les imports "bare" (creation_agent, bibliotheque_fichiers,
# storage, index_documents) qui en dépendent.
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "indexers"))
from creation_agent import generer_id_depuis_nom  # noqa: E402
from bibliotheque_fichiers import enregistrer_fichier, enregistrer_lien  # noqa: E402
from storage import upload_document  # noqa: E402
from index_documents import indexer_document  # noqa: E402
from api.agents import TYPES_BIBLIOTHEQUE_AUTORISES, TAILLE_MAX_BIBLIOTHEQUE_OCTETS  # noqa: E402

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/roles", tags=["roles"])

ROLES_VALIDES = ("etablissement", "enseignant", "etudiant")

# IA fixes, une par rôle, déjà en base (table `agents`, project
# rwcyeppxfonvqbvztxyg) -- même mapping que AGENT_PAR_ROLE côté vitrine
# (djiguigne-ai/components/InscriptionEtablissements.tsx). Remplace le
# principe "on crée une nouvelle IA à chaque inscription" (06/08, demande
# Bourama : "supprimer ce principe, les trois sont redirigés vers des IA
# spécifiques à chacune") -- avant cette date, choisir_role() appelait
# _creer_agent_minimal() qui insérait une ligne `agents` par utilisateur,
# publiée par défaut (`actif` jamais renseigné à l'INSERT -> NULL ->
# traité comme publiée partout ailleurs dans la plateforme), donc visible
# dans le feed découverte comme n'importe quelle IA de créateur -- jamais
# voulu, effet de bord du principe lui-même, pas un bug isolé.
AGENT_PAR_ROLE = {
    "etudiant": "nitrux",
    "enseignant": "stirux",
    "etablissement": "lirinus",
}


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


class ChoisirRolePayload(BaseModel):
    role: Literal["etablissement", "enseignant", "etudiant"]
    etablissement_id: Optional[str] = None
    enseignant_id: Optional[str] = None
    # Étape "profil" ajoutée le 06/08 (Bourama) : nom_affiche fourni par
    # l'utilisateur (obligatoire), remplace le repli "Sans nom" utilisé
    # jusqu'ici. Tous les autres champs sont optionnels -- voir
    # migration profils_champs_inscription_hierarchie pour le schéma.
    nom_affiche: str
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    site_web: Optional[str] = None
    contact: Optional[str] = None
    email_contact: Optional[str] = None
    matiere: Optional[str] = None


class MonRoleReponse(BaseModel):
    role: Optional[str] = None
    etablissement_id: Optional[str] = None
    enseignant_id: Optional[str] = None
    agent_id: Optional[str] = None


@router.post("/choisir", response_model=MonRoleReponse)
def choisir_role(payload: ChoisirRolePayload, request: Request, utilisateur=Depends(utilisateur_courant)):
    profil_existant = _lire_profil_role(utilisateur.id)
    if profil_existant and profil_existant.get("role"):
        raise erreur_api(409, "ROLE_DEJA_CHOISI")

    ligne_maj = {"role": payload.role}

    # Rattachement (établissement pour un enseignant) : optionnel depuis
    # le 06/08 (Bourama, étape "profil"). L'ID reste vérifié s'il est
    # fourni, mais son absence ne bloque plus l'inscription. Le
    # rattachement étudiant->enseignant a été retiré de ce parcours (plus
    # de champ correspondant côté formulaire), enseignant_id n'est donc
    # jamais rempli ici en pratique, mais le payload le garde par
    # compatibilité avec /api/roles/moi qui l'expose.
    if payload.role == "enseignant" and payload.etablissement_id:
        cible = _lire_profil_role(payload.etablissement_id)
        if not cible or cible.get("role") != "etablissement":
            raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")
        ligne_maj["etablissement_id"] = payload.etablissement_id

    elif payload.role == "etudiant" and payload.enseignant_id:
        cible = _lire_profil_role(payload.enseignant_id)
        if not cible or cible.get("role") != "enseignant":
            raise erreur_api(404, "ENSEIGNANT_INTROUVABLE")
        ligne_maj["enseignant_id"] = payload.enseignant_id

    # Champs de profil (06/08) : tous optionnels sauf nom_affiche, jamais
    # écrasés par une valeur vide si l'utilisateur a laissé le champ vide
    # (garde le repli "Sans nom" impossible mais évite d'écrire des
    # chaînes vides en base pour les champs optionnels non remplis).
    ligne_maj["nom_affiche"] = payload.nom_affiche.strip() or "Sans nom"
    for champ in ("bio", "avatar_url", "site_web", "contact", "email_contact", "matiere"):
        valeur = getattr(payload, champ)
        if valeur and valeur.strip():
            ligne_maj[champ] = valeur.strip()

    # Upsert : même situation que PATCH /api/profiles/me, rien ne
    # garantit qu'une ligne `profiles` existe déjà (voir docstring
    # équivalente dans api/profiles.py).
    try:
        deja = supabase.table("profiles").select("slug").eq("user_id", utilisateur.id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification profil existant {utilisateur.id}) : {e}")
        deja = None

    try:
        if deja and deja.data:
            supabase.table("profiles").update(ligne_maj).eq("user_id", utilisateur.id).execute()
        else:
            ligne_maj["user_id"] = utilisateur.id
            ligne_maj["slug"] = generer_id_depuis_nom(utilisateur.id[:8]) or utilisateur.id[:8]
            supabase.table("profiles").insert(ligne_maj).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (écriture rôle {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    agent_id = AGENT_PAR_ROLE[payload.role]

    journaliser(
        action="role.choisi",
        user_id=utilisateur.id,
        cible_type="profile",
        cible_id=utilisateur.id,
        details={"role": payload.role, "agent_id": agent_id},
        request=request,
    )

    return MonRoleReponse(
        role=payload.role,
        etablissement_id=ligne_maj.get("etablissement_id"),
        enseignant_id=ligne_maj.get("enseignant_id"),
        agent_id=agent_id,
    )


@router.get("/moi", response_model=MonRoleReponse)
def mon_role(utilisateur=Depends(utilisateur_courant)):
    profil = _lire_profil_role(utilisateur.id)
    if not profil or not profil.get("role"):
        return MonRoleReponse()
    return MonRoleReponse(
        role=profil.get("role"),
        etablissement_id=profil.get("etablissement_id"),
        enseignant_id=profil.get("enseignant_id"),
        agent_id=AGENT_PAR_ROLE.get(profil.get("role")),
    )


class CompteListe(BaseModel):
    user_id: str
    nom_affiche: str


@router.get("/etablissements", response_model=List[CompteListe])
def lister_etablissements():
    """
    Public (pas d'auth) : alimente le menu déroulant "Ton établissement"
    du formulaire d'inscription enseignant.
    """
    try:
        res = supabase.table("profiles").select("user_id, nom_affiche").eq("role", "etablissement").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste établissements) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [
        CompteListe(user_id=r["user_id"], nom_affiche=r.get("nom_affiche") or "Sans nom") for r in (res.data or [])
    ]


@router.get("/enseignants", response_model=List[CompteListe])
def lister_enseignants(etablissement_id: Optional[str] = None):
    """
    Public (pas d'auth) : alimente le menu déroulant "Ton enseignant" du
    formulaire d'inscription étudiant. Filtrable par établissement si le
    front veut d'abord faire choisir l'établissement.
    """
    try:
        requete = supabase.table("profiles").select("user_id, nom_affiche").eq("role", "enseignant")
        if etablissement_id:
            requete = requete.eq("etablissement_id", etablissement_id)
        res = requete.execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste enseignants) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [
        CompteListe(user_id=r["user_id"], nom_affiche=r.get("nom_affiche") or "Sans nom") for r in (res.data or [])
    ]


class MembreEquipe(BaseModel):
    user_id: str
    nom_affiche: str
    agent_id: Optional[str] = None


@router.get("/mon-equipe", response_model=List[MembreEquipe])
def mon_equipe(utilisateur=Depends(utilisateur_courant)):
    """
    Enseignant connecté -> ses étudiants. Établissement connecté -> ses
    enseignants. Chaque membre inclut `agent_id` (pour lier directement
    vers la page "Modifier"/"Tester" existante côté frontend) -- depuis le
    06/08, c'est l'IA fixe PARTAGÉE de leur rôle (nitrux pour tous les
    étudiants, stirux pour tous les enseignants -- voir AGENT_PAR_ROLE),
    pas une IA individuelle par membre : "Modifier" ici modifie l'IA de
    l'ensemble de ses élèves/enseignants, jamais celle d'un membre en
    particulier (confirmé par Bourama, cette page a toujours désigné une
    IA collective, jamais individuelle).
    """
    profil = _lire_profil_role(utilisateur.id)
    if not profil or profil.get("role") not in ("enseignant", "etablissement"):
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")

    colonne_filtre = "enseignant_id" if profil["role"] == "enseignant" else "etablissement_id"
    role_membres = "etudiant" if profil["role"] == "enseignant" else "enseignant"
    try:
        membres = (
            supabase.table("profiles")
            .select("user_id, nom_affiche")
            .eq(colonne_filtre, utilisateur.id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (mon-equipe {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    agent_id_membres = AGENT_PAR_ROLE[role_membres]
    resultat = []
    for m in membres.data or []:
        resultat.append(
            MembreEquipe(
                user_id=m["user_id"],
                nom_affiche=m.get("nom_affiche") or "Sans nom",
                agent_id=agent_id_membres,
            )
        )
    return resultat


class EnvoyerMessagePayload(BaseModel):
    destinataire_id: str
    contenu: str
    reponse_a: Optional[int] = None


class MessageDirect(BaseModel):
    id: int
    expediteur_id: str
    expediteur_nom: str
    destinataire_id: str
    contenu: str
    reponse_a: Optional[int] = None
    lu: bool
    created_at: str


def _etablissement_de_etudiant(etudiant: dict) -> Optional[str]:
    """
    L'étudiant n'a que `enseignant_id` en base, pas d'`etablissement_id`
    direct -- on remonte via son enseignant (même logique en deux niveaux
    que peut_gerer_base_connaissances dans permissions_hierarchie.py).
    """
    enseignant_id = etudiant.get("enseignant_id")
    if not enseignant_id:
        return None
    enseignant = _lire_profil_role(enseignant_id)
    return enseignant.get("etablissement_id") if enseignant else None


def _peut_echanger_messages(moi: dict, cible: dict) -> bool:
    """
    Établissement <-> enseignant (rattachement direct) ; enseignant <->
    son étudiant ; étudiant <-> étudiant du même établissement ; étudiant
    <-> son établissement (via son enseignant) -- élargi le 2026-08-04
    pour la messagerie enseignant/étudiant et l'outil IA envoyer_message.
    """
    role_moi, role_cible = moi.get("role"), cible.get("role")

    if role_moi == "etablissement" and role_cible == "enseignant":
        return cible.get("etablissement_id") == moi.get("user_id")
    if role_moi == "enseignant" and role_cible == "etablissement":
        return moi.get("etablissement_id") == cible.get("user_id")

    if role_moi == "enseignant" and role_cible == "etudiant":
        return cible.get("enseignant_id") == moi.get("user_id")
    if role_moi == "etudiant" and role_cible == "enseignant":
        return moi.get("enseignant_id") == cible.get("user_id")

    if role_moi == "etudiant" and role_cible == "etudiant":
        if moi.get("user_id") == cible.get("user_id"):
            return False
        etab_moi = _etablissement_de_etudiant(moi)
        return bool(etab_moi) and etab_moi == _etablissement_de_etudiant(cible)

    if role_moi == "etudiant" and role_cible == "etablissement":
        return _etablissement_de_etudiant(moi) == cible.get("user_id")
    if role_moi == "etablissement" and role_cible == "etudiant":
        return _etablissement_de_etudiant(cible) == moi.get("user_id")

    return False


def _profils_par_colonne(colonne: str, valeur: str, roles: tuple) -> List[dict]:
    try:
        res = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, role")
            .eq(colonne, valeur)
            .in_("role", roles)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (profils {colonne}={valeur}) : {e}")
        return []
    return res.data or []


def _contacts_autorises(moi: dict) -> List[dict]:
    """
    Liste les profils ({user_id, nom_affiche, role}) que `moi` a le droit
    de contacter d'après `_peut_echanger_messages` -- utilisée par
    l'outil IA envoyer_message (core/serveur_mcp_generation.py) pour
    résoudre un nom en destinataire, sans devoir tout parcourir.
    """
    role_moi, user_id = moi.get("role"), moi.get("user_id")
    resultats: List[dict] = []

    if role_moi == "etablissement":
        resultats += _profils_par_colonne("etablissement_id", user_id, ("enseignant",))
        # étudiants de tous ses enseignants
        for ens in _profils_par_colonne("etablissement_id", user_id, ("enseignant",)):
            resultats += _profils_par_colonne("enseignant_id", ens["user_id"], ("etudiant",))

    elif role_moi == "enseignant":
        etablissement_id = moi.get("etablissement_id")
        if etablissement_id:
            resultats.append(
                {
                    "user_id": etablissement_id,
                    "nom_affiche": _nom_affiche_ou_repli(etablissement_id),
                    "role": "etablissement",
                }
            )
        resultats += _profils_par_colonne("enseignant_id", user_id, ("etudiant",))

    elif role_moi == "etudiant":
        enseignant_id = moi.get("enseignant_id")
        enseignant = _lire_profil_role(enseignant_id) if enseignant_id else None
        if enseignant_id:
            resultats.append(
                {"user_id": enseignant_id, "nom_affiche": _nom_affiche_ou_repli(enseignant_id), "role": "enseignant"}
            )
        etablissement_id = enseignant.get("etablissement_id") if enseignant else None
        if etablissement_id:
            resultats.append(
                {
                    "user_id": etablissement_id,
                    "nom_affiche": _nom_affiche_ou_repli(etablissement_id),
                    "role": "etablissement",
                }
            )
            # tous les étudiants de l'établissement (tous enseignants confondus)
            for ens in _profils_par_colonne("etablissement_id", etablissement_id, ("enseignant",)):
                resultats += _profils_par_colonne("enseignant_id", ens["user_id"], ("etudiant",))

    return [r for r in resultats if r.get("user_id") != user_id]


class ContactAutorise(BaseModel):
    user_id: str
    nom_affiche: str
    role: str
    agent_id: Optional[str] = None


@router.get("/mes-contacts", response_model=List[ContactAutorise])
def mes_contacts(utilisateur=Depends(utilisateur_courant)):
    """
    Tous les contacts autorisés du compte connecté, tous rôles confondus
    -- réutilise _contacts_autorises (déjà utilisée par l'outil IA
    envoyer_message). Remplace mon-equipe pour l'affichage de la
    messagerie (2026-08-04, tâche C) : mon-equipe restait 403 pour un
    étudiant et ne couvrait pas les contacts "vers le haut" (enseignant
    -> établissement, étudiant -> enseignant/établissement/camarades).
    """
    profil = _lire_profil_role(utilisateur.id)
    if not profil or not profil.get("role"):
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")
    profil["user_id"] = utilisateur.id

    contacts = _contacts_autorises(profil)

    resultat = []
    for c in contacts:
        resultat.append(
            ContactAutorise(
                user_id=c["user_id"],
                nom_affiche=c.get("nom_affiche") or "Sans nom",
                role=c.get("role"),
                agent_id=AGENT_PAR_ROLE.get(c.get("role")),
            )
        )
    return resultat


def resoudre_destinataire_autorise(expediteur_id: str, nom_destinataire: str) -> tuple[Optional[str], Optional[str]]:
    """
    Résout `nom_destinataire` parmi les contacts autorisés de
    `expediteur_id`. Retourne (destinataire_id, erreur) -- l'un des deux
    vaut toujours None. Utilisée par l'outil IA envoyer_message.
    """
    moi = _lire_profil_role(expediteur_id)
    if not moi or not moi.get("role"):
        return None, "Cette fonctionnalité n'est pas disponible pour ce compte."
    moi["user_id"] = expediteur_id

    contacts = _contacts_autorises(moi)
    nom_normalise = nom_destinataire.strip().casefold()
    correspondances = [c for c in contacts if (c.get("nom_affiche") or "").strip().casefold() == nom_normalise]
    if not correspondances:
        correspondances = [c for c in contacts if nom_normalise in (c.get("nom_affiche") or "").strip().casefold()]

    if not correspondances:
        return None, f"Je ne trouve personne nommé {nom_destinataire} parmi tes contacts."
    if len(correspondances) > 1:
        noms = ", ".join(c["nom_affiche"] for c in correspondances)
        return None, f"Plusieurs personnes correspondent à {nom_destinataire} ({noms}) -- précise le nom complet."
    return correspondances[0]["user_id"], None


def _inserer_message(expediteur_id: str, destinataire_id: str, contenu: str, reponse_a: Optional[int] = None) -> dict:
    """
    Insertion brute dans messages_directs, sans vérification de droits
    (déjà faite par l'appelant) -- réutilisée par POST /api/roles/messages
    et par l'outil IA envoyer_message (core/serveur_mcp_generation.py).
    """
    res = (
        supabase.table("messages_directs")
        .insert(
            {
                "expediteur_id": expediteur_id,
                "destinataire_id": destinataire_id,
                "contenu": contenu.strip(),
                "reponse_a": reponse_a,
            }
        )
        .execute()
    )
    return res.data[0]


@router.post("/messages", response_model=MessageDirect, status_code=201)
def envoyer_message(payload: EnvoyerMessagePayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.contenu.strip():
        raise erreur_api(422, "MESSAGE_VIDE")

    moi = _lire_profil_role(utilisateur.id) or {}
    moi["user_id"] = utilisateur.id
    cible = _lire_profil_role(payload.destinataire_id)
    if not cible:
        raise erreur_api(404, "DESTINATAIRE_INTROUVABLE")
    cible["user_id"] = payload.destinataire_id

    if not _peut_echanger_messages(moi, cible):
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")

    try:
        ligne = _inserer_message(utilisateur.id, payload.destinataire_id, payload.contenu, payload.reponse_a)
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (envoi message {utilisateur.id} -> {payload.destinataire_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    return MessageDirect(
        id=ligne["id"],
        expediteur_id=ligne["expediteur_id"],
        expediteur_nom=_nom_affiche_ou_repli(utilisateur.id),
        destinataire_id=ligne["destinataire_id"],
        contenu=ligne["contenu"],
        reponse_a=ligne.get("reponse_a"),
        lu=ligne["lu"],
        created_at=ligne["created_at"],
    )


@router.get("/messages", response_model=List[MessageDirect])
def lister_mes_messages(utilisateur=Depends(utilisateur_courant)):
    """Messages reçus ET envoyés (les deux sens), triés du plus récent au plus ancien."""
    try:
        res = (
            supabase.table("messages_directs")
            .select("*")
            .or_(f"destinataire_id.eq.{utilisateur.id},expediteur_id.eq.{utilisateur.id}")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste messages {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    return [
        MessageDirect(
            id=l["id"],
            expediteur_id=l["expediteur_id"],
            expediteur_nom=_nom_affiche_ou_repli(l["expediteur_id"]),
            destinataire_id=l["destinataire_id"],
            contenu=l["contenu"],
            reponse_a=l.get("reponse_a"),
            lu=l["lu"],
            created_at=l["created_at"],
        )
        for l in (res.data or [])
    ]


class AnnoncePayload(BaseModel):
    contenu: str


@router.post("/annonce", status_code=201)
def envoyer_annonce(payload: AnnoncePayload, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Établissement -> tous ses enseignants + tous les étudiants de ces
    enseignants (confirmé par Bourama, 2026-08-04) -- PAS toute la
    plateforme. Le fan-out en notifications individuelles est fait par
    trigger Postgres (voir migration), pas ici.
    """
    if not payload.contenu.strip():
        raise erreur_api(422, "ANNONCE_VIDE")

    profil = _lire_profil_role(utilisateur.id)
    if not profil or profil.get("role") != "etablissement":
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")

    try:
        supabase.table("annonces_etablissement").insert(
            {"etablissement_id": utilisateur.id, "contenu": payload.contenu.strip()}
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (annonce établissement {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    journaliser(
        action="annonce.envoyee",
        user_id=utilisateur.id,
        cible_type="annonce_etablissement",
        cible_id=None,
        details={"longueur_contenu": len(payload.contenu.strip())},
        request=request,
    )
    return {"envoye": True}


class ResultatDiffusion(BaseModel):
    diffuse_a: int
    total_cibles: int
    echecs: List[str]


@router.post("/documents/diffuser", response_model=ResultatDiffusion, status_code=201)
async def diffuser_document(
    request: Request,
    fichier: UploadFile = File(...),
    titre: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    cible: Optional[str] = Form("tous"),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Ajoutée le 2026-08-05 (demande Bourama, partie D du reste-à-faire de
    la hiérarchie de rôles) : un établissement ajoute UN document en une
    fois à la bibliothèque de TOUS ses enseignants + tous les étudiants de
    ces enseignants -- même portée que peut_gerer_base_connaissances pour
    un établissement (deux niveaux, voir permissions_hierarchie.py).
    Réutilise `_contacts_autorises` (déjà écrite pour l'outil IA
    envoyer_message) pour la liste des cibles : même périmètre, pas de
    logique de rattachement dupliquée.

    Ouverte le 2026-08-06 (demande Bourama) à l'enseignant également :
    même endpoint, `_contacts_autorises` limite déjà naturellement ses
    cibles à ses seuls étudiants (un niveau, pas l'établissement), donc
    aucune branche supplémentaire nécessaire ici -- juste élargir le
    rôle autorisé en entrée.

    `cible` ajouté le 2026-08-06 (demande Bourama) : un établissement
    peut choisir "tous" (défaut, comportement inchangé), "enseignant"
    (ses enseignants seulement) ou "etudiant" (tous les étudiants de ses
    enseignants seulement, en sautant le niveau enseignant). Sans effet
    réel pour un enseignant (ses cibles ne contiennent déjà qu'"etudiant"),
    donc pas besoin de le masquer/valider différemment selon le rôle.

    Réutilise telle quelle la logique de POST /api/agents/{agent_id}/bibliotheque
    (api/agents.py) -- storage + indexation RAG si PDF, ligne
    bibliotheque_fichiers -- répétée pour chaque agent cible au lieu d'un
    seul. Best-effort : un échec sur une cible (pas encore d'IA créée,
    erreur Supabase ponctuelle...) n'interrompt pas la diffusion aux
    autres, chaque échec est juste listé en retour.

    Contrôle 403 sur profil.role retiré le 07/08 (demande Bourama) :
    le bouton "Envoyer à..." s'affiche désormais sans condition de rôle
    côté frontend (chat de stirux/lirinus, voir SidebarChat.tsx), donc
    le bloquer encore ici renverrait une erreur au premier essai. ATTENTION :
    ça ne rend pas la diffusion "fonctionnelle" pour autant pour un compte
    sans rattachement réel (profiles.role/etablissement_id/enseignant_id) --
    _contacts_autorises ci-dessous reste basée sur ces colonnes (jamais
    renseignées depuis que l'inscription par rôle est désactivée), donc
    cibles restera vide (diffuse_a=0/total_cibles=0) pour un tel compte,
    sans erreur mais sans effet réel.
    """
    profil = _lire_profil_role(utilisateur.id) or {"role": None, "user_id": utilisateur.id}
    profil["user_id"] = utilisateur.id

    if not (titre or "").strip() and not (description or "").strip():
        raise erreur_api(400, "DONNE_AU_MOINS_UNE_DESCRIPTION_OU")
    if fichier.content_type not in TYPES_BIBLIOTHEQUE_AUTORISES:
        raise erreur_api(400, "TYPE_DE_FICHIER_NON_SUPPORTE")

    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_BIBLIOTHEQUE_OCTETS:
        raise erreur_api(400, "FICHIER_TROP_LOURD_50_MO_MAX")

    nom_original = fichier.filename or "fichier"
    description_finale = (
        f"{titre.strip()} — {description.strip()}"
        if (titre or "").strip() and (description or "").strip()
        else (description or titre or "").strip()
    )

    cibles = [c for c in _contacts_autorises(profil) if c.get("role") in ("enseignant", "etudiant")]

    if cible and cible != "tous":
        if cible not in ("enseignant", "etudiant"):
            raise erreur_api(400, "CIBLE_INVALIDE")
        cibles = [c for c in cibles if c["role"] == cible]

    # Depuis le 06/08, un rôle donné = UNE IA fixe partagée par tout le
    # monde (AGENT_PAR_ROLE) -- diffuser une fois par personne comme avant
    # uploaderait/indexerait le même fichier plusieurs fois dans la même
    # IA. On diffuse une seule fois par rôle réellement présent parmi les
    # cibles, et on compte quand même `diffuse_a`/`echecs` par personne
    # couverte (même sémantique de retour qu'avant pour le frontend/les
    # logs, juste le travail réel fait une fois par IA).
    roles_cibles = sorted({c["role"] for c in cibles})

    echecs: List[str] = []
    diffuse_a = 0

    for role in roles_cibles:
        agent_id = AGENT_PAR_ROLE[role]
        cibles_du_role = [c for c in cibles if c["role"] == role]
        try:
            if fichier.content_type == "application/pdf":
                nom_stockage_rag = f"{agent_id}__{nom_original}"
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(contenu)
                    chemin_temp = tmp.name
                try:
                    upload_document(chemin_temp, nom_stockage_rag)
                    indexer_document(chemin_temp, nom_stockage_rag, agent_id)
                finally:
                    try:
                        os.remove(chemin_temp)
                    except OSError:
                        pass

            enregistrer_fichier(
                contenu=contenu,
                nom_fichier=nom_original,
                type_mime=fichier.content_type,
                niveau="agent",
                uploade_par=utilisateur.id,
                agent_id=agent_id,
                description=description_finale,
            )
            diffuse_a += len(cibles_du_role)
        except Exception as e:
            logging.error(f"ERREUR diffusion document (agent_id={agent_id}, role={role}) : {e}")
            echecs.extend(c.get("nom_affiche") or "Sans nom" for c in cibles_du_role)

    journaliser(
        action="document.diffuse",
        user_id=utilisateur.id,
        cible_type="profile",
        cible_id=None,
        details={"nom_original": nom_original, "diffuse_a": diffuse_a, "total_cibles": len(cibles)},
        request=request,
    )

    return ResultatDiffusion(diffuse_a=diffuse_a, total_cibles=len(cibles), echecs=echecs)


class DiffuserLienPayload(BaseModel):
    url: str
    titre: Optional[str] = None
    description: Optional[str] = None
    cible: Optional[str] = "tous"


@router.post("/liens/diffuser", response_model=ResultatDiffusion, status_code=201)
def diffuser_lien(
    payload: DiffuserLienPayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Pendant de diffuser_document ci-dessus pour un lien de bibliothèque
    (pas de fichier, juste une URL) -- ajoutée le 2026-08-06 en même
    temps que l'ouverture du droit de diffusion à l'enseignant, demande
    Bourama. Même portée par rôle réel que diffuser_document :
    établissement -> ses enseignants + leurs étudiants (deux niveaux),
    enseignant -> ses étudiants (un niveau). Réutilise enregistrer_lien
    (core/bibliotheque_fichiers.py), un seul enregistrement par rôle
    cible réellement présent (pas par personne, mêmes IA fixes
    partagées que diffuser_document). `cible` : voir diffuser_document
    ci-dessus (même sémantique "tous"/"enseignant"/"etudiant").

    Contrôle 403 sur profil.role retiré le 07/08 (demande Bourama) --
    même motif et même limite que diffuser_document ci-dessus (voir sa
    docstring) : cibles restera vide pour un compte sans rattachement réel.
    """
    profil = _lire_profil_role(utilisateur.id) or {"role": None, "user_id": utilisateur.id}
    profil["user_id"] = utilisateur.id

    if not (payload.titre or "").strip() and not (payload.description or "").strip():
        raise erreur_api(400, "DONNE_AU_MOINS_UNE_DESCRIPTION_OU")
    if not (payload.url or "").strip():
        raise erreur_api(400, "URL_MANQUANTE")

    description_finale = (
        f"{payload.titre.strip()} — {payload.description.strip()}"
        if (payload.titre or "").strip() and (payload.description or "").strip()
        else (payload.description or payload.titre or "").strip()
    )

    cibles = [c for c in _contacts_autorises(profil) if c.get("role") in ("enseignant", "etudiant")]

    if payload.cible and payload.cible != "tous":
        if payload.cible not in ("enseignant", "etudiant"):
            raise erreur_api(400, "CIBLE_INVALIDE")
        cibles = [c for c in cibles if c["role"] == payload.cible]

    roles_cibles = sorted({c["role"] for c in cibles})

    echecs: List[str] = []
    diffuse_a = 0

    for role in roles_cibles:
        agent_id = AGENT_PAR_ROLE[role]
        cibles_du_role = [c for c in cibles if c["role"] == role]
        try:
            enregistrer_lien(
                url=payload.url.strip(),
                nom_fichier=(payload.titre or payload.url).strip(),
                niveau="agent",
                uploade_par=utilisateur.id,
                agent_id=agent_id,
                description=description_finale,
            )
            diffuse_a += len(cibles_du_role)
        except Exception as e:
            logging.error(f"ERREUR diffusion lien (agent_id={agent_id}, role={role}) : {e}")
            echecs.extend(c.get("nom_affiche") or "Sans nom" for c in cibles_du_role)

    journaliser(
        action="lien.diffuse",
        user_id=utilisateur.id,
        cible_type="profile",
        cible_id=None,
        details={"url": payload.url.strip(), "diffuse_a": diffuse_a, "total_cibles": len(cibles)},
        request=request,
    )

    return ResultatDiffusion(diffuse_a=diffuse_a, total_cibles=len(cibles), echecs=echecs)


class ElementDiffuse(BaseModel):
    id: str
    nom_fichier: str
    description: Optional[str] = None
    type_mime: str
    role_cible: str
    created_at: str


@router.get("/diffusions", response_model=List[ElementDiffuse])
def lister_mes_diffusions(utilisateur=Depends(utilisateur_courant)):
    """
    Ajoutée le 2026-08-06 (demande Bourama) : liste ce que LA PERSONNE
    CONNECTÉE a déjà ajouté via diffuser_document/diffuser_lien, pour
    éviter les doublons (renvoyer deux fois le même PDF sans le savoir)
    -- rien de tel n'existait, la section "Envoyer à..." était à sens
    unique côté frontend. Filtre sur `uploade_par` (pas sur
    `peut_gerer_base_connaissances`, qui se base sur owner_id -- nitrux/
    stirux/lirinus n'ont pas de owner_id correspondant à qui que ce
    soit dans la hiérarchie, voir permissions_hierarchie.py) : chacun ne
    voit que ce qu'il a lui-même ajouté, jamais ce qu'un autre
    enseignant/établissement a diffusé.

    Contrôle 403 sur profil.role retiré le 07/08 (demande Bourama), même
    motif que diffuser_document/diffuser_lien ci-dessus : sans rôle réel,
    `agents_possibles` ci-dessous reste construit sur la base d'un rôle
    `None` -- non couvert par le if/elif -- donc reste vide et cette
    route renvoie simplement une liste vide plutôt qu'une erreur.
    """
    profil = _lire_profil_role(utilisateur.id) or {"role": None}

    agents_possibles = (
        [AGENT_PAR_ROLE["enseignant"], AGENT_PAR_ROLE["etudiant"]]
        if profil.get("role") == "etablissement"
        else [AGENT_PAR_ROLE["etudiant"]]
        if profil.get("role") == "enseignant"
        else []
    )
    role_par_agent = {v: k for k, v in AGENT_PAR_ROLE.items()}

    try:
        res = (
            supabase.table("fichiers_uploades")
            .select("id, nom_fichier, description, type_mime, agent_id, created_at")
            .eq("niveau", "agent")
            .eq("uploade_par", utilisateur.id)
            .in_("agent_id", agents_possibles)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture diffusions de {utilisateur.id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_LISTER_TES_DIFFUSIONS")

    return [
        ElementDiffuse(
            id=ligne["id"],
            nom_fichier=ligne["nom_fichier"],
            description=ligne.get("description"),
            type_mime=ligne["type_mime"],
            role_cible=role_par_agent.get(ligne["agent_id"], ligne["agent_id"]),
            created_at=ligne["created_at"],
        )
        for ligne in (res.data or [])
    ]
