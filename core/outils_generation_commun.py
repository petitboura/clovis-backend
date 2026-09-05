"""
Infrastructure et helpers partagés par tous les modules d'outils MCP
"generation" (core/outils_generation_documents.py, outils_generation_media.py,
outils_bibliotheque.py, outils_memoire_profil.py,
outils_comportements_connaissance.py, outils_mobile.py).

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code. L'objet `mcp_generation` défini ici est LE même
objet FastMCP partagé par tous ces modules : chaque `@mcp_generation.tool()`
déclaré dans un module ci-dessus enregistre bien son outil sur ce serveur
unique, monté dans api/main.py.
"""

import os
import logging
import requests

from mcp.server.mcpserver import MCPServer as FastMCP, Context
from supabase import create_client

from core.bibliotheque_fichiers import enregistrer_fichier as _enregistrer_fichier

# Le classement de documents dans le programme et le rattachement de
# comportements à un emplacement du programme dépendaient de
# core/bibliotheque_programme.py, retiré du code actif. Stubs volontaires :
# plus aucun classement/emplacement n'est autorisé.
TYPES_EMPLACEMENT_BIBLIOTHEQUE: tuple[str, ...] = ()


def _libelle_emplacement(lien_type, lien_id):
    return None


def _lister_emplacements_document(fichier_id):
    return []



_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
_supabase_memoire = create_client(_SUPABASE_URL, _SUPABASE_SECRET)

mcp_generation = FastMCP(name="generation")

# RAPPEL NON NEGOCIABLE (Bourama, 18/08) -- POUR NE PAS OUBLIER :
# tout NOUVEL outil ajoute ici (ou ailleurs dans le depot) doit
# systematiquement faire l'objet d'une question explicite a Bourama :
# "cet outil doit-il aussi etre expose sur le serveur MCP PUBLIC
# (core/serveur_mcp_espace.py, mcp_espace) ?" -- jamais suppose oui,
# jamais suppose non, jamais ajoute la-bas sans validation prealable.

# Même limite que core/serveur_mcp_espace.py et
# api/bibliotheque_utilisateur.py (à garder en phase si elle change).
# Pas de liste blanche de type MIME : retirée du reste du dépôt le 17/08
# (Bourama, "retrait des whitelists de type de fichier"), voir
# core/serveur_mcp_espace.py::ajouter_document_bibliotheque pour la même
# évolution côté serveur externe.
_TAILLE_MAX_OCTETS_BIBLIOTHEQUE = 50 * 1024 * 1024  # 50 Mo


# 02/09/2026, demande Bourama : tout ce que l'IA génère (document, code,
# image, audio, vidéo, 3D...) doit atterrir automatiquement dans la
# bibliothèque personnelle de l'utilisateur, avec une origine dédiée
# ("ia_generee", nouvel onglet "Généré par l'IA" côté UI) -- avant ce
# correctif, ces générations n'étaient JAMAIS sauvegardées nulle part,
# juste affichées comme un lien dans le chat. Best-effort : une erreur
# ici ne doit jamais faire échouer l'outil de génération lui-même (le
# lien reste utilisable même si la sauvegarde échoue), juste logguée.
def _sauvegarder_generation_bibliotheque(ctx: Context, url: str, nom_fichier: str, type_mime: str) -> None:
    try:
        user_id = ctx.request_context.request.query_params.get("user_id")
        if not user_id:
            return
        reponse = requests.get(url, timeout=30)
        reponse.raise_for_status()
        _enregistrer_fichier(
            contenu=reponse.content,
            nom_fichier=nom_fichier,
            type_mime=type_mime,
            niveau="utilisateur",
            uploade_par=user_id,
            user_id=user_id,
            description=nom_fichier,
            origine="ia_generee",
        )
    except Exception as e:
        logging.error(f"ERREUR sauvegarde bibliotheque (generation IA, {nom_fichier}) : {e}")
