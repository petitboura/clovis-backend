"""
Backend API du frontend Next.js (Streamlit entièrement retiré depuis le 25/07/2026).

Lancement local : uvicorn api.main:app --reload --port 8000
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from anyio import to_thread
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.agents import router as agents_router
from api.profiles import router as profiles_router
from api.uploads import router as uploads_router
from api.historique import router as historique_router
from api.notifications_push import router as notifications_push_router
from api.chat import router as chat_router
from api.feedback import router as feedback_router
from api.generation import router as generation_router
from api.memoire import router as memoire_router
from api.bibliotheque_utilisateur import router as bibliotheque_utilisateur_router
from api.contenu_dynamique_matiere import router_enseignant as contenu_matiere_enseignant_router
from api.contenu_dynamique_matiere import router_etudiant as contenu_matiere_etudiant_router
from api.contenu_dynamique_matiere import router_liste_agents as contenu_matiere_liste_agents_router
from api.comportements_etudiants import router as comportements_etudiants_router
from api.programmes import router_programmes, router_matieres, router_chapitres
from api.audits_programme import router_audits_programme
from api.contenu_programme import router as contenu_programme_router
from api.plugins_programme import router as plugins_router, router_programmes as plugins_programmes_router
from api.codes_partage import router_mes_codes, router_rattachements
from api.outils_registre import router as outils_registre_router
from core.serveur_mcp_generation import mcp_generation
from core.notifications_push import traiter_rappels_echus, notifications_push_disponible
from core.proactivite import verifier_relances_proactives
from core.serveur_mcp_github import mcp_github
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)


async def _boucle_planificateur_rappels():
    # Vérifie les rappels arrivés à échéance toutes les 60s (voir
    # core/notifications_push.py:traiter_rappels_echus). Tourne tant que
    # le process vit -- pas de garantie de service externe (cron
    # Railway, etc.), donc si le process redémarre, au pire un rappel
    # est traité quelques secondes plus tard, jamais perdu (la ligne
    # reste "envoye=false" en base tant qu'elle n'a pas été traitée).
    while True:
        try:
            traites = traiter_rappels_echus()
            if traites:
                logging.info(f"Planificateur rappels : {traites} notification(s) envoyée(s).")
        except Exception as e:
            logging.error(f"ERREUR boucle planificateur rappels : {e}")
        await asyncio.sleep(60)


async def _boucle_planificateur_proactivite():
    # Contrairement aux rappels (demande explicite, échéance à la
    # minute), la proactivité se mesure en jours d'inactivité (voir
    # core/proactivite.py) -- pas besoin d'un passage aussi fréquent.
    # Intervalle volontairement plus long que COOLDOWN_VERIFICATION
    # (6h) pour ne jamais re-scanner une paire déjà vérifiée dans le
    # même cycle.
    while True:
        try:
            envoyees = verifier_relances_proactives()
            if envoyees:
                logging.info(f"Planificateur proactivité : {envoyees} relance(s) envoyée(s).")
        except Exception as e:
            logging.error(f"ERREUR boucle planificateur proactivité : {e}")
        await asyncio.sleep(6 * 60 * 60)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Toutes les routes API sont en `def` sync (Supabase, Groq, Gemini :
    # SDKs synchrones) -- FastAPI les exécute correctement dans le
    # threadpool par défaut d'AnyIO, mais celui-ci est limité à 40
    # workers. /api/chat (SSE) retient un worker pendant toute la
    # génération (plusieurs secondes en streaming) : au-delà de ~40
    # conversations simultanées, les nouvelles requêtes attendraient un
    # worker libre. Relevé ici en attendant une éventuelle migration vers
    # des clients async (AsyncGroq, etc.) -- valeur à ajuster selon la
    # RAM disponible sur Railway (chaque thread a un coût mémoire).
    to_thread.current_default_thread_limiter().total_tokens = 100

    # Requis par FastMCP (stateless_http=True) : le session_manager du
    # serveur MCP de génération (voir core/serveur_mcp_generation.py) a
    # besoin de tourner pendant toute la durée de vie du process, sinon
    # streamable_http_app() renvoie une erreur "Task group is not
    # initialized" au premier appel d'outil.
    async with mcp_generation.session_manager.run(), mcp_github.session_manager.run():
        tache_planificateur = None
        tache_proactivite = None
        if notifications_push_disponible():
            tache_planificateur = asyncio.create_task(_boucle_planificateur_rappels())
            tache_proactivite = asyncio.create_task(_boucle_planificateur_proactivite())
        yield
        if tache_planificateur:
            tache_planificateur.cancel()
        if tache_proactivite:
            tache_proactivite.cancel()


app = FastAPI(title="Clovis API", version="0.1.0", lifespan=_lifespan)

# Serveur MCP interne (documents/code/images), monté en sous-application
# ASGI : voir core/serveur_mcp_generation.py pour le detail des outils, et
# registre_outils.py pour son enregistrement côté agent (nom "generation").
# CORRECTION (29/07) : mcp 2.0.0 a deplace stateless_http et
# streamable_http_path du constructeur MCPServer(...) vers
# streamable_http_app(...) -- voir les 2 fichiers serveur_mcp_*.py, qui ne
# les passent plus a la construction. streamable_http_path="/" fait que
# le point d'entree final est bien /mcp/generation, sans /mcp en trop.
app.mount("/mcp/generation", mcp_generation.streamable_http_app(stateless_http=True, streamable_http_path="/"))

# Serveur MCP interne (exploration/lecture/écriture GitHub) : voir
# core/serveur_mcp_github.py, monté de la même façon que "generation"
# ci-dessus. registre_outils.py l'enregistre sous le nom "github".
app.mount("/mcp/github", mcp_github.streamable_http_app(stateless_http=True, streamable_http_path="/"))

# Domaines autorisés à appeler cette API. Service isolé pour Clovis
# uniquement (séparé de djiguigne-backend le 12/08) -- seules les origines
# Clovis restent ici, les origines Djiguignè (djiguign-ai.vercel.app,
# app.djiguigne.com) retirées puisque ce service ne les sert plus.
ORIGINES_AUTORISEES = [
    "http://localhost:3000",
]

# Vercel donne une URL DIFFERENTE a chaque deploiement de preview (en plus
# de l'alias stable) -- ce motif autorise automatiquement toutes les URLs
# Vercel du projet Clovis (ex. clovis-frontend-bld5bmptn-petitbouras-
# projects.vercel.app), sans avoir a retoucher ce fichier a chaque nouveau
# lien. 11/08 : accepte encore "classgpt-frontend" en plus de
# "clovis-frontend" le temps que le projet Vercel lui-meme soit renomme
# (a retirer une fois fait dans ses Settings).
MOTIF_ORIGINES_CLOVIS = r"https://(classgpt|clovis)-frontend[a-z0-9\-]*\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_AUTORISEES,
    allow_origin_regex=MOTIF_ORIGINES_CLOVIS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GZipSaufChat:
    """GZip sur toute l'API SAUF /api/chat.

    Le fil, l'historique, la recherche et les listes d'agents gagnent
    beaucoup à être compressés (JSON qui peut être volumineux). Le SSE
    de /api/chat, lui, streame des chunks minuscules token par token :
    les compresser n'apporte quasi rien et ajouterait une latence de
    flush inutile, à l'encontre du réglage anti-buffering déjà en place
    (X-Accel-Buffering: no, voir api/chat.py). D'où l'exclusion
    explicite plutôt qu'un GZipMiddleware appliqué partout.
    """

    def __init__(self, app):
        self._app_brut = app
        self._app_gzip = GZipMiddleware(app, minimum_size=500)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/chat"):
            await self._app_brut(scope, receive, send)
        else:
            await self._app_gzip(scope, receive, send)


app.add_middleware(GZipSaufChat)

app.include_router(agents_router)
app.include_router(profiles_router)
app.include_router(uploads_router)
app.include_router(historique_router)
app.include_router(chat_router)
app.include_router(feedback_router)
app.include_router(generation_router)
app.include_router(notifications_push_router)
app.include_router(memoire_router)
app.include_router(bibliotheque_utilisateur_router)
# roles_router (ancien systeme etablissement/enseignant/etudiant) retire
# le 12/08 lors de l'isolation de ce service -- deja remplace cote Clovis
# depuis le 09/08 par contenu_matiere_enseignant_router/etudiant_router
# ci-dessous (meme metier : generer/entrer un code, sans notion de role).
# api/roles.py et api/permissions_hierarchie.py restent sur disque, non
# montes : core/serveur_mcp_generation.py et api/agents.py importent
# encore quelques fonctions de ces fichiers (resoudre_destinataire_autorise,
# _inserer_message, peut_modifier_comportement, peut_gerer_base_connaissances).
app.include_router(contenu_matiere_enseignant_router)
app.include_router(contenu_matiere_etudiant_router)
app.include_router(contenu_matiere_liste_agents_router)
app.include_router(comportements_etudiants_router)
app.include_router(router_programmes)
app.include_router(router_matieres)
app.include_router(router_chapitres)
app.include_router(router_audits_programme)
app.include_router(contenu_programme_router)
app.include_router(plugins_router)
app.include_router(plugins_programmes_router)
app.include_router(router_mes_codes)
app.include_router(router_rattachements)
app.include_router(outils_registre_router)


@app.get("/health")
def health():
    """Verification basique : l'API repond, sans dependance a Supabase."""
    return {"status": "ok"}


@app.get("/health/me")
def health_me(utilisateur=Depends(utilisateur_courant)):
    """
    Verification de bout en bout de l'auth : necessite un vrai token
    Supabase valide en en-tete Authorization. Sert a valider, avant de
    construire quoi que ce soit d'autre, que le frontend arrive bien a
    s'authentifier aupres de cette API. A garder meme apres l'Etape 0
    (utile pour deboguer un token en prod).
    """
    return {"id": utilisateur.id, "email": utilisateur.email}

