"""
Ajoute le 30/08/2026, Bourama : Lot 1 Partie 3 (app mobile), chantier
"Exploration de dossier en temps reel" (voir 00-commun-exploration-dossier.md
et 01-canal-temps-reel.md a la racine du depot).

Route WebSocket separee de api/appareils_mobiles.py, qui reste dedie au
systeme d'actions fire-and-forget existant (voir docstring de ce
fichier). Ce lot ne contient AUCUNE logique de dossier -- uniquement le
canal lui-meme, plus une route de test pour valider l'aller-retour de
bout en bout (critere de fin du Lot 1). La vraie logique d'exploration
(lots 2 a 5) branchera sur core/canal_temps_reel.poser_question_appareil
via un outil agent dedie.
"""

import logging

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from api.auth import supabase, utilisateur_courant
from core.canal_temps_reel import (
    connecter,
    deconnecter,
    poser_question_appareil,
    recevoir_reponse,
)

router = APIRouter(prefix="/api/canal-temps-reel", tags=["canal-temps-reel"])


def _verifier_token(token: str):
    """
    Equivalent de api.auth.utilisateur_courant, adapte au WebSocket : le
    token Supabase arrive en parametre de l'URL de connexion (?token=...)
    plutot qu'en en-tete Authorization -- un client WebSocket JS ne peut
    pas fixer d'en-tete personnalise a la connexion (voir
    01-canal-temps-reel.md). Renvoie None (plutot que de lever) si le
    token est absent/invalide/expire : la route appelante ferme alors
    proprement la connexion.
    """
    if not token:
        return None
    try:
        reponse = supabase.auth.get_user(token)
    except Exception as e:
        logging.error(f"ERREUR verification token canal temps reel : {e}")
        return None
    if not reponse or not reponse.user:
        return None
    return reponse.user


@router.websocket("/ws")
async def canal_temps_reel(
    websocket: WebSocket, token: str = Query(default=""), appareil_id: str = Query(default="")
):
    """
    CONTRAT APP MOBILE : ouvrir cette connexion des que l'app est au
    premier plan (et la reouvrir a chaque reprise), tant qu'un compte est
    connecte. Chaque question recue a la forme {"id": ..., "question":
    ...} ; la reponse doit etre renvoyee avec le meme "id" : {"id": ...,
    "reponse": ...}. Pour ce lot, aucun traitement reel n'est attendu :
    repondre "oui" a n'importe quelle question suffit pour valider le
    tuyau (voir 01-canal-temps-reel.md).

    `appareil_id` (ajoute le 04/09/2026, voir
    core/canal_temps_reel.py, commentaire au-dessus de _connexions) :
    identifie CET appareil precis (ou "" pour une session web, voir
    urlWebSocket cote clovis-frontend) -- sans ca, un deuxieme appareil
    du meme compte qui se connecte remplacerait purement et simplement
    le premier dans la table des connexions actives.
    """
    utilisateur = _verifier_token(token)
    if utilisateur is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    await connecter(utilisateur.id, appareil_id, websocket)

    try:
        while True:
            message = await websocket.receive_json()
            correlation_id = message.get("id")
            reponse = message.get("reponse")
            if correlation_id is not None and reponse is not None:
                recevoir_reponse(correlation_id, reponse)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"ERREUR canal temps reel (user={utilisateur.id}, appareil={appareil_id}) : {e}")
    finally:
        await deconnecter(utilisateur.id, appareil_id, websocket)


@router.post("/test")
async def tester_canal(appareil_id: str = "", utilisateur=Depends(utilisateur_courant)):
    """
    Route de test (critere de fin du Lot 1) : envoie une question de
    test ("es-tu la ?") au telephone `appareil_id` de l'utilisateur
    connecte et attend la reponse en direct via le WebSocket ci-dessus.
    `connecte: false` signifie que cet appareil precis n'a pas de
    connexion active (renvoi immediat, sans attendre le timeout de 30s)
    -- a distinguer d'une reponse recue apres attente.
    """
    reponse = await poser_question_appareil(utilisateur.id, appareil_id, "es-tu là ?")
    if reponse is None:
        return {"connecte": False, "reponse": None}
    return {"connecte": True, "reponse": reponse}
