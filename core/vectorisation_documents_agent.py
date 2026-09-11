"""
Vectorisation en arrière-plan des sections de la base de connaissance
agent (table `documents`, colonne `embedding`).

Ajouté suite à la suppression de l'upload de fichier pour cette base de
connaissance (demande Bourama) : les sections sont désormais écrites
directement (nom + contenu) dans `documents` via Supabase, embedding
laissé vide -- cette boucle le calcule automatiquement, en réutilisant
le même coupe-circuit quota Gemini que core/file_attente_vectorisation.py
(voir embeddings.py : est_en_pause_quota_gemini/activer_pause_quota_gemini).

Pas de colonnes de statut/tentatives ici (contrairement à
file_attente_vectorisation.py, pensé pour un flux utilisateur en masse) :
ce circuit est alimenté ponctuellement (peu de sections, écrites à la
demande), pas besoin de mécanique de réessai à froid distincte -- une
ligne restée sans embedding après une erreur est simplement retentée au
passage suivant de la boucle (voir api/main.py:_boucle_vectorisation_
documents_agent).
"""

import logging
import os

from supabase import create_client, ClientOptions
from client_http_supabase import nouveau_client_http_supabase

from embeddings import (
    vectoriser,
    est_erreur_quota_gemini,
    est_en_pause_quota_gemini,
    activer_pause_quota_gemini,
)

TAILLE_LOT = 5  # nb max de lignes vectorisées par passage, pour ne pas bloquer longtemps la boucle asyncio


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(
    _get_secret("SUPABASE_URL"),
    _get_secret("SUPABASE_SECRET"),
    options=ClientOptions(httpx_client=nouveau_client_http_supabase()),
)


def traiter_documents_a_vectoriser_une_fois() -> int:
    """
    Un seul passage : vectorise jusqu'à TAILLE_LOT lignes de `documents`
    dont l'embedding est encore vide (nouvelle section écrite, pas
    encore traitée). Voir api/main.py:_boucle_vectorisation_documents_
    agent pour la boucle continue. Renvoie le nombre de lignes
    effectivement vectorisées.
    """
    if est_en_pause_quota_gemini():
        return 0

    try:
        res = (
            supabase.table("documents")
            .select("id, contenu")
            .is_("embedding", "null")
            .limit(TAILLE_LOT)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture documents à vectoriser) : {e}")
        return 0

    traites = 0
    for ligne in res.data or []:
        contenu = (ligne.get("contenu") or "").strip()
        if not contenu:
            continue

        try:
            embedding = vectoriser(contenu)
        except Exception as e:
            if est_erreur_quota_gemini(str(e)):
                activer_pause_quota_gemini()
                break
            logging.error(f"ERREUR vectorisation document id={ligne['id']} : {e}")
            continue

        try:
            supabase.table("documents").update({"embedding": embedding}).eq("id", ligne["id"]).execute()
            traites += 1
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (écriture embedding document id={ligne['id']}) : {e}")

    return traites
