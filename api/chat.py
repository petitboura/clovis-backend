"""
Route de chat en streaming pour le frontend Next.js (djiguigne-frontend).

Chaînon manquant identifié pendant la migration Streamlit -> Next.js :
jusqu'ici, la fonction chat()
(core/main.py) n'était appelée qu'en interne par chat.py (Streamlit), en
process. Cette route l'expose en HTTP, via Server-Sent Events (SSE), pour
que la nouvelle page de chat React puisse lui parler à distance.

La logique IA elle-même (chat(), dans core/main.py) n'est PAS réécrite ici,
seulement branchée à un vrai endpoint HTTP -- même prompt système, mêmes
outils MCP, même cascade de modèles qu'avant.
"""

import json
import logging
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Literal

from api.auth import utilisateur_optionnel, supabase
from main import chat as chat_generateur  # core/main.py:chat()
from fournisseurs_llm import modele_id_est_autorise

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class MessageHistorique(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Localisation(BaseModel):
    latitude: float
    longitude: float


class RepriseConfirmation(BaseModel):
    etat_reprise: dict
    approuve: bool


class EnvoyerMessagePayload(BaseModel):
    # message/agent_id optionnels : sur un appel de reprise (après
    # confirmation_requise), seul `reprise` est fourni -- chat_generateur
    # les ignore dans ce cas (voir core/main.py:chat()).
    message: Optional[str] = None
    agent_id: Optional[str] = None
    historique: List[MessageHistorique] = []
    conversation_id: Optional[str] = None
    # Barre de saisie migrée : sélecteur Courte/Moyenne/Longue, modifiable à chaque message.
    longueur_reponse: Literal["courte", "moyenne", "longue"] = "moyenne"
    # Image jointe au message (URL publique renvoyée par
    # POST /api/uploads/image-chat, voir uploads.py). Quand présente,
    # core/main.py:chat() route directement vers Gemini (seul modèle
    # multimodal de la cascade) au lieu du cascade Groq habituel — voir
    # le commentaire au-dessus de la branche image_url dans chat().
    image_url: Optional[str] = None
    # Position GPS transmise explicitement par l'étudiant via un bouton
    # dédié (jamais capturée automatiquement) -- voir core/main.py:chat(),
    # paramètre localisation, injecté en contexte de prompt système.
    localisation: Optional[Localisation] = None
    # Fuseau IANA du navigateur (Intl.DateTimeFormat().resolvedOptions().timeZone),
    # PAS une valeur choisie côté serveur -- voir core/main.py:chat().
    fuseau_horaire: Optional[str] = None
    # Frames JPEG en base64, extraites d'une vidéo uploadée (voir
    # api/uploads.py:uploader_video_chat). Combinable avec image_url mais
    # rarement les deux en même temps en pratique.
    images_base64: Optional[List[str]] = None
    # Icône de recherche web dans la barre de saisie (djiguigne-frontend,
    # 2026-07-23) -- forçage manuel EN PLUS de l'activation automatique
    # déjà possible : le modèle peut de toute façon décider seul
    # d'utiliser Tavily (tool-calling normal, voir INSTRUCTIONS_RECHERCHE_FORCEE
    # dans core/main.py). Ce flag garantit que ça arrive pour CE message
    # précis, quand l'étudiant veut être sûr d'avoir une recherche fraîche.
    recherche_forcee: Optional[bool] = None
    # Bouton "Outils" (2026-07-25, étendu à la MULTI-sélection le 26/07 --
    # voir core/mcp_tools.py:lister_tous_les_outils) : liste des outils
    # sélectionnés manuellement côté frontend (BarreDeSaisie.tsx), zéro,
    # un ou plusieurs à la fois.
    outil_force: Optional[List[str]] = None
    # Bouton "Aucun" à côté des suggestions du routeur (2026-07-31, demande
    # Bourama : le routeur se trompe souvent) -- distinct de outil_force
    # vide/absent : signale explicitement "réponds normalement, ne relance
    # pas le routeur", voir core/main.py:chat() pour le pourquoi (sinon
    # boucle silencieuse de suggestion).
    ignorer_suggestion_outils: Optional[bool] = False
    # Ajouté (2026-07-20) pour exposer le chemin de reprise de chat() --
    # jusqu'ici accessible seulement en appel Python interne (chat.py
    # Streamlit), jamais via cette route HTTP. Voir StatutOutil.tsx /
    # ChatIA.tsx côté djiguigne-frontend pour le flux de confirmation d'outil.
    reprise: Optional[RepriseConfirmation] = None
    # Selecteur de modele premium (02/08/2026, voir core/fournisseurs_llm.py
    # et djiguigne-frontend) : modele_id choisi par l'etudiant/le createur
    # pour CE message (ex: "claude-sonnet-5"). None = comportement
    # historique inchange (cascade Groq/Gemini par defaut). REVALIDE ici
    # contre les modeles reellement debloques de l'agent avant d'etre
    # transmis a chat() -- jamais fait confiance a la valeur brute envoyee
    # par le frontend (voir _resoudre_modele_force plus bas).
    modele: Optional[str] = None
    # Bouton "Sans enseignant" (06/08/2026, demande Bourama) -- uniquement
    # pertinent pour les agents à contenu dynamique par matière (Nitrux,
    # voir core/contenu_dynamique_matiere.py) : force le prompt
    # généraliste pour CE message précis, sans utiliser le contenu
    # d'aucun enseignant même si l'étudiant a des matières débloquées.
    # Sans effet sur tous les autres agents (ignoré silencieusement par
    # _construire_system_prompt, qui ne consulte ce flag que pour les
    # agents marqués contenu_dynamique_par_matiere).
    sans_enseignant: Optional[bool] = False


def _resoudre_modele_force(agent_id, modele_demande):
    """
    Revalide `modele_demande` (envoye par le frontend) contre ce que
    l'agent `agent_id` a REELLEMENT debloque en base (distributeur_debloque/
    palier_debloque, voir api/agents.py et core/fournisseurs_llm.py) --
    jamais de confiance aveugle dans un modele_id venu du client, sinon
    n'importe qui pourrait forcer Claude/GPT sur un agent qui n'a rien
    debloque. Retourne None (repli silencieux sur la cascade Groq
    habituelle) si `modele_demande` est absent, si l'agent est introuvable,
    ou si le modele n'est pas dans la liste autorisee pour CET agent.
    """
    if not modele_demande or not agent_id:
        return None
    try:
        res = (
            supabase.table("agents")
            .select("distributeur_debloque, palier_debloque")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (verification modele premium, agent {agent_id}) : {e}")
        return None
    if not res or not res.data:
        return None
    ligne = res.data
    if modele_id_est_autorise(modele_demande, ligne.get("distributeur_debloque"), ligne.get("palier_debloque")):
        return modele_demande
    logging.error(
        f"Modele '{modele_demande}' demande mais non debloque pour l'agent {agent_id} -- ignore, repli sur Groq."
    )
    return None


def _evenements_sse(payload: EnvoyerMessagePayload, user_id: Optional[str]):
    """
    Convertit le générateur Python de chat() en flux SSE (`data: {...}\n\n`
    par événement), le format que `fetch` + `ReadableStream` côté Next.js
    sait consommer nativement sans dépendance supplémentaire.

    Ne change PAS la structure des événements produits par chat() (voir sa
    docstring) -- on les sérialise tels quels, un JSON par ligne `data:`.
    """
    try:
        if payload.reprise is not None:
            generateur = chat_generateur(
                reprise={
                    "etat_reprise": payload.reprise.etat_reprise,
                    "approuve": payload.reprise.approuve,
                }
            )
        else:
            generateur = chat_generateur(
                message_utilisateur=payload.message,
                historique=[m.model_dump() for m in payload.historique],
                user_id=user_id,
                agent_id=payload.agent_id,
                conversation_id=payload.conversation_id,
                longueur_reponse=payload.longueur_reponse,
                image_url=payload.image_url,
                localisation=payload.localisation.model_dump() if payload.localisation else None,
                fuseau_horaire=payload.fuseau_horaire,
                images_base64=payload.images_base64,
                recherche_forcee=payload.recherche_forcee,
                outil_force=payload.outil_force,
                ignorer_suggestion_outils=payload.ignorer_suggestion_outils or False,
                modele_force=_resoudre_modele_force(payload.agent_id, payload.modele),
                sans_enseignant=payload.sans_enseignant or False,
            )
        for evenement in generateur:
            yield f"data: {json.dumps(evenement)}\n\n"
    except Exception as e:
        logging.error(f"ERREUR chat() en streaming (agent_id={payload.agent_id}) : {e}")
        yield f"data: {json.dumps({'type': 'reponse', 'texte': 'Une erreur est survenue, réessaie dans un instant.'})}\n\n"
    # Signal de fin explicite : côté Next.js, permet de savoir que le flux
    # est terminé sans dépendre uniquement de la fermeture de connexion.
    yield "data: [DONE]\n\n"


@router.post("")
def envoyer_message(payload: EnvoyerMessagePayload, utilisateur=Depends(utilisateur_optionnel)):
    """
    Chat accessible aux visiteurs non connectés (utilisateur_optionnel),
    comme sur chat.py -- voir SEUIL_VISITEUR_NON_CONNECTE côté ancien
    frontend Streamlit ; la même limite devra être réimplémentée côté
    Next.js (comptage local, pas de dépendance à cette route pour ça).

    user_id=None si non connecté : chat() gère déjà ce cas (pas de
    mémoire long-terme persistée, pas d'événement "meta" -- voir sa
    docstring), donc rien de spécial à faire ici.
    """
    user_id = utilisateur.id if utilisateur else None
    return StreamingResponse(
        _evenements_sse(payload, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # évite le buffering côté proxy (Railway/nginx)
        },
    )
