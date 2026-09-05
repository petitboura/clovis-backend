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

# Une seule connexion active par (utilisateur, appareil) a la fois : une
# nouvelle connexion du MEME appareil (ex: reconnexion apres coupure
# reseau) remplace toujours l'ancienne (voir 01-canal-temps-reel.md).
#
# Modifie le 04/09/2026, Bourama : cle composite (user_id, appareil_id)
# plutot que user_id seul -- avant cette date, un etudiant connecte sur
# deux telephones (ou telephone + onglet web, le meme canal servant les
# deux, voir lib/canalTempsReel.ts) voyait sa DEUXIEME connexion
# remplacer purement et simplement la premiere, rendant le premier
# appareil injoignable pour l'exploration en direct sans aucun signe
# visible de l'erreur. `appareil_id` vide ("") est une cle valide comme
# une autre : c'est celle utilisee par une session web (voir
# urlWebSocket cote clovis-frontend), naturellement distincte de tout
# vrai telephone.
_connexions: dict[tuple[str, str], WebSocket] = {}
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


async def connecter(user_id: str, appareil_id: str, websocket: WebSocket) -> None:
    """
    Enregistre `websocket` comme connexion active pour CET appareil de
    cet utilisateur. Si une connexion existait deja pour le MEME
    (user_id, appareil_id) (reconnexion), elle est fermee puis remplacee
    -- mais une connexion d'un AUTRE appareil du meme utilisateur n'est
    jamais touchee (voir commentaire au-dessus de _connexions).
    """
    cle = (user_id, appareil_id)
    async with _verrou_connexions:
        ancienne = _connexions.get(cle)
        _connexions[cle] = websocket

    if ancienne is not None and ancienne is not websocket:
        try:
            await ancienne.close()
        except Exception:
            # Deja fermee cote client (cas le plus frequent d'une
            # reconnexion) -- sans consequence.
            pass


async def deconnecter(user_id: str, appareil_id: str, websocket: WebSocket) -> None:
    """
    Retire `websocket` de la table des connexions actives, seulement si
    c'est toujours la connexion active pour ce (user_id, appareil_id)
    (une reconnexion du MEME appareil a pu deja la remplacer entre-temps
    -- dans ce cas, ne surtout pas supprimer la nouvelle connexion par
    erreur).
    """
    cle = (user_id, appareil_id)
    async with _verrou_connexions:
        if _connexions.get(cle) is websocket:
            del _connexions[cle]


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


async def notifier_utilisateur(user_id: str, notification: dict) -> bool:
    """
    Ajoute le 02/09/2026, Bourama : centre de notifications Clovis.
    Diffuse `notification` en direct sur la MEME connexion WebSocket que
    poser_question_appareil, mais sans jamais toucher a sa logique de
    correlation/attente -- ceci est un envoi serveur->client simple,
    fire-and-forget, distingue par son champ "type" au niveau du message
    ({"type": "notification_nouvelle", "notification": {...}}) plutot
    que {"id":..., "question":...}. Le cote client (lib/canalTempsReel.ts)
    distingue les deux formes a la reception.

    Renvoie False immediatement (pas d'attente, pas d'exception) si
    l'utilisateur n'a aucune connexion active -- la notification reste
    de toute facon en base (voir core/notifications.py), ce n'est qu'un
    bonus temps reel.

    Modifie le 04/09/2026 : diffuse desormais a TOUS les appareils
    connectes de cet utilisateur (avant cette date, un seul -- la notion
    meme d'"appareil" n'existait pas encore ici), coherent avec le
    centre de notifications qui doit sonner partout, pas seulement sur
    le dernier appareil connecte.
    """
    async with _verrou_connexions:
        websockets = [ws for (uid, _appareil_id), ws in _connexions.items() if uid == user_id]

    if not websockets:
        return False

    diffuse = False
    for websocket in websockets:
        try:
            await websocket.send_json({"type": "notification_nouvelle", "notification": notification})
            diffuse = True
        except Exception as e:
            logging.error(f"ERREUR diffusion notification temps reel (user={user_id}) : {e}")
    return diffuse


async def _appeler_statut(on_statut, texte: str) -> None:
    if on_statut is None:
        return
    try:
        resultat = on_statut(texte)
        if asyncio.iscoroutine(resultat):
            await resultat
    except Exception as e:
        logging.error(f"ERREUR callback statut canal temps reel : {e}")


async def poser_question_appareil(
    user_id: str, appareil_id: str, contenu: Any, on_statut=None
) -> Any | None:
    """
    Fonction centrale du canal temps reel (voir 01-canal-temps-reel.md).

    Pose `contenu` comme question au telephone `appareil_id` de
    `user_id` et attend la reponse. `contenu` est un texte simple pour
    un test basique (Lot 1), ou un objet JSON structure (ex: {"action":
    "lister_contenu", "dossier_nom": ...}) pour les capacites
    d'exploration reelles (Lot 2 et suivants, voir
    core/exploration_dossier_mobile.py) -- transmis tel quel au
    telephone via le champ "question" du message WebSocket.

    `appareil_id` (obligatoire depuis le 04/09/2026, voir
    core/dossiers_designes_mobile.resoudre_appareil_cible) : l'appelant
    doit deja savoir QUEL appareil possede le dossier vise avant
    d'interroger le canal temps reel -- ce module ne devine plus jamais
    "le" telephone de l'utilisateur des qu'il peut y en avoir plusieurs.

    Renvoie :
    - None IMMEDIATEMENT si aucune connexion active pour cet appareil
      precis (app fermee, ou c'est un AUTRE appareil du meme utilisateur
      qui est ouvert) -- jamais d'attente de 30 secondes inutile ;
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
        websocket = _connexions.get((user_id, appareil_id))

    if websocket is None:
        return None

    correlation_id = str(uuid.uuid4())
    future: "asyncio.Future[Any]" = asyncio.get_event_loop().create_future()
    _attentes[correlation_id] = future

    try:
        try:
            await websocket.send_json({"id": correlation_id, "question": contenu})
        except Exception as e:
            logging.error(f"ERREUR envoi question canal temps reel (user={user_id}, appareil={appareil_id}) : {e}")
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
                f"ABANDON canal temps reel (user={user_id}, appareil={appareil_id}, id={correlation_id}) : "
                f"pas de reponse apres {DELAI_ABANDON_SECONDES}s"
            )
            return None
    finally:
        _attentes.pop(correlation_id, None)
