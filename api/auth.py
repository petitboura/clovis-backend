"""
Verifie le token Supabase envoye par le frontend Next.js (en-tete
`Authorization: Bearer <access_token>`), sans jamais gerer de mot de
passe cote API : l'inscription/connexion se font entierement dans
Next.js via le SDK JS Supabase.
"""

import os
import logging
from fastapi import Header, HTTPException
from supabase import create_client
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    """
    Variables d'environnement uniquement (Railway/uvicorn).
    """
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error("SUPABASE_URL ou SUPABASE_SECRET manquant : l'auth API sera toujours en echec.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def utilisateur_courant(authorization: str = Header(default=None)):
    """
    Dependance FastAPI a utiliser sur toute route protegee :

        @app.post("/api/agents")
        def creer_agent(payload: ..., utilisateur=Depends(utilisateur_courant)):
            owner_id = utilisateur.id
            ...

    Leve une 401 si le token est absent, mal forme, ou invalide/expire.
    Ne verifie PAS de permissions metier (ex: "est-ce le proprietaire de
    cet agent ?") : ca reste a la charge de chaque route (verification
    owner_id).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise erreur_api(401, "TOKEN_MANQUANT")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise erreur_api(401, "TOKEN_MANQUANT")

    try:
        reponse = supabase.auth.get_user(token)
    except Exception as e:
        logging.error(f"ERREUR verification token Supabase : {e}")
        raise erreur_api(401, "TOKEN_INVALIDE")

    if not reponse or not reponse.user:
        raise erreur_api(401, "TOKEN_INVALIDE")

    return reponse.user


def utilisateur_optionnel(authorization: str = Header(default=None)):
    """
    Variante de utilisateur_courant pour les routes PUBLIQUES mais
    personnalisables (ex: GET .../follow, qui doit rester accessible sans
    connexion pour afficher un compteur, mais renvoie en plus "est-ce que
    JE suis ce créateur" si un token valide est fourni). Ne lève jamais :
    renvoie None si le token est absent, mal forme, ou invalide/expire,
    plutot qu'une 401. Ajoutee pour le portfolio créateur (étape D.4 du
    pivot social).
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None

    try:
        reponse = supabase.auth.get_user(token)
    except Exception:
        return None

    if not reponse or not reponse.user:
        return None

    return reponse.user
