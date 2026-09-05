"""
Ajoute le 23/08/2026, Bourama : Lot 1 Partie 3 (app mobile), socle.
Etendu le 23/08/2026, Lot 3 (notifications & rappels) : enregistrement
du token push natif (FCM/APNs), voir core/notifications_push.py pour
la logique d'envoi (etendue, pas dupliquee -- meme systeme que les
rappels navigateur existants).
Etendu le 23/08/2026, Lot 5 (connecteurs tiers) : connecteurs Notion.
Etendu le 24/08/2026, Lot 1A (brancher le cerveau) : canal de decision
generique -- l'app recoit une action via push (voir
core/notifications_push.envoyer_action_appareil), vient la chercher ici,
puis rapporte le resultat. Voir core/actions_appareil_mobile.py pour le
detail.
Etendu le 26/08/2026 : branchement reel du canal a l'agent (outil MCP
executer_action_mobile, voir core/serveur_mcp_generation.py), types
"dossier_*" (miroir des dossiers designes, voir
core/dossiers_designes_mobile.py) et "accessibilite_*" (flavor Android
externe uniquement).

Canal dedie entre l'app mobile Clovis (Android/iOS, depot
clovis-mobile) et ce backend. Reutilise l'auth Supabase standard deja
en place (voir api/auth.py) : l'app mobile se connecte directement a
Supabase avec le SDK natif, puis envoie son access_token en Bearer sur
ces routes, exactement comme le fait clovis-frontend.

Capacites couvertes ici : usage (Lot 1), miroir des dossiers designes
(Lot 2, 26/08), token push natif (Lot 3), connecteurs tiers, Notion en
premier (Lot 5), canal de decision generique (Lot 1A). Reste a faire :
controles de session (Lot 4).
"""

import logging
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from core.usage_appareil_mobile import enregistrer_usage, lire_usage
from core.notifications_push import enregistrer_token_natif, supprimer_token_natif
from core.actions_appareil_mobile import lire_action, lire_actions_en_attente, marquer_resultat
from core.dossiers_designes_mobile import synchroniser_dossiers_designes
from connexions.notion import (
    demarrer_connexion_notion,
    finaliser_connexion_notion,
    obtenir_token_valide as obtenir_token_notion,
    est_connecte as notion_est_connecte,
    REDIRECT_URI_MOBILE,
)

router = APIRouter(prefix="/api/appareils-mobiles", tags=["appareils-mobiles"])


class EntreeUsage(BaseModel):
    nom_app: str
    date: str  # "AAAA-MM-JJ"
    duree_secondes: int


class SynchronisationUsage(BaseModel):
    plateforme: str  # "android" ou "ios"
    entrees: list[EntreeUsage]


@router.post("/usage", status_code=204)
def synchroniser_usage(payload: SynchronisationUsage, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : appeler a chaque ouverture de l'ecran usage (ou
    en tache de fond periodique plus tard), avec le total du jour par
    app tel que calcule cote telephone. Ecrase la valeur precedente
    pour chaque (app, jour) concerne.
    """
    if payload.plateforme not in ("android", "ios"):
        raise erreur_api(400, "PLATEFORME_INCONNUE")

    try:
        enregistrer_usage(
            utilisateur.id,
            payload.plateforme,
            [entree.model_dump() for entree in payload.entrees],
        )
    except Exception as e:
        logging.error(f"ERREUR synchronisation usage mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_SYNCHRONISATION_USAGE")


@router.get("/usage")
def obtenir_usage(jours: int = 7, utilisateur=Depends(utilisateur_courant)):
    """
    Renvoie l'usage des `jours` derniers jours (7 par defaut) pour
    l'ecran minimal du Lot 1. `jours` reste un parametre, jamais une
    valeur figee cote frontend mobile.
    """
    if jours < 1 or jours > 90:
        raise erreur_api(400, "PLAGE_JOURS_INVALIDE")

    aujourdhui = date.today()
    depuis = (aujourdhui - timedelta(days=jours - 1)).isoformat()
    jusqua = aujourdhui.isoformat()

    try:
        lignes = lire_usage(utilisateur.id, depuis, jusqua)
    except Exception as e:
        logging.error(f"ERREUR lecture usage mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_LECTURE_USAGE")

    return {"usage": lignes}


# --- Lot 2 (suite, 26/08/2026) : dossiers designes, miroir pour l'agent ---
#
# CONTRAT APP MOBILE : appeler apres CHAQUE changement (ajout/retrait d'un
# dossier designe via Dossiers.choisirDossier/retirerDossierDesigne) ET une
# fois a l'ouverture de l'app, avec la liste COMPLETE des noms actuels.
# Mode miroir, pas un ajout : chaque appel remplace l'etat cote backend
# (voir core/dossiers_designes_mobile.py). `noms` peut etre vide.


class SynchronisationDossiers(BaseModel):
    plateforme: str  # "android" ou "ios"
    appareil_id: str  # ajoute le 04/09/2026 : UUID genere/persiste par l'app, voir IdentifiantAppareil.kt/.swift
    appareil_nom: str | None = None  # libelle lisible (choisi par l'etudiant ou genere par defaut cote app)
    noms: list[str]


@router.post("/dossiers", status_code=204)
def synchroniser_dossiers(payload: SynchronisationDossiers, utilisateur=Depends(utilisateur_courant)):
    if payload.plateforme not in ("android", "ios"):
        raise erreur_api(400, "PLATEFORME_INCONNUE")
    if not payload.appareil_id.strip():
        raise erreur_api(400, "APPAREIL_ID_MANQUANT")

    try:
        synchroniser_dossiers_designes(
            utilisateur.id, payload.plateforme, payload.appareil_id, payload.appareil_nom, payload.noms
        )
    except Exception as e:
        logging.error(f"ERREUR synchronisation dossiers designes (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_SYNCHRONISATION_DOSSIERS")


class TokenPush(BaseModel):
    plateforme: str  # "android" ou "ios"
    token: str
    appareil_id: str | None = None  # ajoute le 04/09/2026, voir migrations/2026_09_04_appareil_id_ciblage.sql


@router.post("/push-token", status_code=204)
def enregistrer_push_token(payload: TokenPush, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : appeler a chaque obtention/renouvellement du
    token FCM (Android, onNewToken) ou APNs (iOS,
    didRegisterForRemoteNotificationsWithDeviceToken) -- pas seulement
    au premier lancement, le SDK peut renouveler ce token a tout moment.
    `appareil_id` (depuis le 04/09/2026) permet de livrer une action a
    UN SEUL appareil precis (voir core/notifications_push.py) ; laisse
    vide, le token reste utilisable mais uniquement pour une diffusion
    large (comportement d'avant cette date).
    """
    if payload.plateforme not in ("android", "ios"):
        raise erreur_api(400, "PLATEFORME_INCONNUE")
    if not payload.token.strip():
        raise erreur_api(400, "TOKEN_VIDE")

    try:
        enregistrer_token_natif(utilisateur.id, payload.plateforme, payload.token, payload.appareil_id)
    except Exception as e:
        logging.error(f"ERREUR enregistrement token push mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_ENREGISTREMENT_TOKEN")


@router.delete("/push-token", status_code=204)
def desinscrire_push_token(token: str, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : appeler a la deconnexion (l'utilisateur se
    deconnecte de son compte Clovis sur ce telephone) pour ne plus
    recevoir de rappels sur cet appareil.
    """
    try:
        supprimer_token_natif(utilisateur.id, token)
    except Exception as e:
        logging.error(f"ERREUR desinscription token push mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "ECHEC_DESINSCRIPTION_TOKEN")


# --- Lot 1A : canal de decision generique (brancher le cerveau) ---
#
# Aucune route ici ne DECIDE une action -- ca reste a construire cote agent
# (voir core/actions_appareil_mobile.py, TODO note en tete de ce module).
# Ces routes exposent seulement le canal generique de lecture/rapport.


@router.get("/actions/en-attente")
def obtenir_actions_en_attente(appareil_id: str = "", utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : filet de secours a appeler a chaque ouverture de
    l'app, pour rattraper les actions decidees par Clovis pendant qu'elle
    etait fermee/hors ligne (le push peut ne pas etre arrive). Passer
    `appareil_id` (depuis le 04/09/2026) : une action ciblant un AUTRE
    appareil du meme compte (dossier possede par un autre telephone)
    n'est jamais renvoyee ici, pour ne pas qu'un appareil qui n'a pas le
    bon dossier la marque "echouee" a la place du bon appareil.
    """
    return {"actions": lire_actions_en_attente(utilisateur.id, appareil_id)}


@router.get("/actions/{action_id}")
def obtenir_action(action_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    CONTRAT APP MOBILE : appeler des reception du push type="action"
    (action_id fourni dans le payload) pour recuperer type_action et
    parametres complets.
    """
    action = lire_action(action_id, utilisateur.id)
    if action is None:
        raise erreur_api(404, "ACTION_INTROUVABLE")
    return action


class ResultatAction(BaseModel):
    succes: bool
    resultat: str = ""


@router.post("/actions/{action_id}/resultat", status_code=204)
def rapporter_resultat_action(
    action_id: str, payload: ResultatAction, utilisateur=Depends(utilisateur_courant)
):
    """
    CONTRAT APP MOBILE : appeler systematiquement apres avoir tente
    d'executer une action recue (succes OU echec, y compris "type_action
    non reconnu") pour que l'agent puisse relayer le resultat reel a
    l'etudiant dans la conversation plutot que de rester silencieux.
    """
    try:
        marquer_resultat(action_id, utilisateur.id, payload.succes, payload.resultat)
    except Exception as e:
        logging.error(f"ERREUR rapport resultat action (id={action_id}) : {e}")
        raise erreur_api(500, "ECHEC_RAPPORT_RESULTAT")


# --- Lot 5 : connecteurs tiers (Notion en premier) ---
#
# CONTRAT APP MOBILE (voir 05-connecteurs-tiers.md) : l'app n'appelle jamais
# Notion directement, elle passe par ce routeur -- clovis-backend pilote
# l'OAuth et stocke les tokens (connexions_notion), reutilisant exactement
# la meme infra que celle deja utilisee par le chat (core/registre_outils.py).
#
# Flow cote app :
# 1. POST .../connecteurs/notion/demarrer -> ouvrir l'url_autorisation
#    renvoyee dans ASWebAuthenticationSession (iOS, callbackURLScheme=
#    "clovismobile") ou Custom Tabs + intent-filter (Android).
# 2. Recuperer `code` et `state` depuis l'URI de redirection interceptee
#    (clovismobile://oauth-callback?code=...&state=...).
# 3. POST .../connecteurs/notion/finaliser avec {code, state}.
# 4. GET .../connecteurs/notion/statut pour verifier l'etat a tout moment.
# 5. GET .../connecteurs/notion/rechercher?q=... pour un appel effectif de
#    bout en bout (critere de fin du Lot 5) -- utilise directement l'API
#    REST Notion (POST https://api.notion.com/v1/search), pas le MCP (le
#    MCP Notion est concu pour l'agent de chat, pas pour un appel simple
#    depuis le mobile ; l'API REST suffit et evite de reimplementer un
#    client MCP complet cote backend pour ce lot).

NOTION_API_VERSION = "2022-06-28"


class FinalisationNotion(BaseModel):
    code: str
    state: str


@router.post("/connecteurs/notion/demarrer")
def demarrer_notion(utilisateur=Depends(utilisateur_courant)):
    url = demarrer_connexion_notion(utilisateur.id, agent_id=None, redirect_uri=REDIRECT_URI_MOBILE)
    if not url:
        raise erreur_api(500, "NOTION_URL_AUTORISATION_INDISPONIBLE")
    return {"url_autorisation": url}


@router.post("/connecteurs/notion/finaliser")
def finaliser_notion(payload: FinalisationNotion, utilisateur=Depends(utilisateur_courant)):
    succes, message = finaliser_connexion_notion(payload.code, payload.state)
    if not succes:
        raise erreur_api(400, "NOTION_CONNEXION_ECHEC")
    return {"connecte": True, "espace": message}


@router.get("/connecteurs/notion/statut")
def statut_notion(utilisateur=Depends(utilisateur_courant)):
    return {"connecte": notion_est_connecte(utilisateur.id)}


# Ajoute le 30/08/2026, Bourama : la reponse brute de l'API Notion ne donne
# jamais le titre directement au meme endroit -- pour une "database", il est
# dans le champ "title" a la racine ; pour une "page", il faut trouver, parmi
# ses "properties", celle dont le type vaut "title" (son nom varie, ce n'est
# pas toujours "Name"), chacune etant une liste de blocs de texte enrichi a
# concatener. Sans ca l'etudiant ne voyait qu'un identifiant technique brut.
def _titre_resultat_notion(resultat: dict) -> str | None:
    if resultat.get("object") == "database":
        blocs = resultat.get("title", [])
    else:
        blocs = []
        for propriete in (resultat.get("properties") or {}).values():
            if propriete.get("type") == "title":
                blocs = propriete.get("title", [])
                break
    texte = "".join(bloc.get("plain_text", "") for bloc in blocs).strip()
    return texte or None


@router.get("/connecteurs/notion/rechercher")
def rechercher_notion(q: str = "", utilisateur=Depends(utilisateur_courant)):
    """
    Preuve de bout en bout du Lot 5 (critere de fin) : recherche dans
    l'espace Notion connecte de l'utilisateur. `q` vide renvoie les
    elements les plus recents (comportement standard de l'API Notion).
    """
    token = obtenir_token_notion(utilisateur.id)
    if not token:
        raise erreur_api(400, "NOTION_NON_CONNECTE")

    try:
        reponse = httpx.post(
            "https://api.notion.com/v1/search",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_API_VERSION,
                "Content-Type": "application/json",
            },
            json={"query": q, "page_size": 20},
            timeout=10,
        )
        reponse.raise_for_status()
    except Exception as e:
        logging.error(f"ERREUR recherche Notion mobile (utilisateur {utilisateur.id}) : {e}")
        raise erreur_api(500, "NOTION_RECHERCHE_ECHEC")

    resultats = reponse.json().get("results", [])
    return {
        "resultats": [
            {
                "id": r.get("id"),
                "type": r.get("object"),
                "url": r.get("url"),
                "titre": _titre_resultat_notion(r),
            }
            for r in resultats
        ]
    }
