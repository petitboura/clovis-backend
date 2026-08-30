"""
Cree le 30/08/2026, Bourama : Lot 1 Partie 3 (app mobile), chantier
"Exploration de dossier en temps reel" (voir 00-commun-exploration-dossier.md
et 01-canal-temps-reel.md a la racine du depot pour le contexte complet).

Ce module ne contient AUCUNE logique de dossier -- uniquement le canal
temps reel lui-meme : gestion de la connexion WebSocket active par
utilisateur, correlation question/reponse, et la fonction centrale
poser_question_appareil utilisee par les futurs outils agent (Lot 2 et
suivants) pour poser une question au telephone et recevoir la reponse
dans le meme tour de raisonnement.

A ne pas confondre avec core/actions_appareil_mobile.py : ce dernier
reste le systeme "envoie et oublie" (l'agent decide une action, le
resultat revient plus tard, hors de la conversation en cours). Ici,
c'est synchrone et en direct -- l'app doit etre ouverte au moment de la
question, sinon poser_question_appareil renvoie None immediatement (voir
docstring plus bas).
"""

import asyncio
import logging
import uuid
from typing import Any

from fastapi import WebSocket

# Une seule connexion active par utilisateur (user_id) a la fois : une
# nouvelle connexion (ex: reconnexion apres coupure reseau) remplace
# toujours l'ancienne (voir 01-canal-temps-reel.md).
_connexions: dict[str, WebSocket] = {}
_verrou_connexions = asyncio.Lock()

# Requetes en attente de reponse, indexees par identifiant de correlation
# -- permet de ne jamais melanger deux echanges si plusieurs surviennent
# a la suite pour le meme utilisateur.
_attentes: dict[str, "asyncio.Future[Any]"] = {}

# Timeouts decides avec Bourama le 29/08/2026 (voir 01-canal-temps-reel.md) :
# 30 secondes au total avant abandon, avec deux points d'etape
# intermediaires a 5s et 15s pour ne jamais laisser l'etudiant sans
# nouvelle pendant que Clovis attend.
DELAI_STATUT_1_SECONDES = 5
DELAI_STATUT_2_SECONDES = 15
DELAI_ABANDON_SECONDES = 30

TEXTE_STATUT_1 = "Clovis regarde toujours..."
TEXTE_STATUT_2 = "Ça prend un peu plus de temps que prévu..."


async def connecter(user_id: str, websocket: WebSocket) -> None:
    """
    Enregistre `websocket` comme connexion active pour cet utilisateur.
    Si une connexion existait deja (reconnexion), elle est fermee puis
    remplacee -- jamais deux connexions actives en meme temps pour un
    meme utilisateur.
    """
    async with _verrou_connexions:
        ancienne = _connexions.get(user_id)
        _connexions[user_id] = websocket

    if ancienne is not None and ancienne is not websocket:
        try:
            await ancienne.close()
        except Exception:
            # Deja fermee cote client (cas le plus frequent d'une
            # reconnexion) -- sans consequence.
            pass


async def deconnecter(user_id: str, websocket: WebSocket) -> None:
    """
    Retire `websocket` de la table des connexions actives, seulement si
    c'est toujours la connexion active pour cet utilisateur (une
    reconnexion a pu deja la remplacer entre-temps -- dans ce cas, ne
    surtout pas supprimer la nouvelle connexion par erreur).
    """
    async with _verrou_connexions:
        if _connexions.get(user_id) is websocket:
            del _connexions[user_id]


def recevoir_reponse(correlation_id: str, reponse: Any) -> None:
    """
    Appelee par la route WebSocket a chaque message recu du telephone.
    Resout la Future en attente correspondante si elle existe encore
    (une reponse qui arrive apres l'abandon cote serveur est simplement
    ignoree -- la Future n'existe plus).
    """
    future = _attentes.get(correlation_id)
    if future is not None and not future.done():
        future.set_result(reponse)


async def _appeler_statut(on_statut, texte: str) -> None:
    if on_statut is None:
        return
    try:
        resultat = on_statut(texte)
        if asyncio.iscoroutine(resultat):
            await resultat
    except Exception as e:
        logging.error(f"ERREUR callback statut canal temps reel : {e}")


async def poser_question_appareil(user_id: str, contenu: Any, on_statut=None) -> Any | None:
    """
    Fonction centrale du canal temps reel (voir 01-canal-temps-reel.md).

    Pose `contenu` comme question au telephone de `user_id` et attend la
    reponse. `contenu` est un texte simple pour un test basique (Lot 1),
    ou un objet JSON structure (ex: {"action": "lister_contenu",
    "dossier_nom": ...}) pour les capacites d'exploration reelles (Lot 2
    et suivants, voir core/exploration_dossier_mobile.py) -- transmis tel
    quel au telephone via le champ "question" du message WebSocket.
    Renvoie :
    - None IMMEDIATEMENT si aucune connexion active pour cet utilisateur
      (app fermee) -- jamais d'attente de 30 secondes inutile dans ce cas ;
    - la reponse du telephone des qu'elle arrive (meme forme que ce que
      l'app a mis dans le champ "reponse" -- texte ou objet JSON) ;
    - None apres 30 secondes si la connexion existait mais n'a jamais
      repondu (coupure reseau en cours de route, app fermee entre-temps,
      etc).

    `on_statut` (optionnel) : callback (sync ou coroutine) appele avec un
    texte francais a 5s puis 15s d'attente sans reponse, pour relayer un
    message intermediaire a l'etudiant. Concu pour etre branche sur le
    meme mecanisme "statut"/"statut_termine" deja utilise pendant le
    streaming du chat pour les outils MCP (voir core/main.py) -- la
    connexion effective a ce mecanisme se fera au Lot 2, quand l'outil
    agent existera vraiment.
    """
    async with _verrou_connexions:
        websocket = _connexions.get(user_id)

    if websocket is None:
        return None

    correlation_id = str(uuid.uuid4())
    future: "asyncio.Future[Any]" = asyncio.get_event_loop().create_future()
    _attentes[correlation_id] = future

    try:
        try:
            await websocket.send_json({"id": correlation_id, "question": contenu})
        except Exception as e:
            logging.error(f"ERREUR envoi question canal temps reel (user={user_id}) : {e}")
            return None

        try:
            return await asyncio.wait_for(future, timeout=DELAI_STATUT_1_SECONDES)
        except asyncio.TimeoutError:
            pass

        await _appeler_statut(on_statut, TEXTE_STATUT_1)
        try:
            return await asyncio.wait_for(
                future, timeout=DELAI_STATUT_2_SECONDES - DELAI_STATUT_1_SECONDES
            )
        except asyncio.TimeoutError:
            pass

        await _appeler_statut(on_statut, TEXTE_STATUT_2)
        try:
            return await asyncio.wait_for(
                future, timeout=DELAI_ABANDON_SECONDES - DELAI_STATUT_2_SECONDES
            )
        except asyncio.TimeoutError:
            logging.warning(
                f"ABANDON canal temps reel (user={user_id}, id={correlation_id}) : "
                f"pas de reponse apres {DELAI_ABANDON_SECONDES}s"
            )
            return None
    finally:
        _attentes.pop(correlation_id, None)
