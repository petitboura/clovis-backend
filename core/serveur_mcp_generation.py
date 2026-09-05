"""
Serveur MCP local (documents / code / images), monté directement dans
l'API FastAPI existante (voir api/main.py) -- pas un service Railway
séparé, pas de déploiement supplémentaire à gérer.

Pourquoi un serveur MCP plutôt que d'appeler generation_*.py directement
dans core/main.py : pour rester cohérent avec registre_outils.py, qui
documente explicitement "pour ajouter un nouvel outil, ajoute une entrée
dans SERVEURS_MCP, ni mcp_tools.py ni main.py n'ont besoin d'être
touchés". Ce fichier-ci EST le nouveau serveur qu'on enregistre là-bas,
au même titre que Wolfram/Tavily/Notion, sauf qu'il tourne chez nous au
lieu d'être hébergé par un tiers.

Génération d'image (generer_image) est TOUJOURS active maintenant
(Pollinations en repli gratuit, Together AI en amélioration payante
optionnelle -- voir generation_images.py, mis à jour le 21/07/2026).

Découpé le 05/09/2026 (fichier passé de 2524 à ~40 lignes) : ce fichier
ne fait plus que rassembler les modules ci-dessous, qui déclarent chacun
leurs outils sur le même objet partagé `mcp_generation`
(core/outils_generation_commun.py). Aucun changement de comportement,
uniquement un déplacement de code :
  - outils_generation_commun.py  : instance FastMCP partagée + helpers
  - outils_generation_documents.py : Word/Excel/PowerPoint/LaTeX/code/
    calcul symbolique/export/site
  - outils_generation_media.py   : image/3D/vidéo/audio/signature
  - outils_bibliotheque.py       : fichiers/dossiers/RAG de la bibliothèque
  - outils_memoire_profil.py     : mémoire, historique, profil, messagerie,
    rappels
  - outils_comportements_connaissance.py : comportements (skills), base
    de connaissance, matière active
  - outils_mobile.py             : dossiers désignés et exploration sur
    le téléphone de l'étudiant
"""

# RAPPEL NON NEGOCIABLE (Bourama, 18/08) -- POUR NE PAS OUBLIER :
# tout NOUVEL outil ajoute dans un des modules ci-dessous (ou ailleurs
# dans le depot) doit systematiquement faire l'objet d'une question
# explicite a Bourama : "cet outil doit-il aussi etre expose sur le
# serveur MCP PUBLIC (core/serveur_mcp_espace.py, mcp_espace) ?" --
# jamais suppose oui, jamais suppose non, jamais ajoute la-bas sans
# validation prealable.

# Fonctionnalité "Programme" désactivée et isolée le 29/08/2026 (demande
# Bourama) -- voir _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md.
# Anciens imports (ne jamais réactiver) :
#   from core.programme_llm import obtenir_structure_programme, obtenir_chapitres_matiere,
#       obtenir_contenu_chapitre, obtenir_examens_programme, lister_mes_programmes_legers
#   from core.programme_ecriture import ajouter_programme, modifier_programme, ajouter_matiere,
#       modifier_matiere, ajouter_chapitre, modifier_chapitre, ajouter_document, modifier_document,
#       ajouter_exercice, modifier_exercice, ajouter_examen, modifier_examen, supprimer_programme,
#       supprimer_matiere, supprimer_chapitre, supprimer_document, supprimer_exercice,
#       supprimer_examen, annuler_derniere_modification

from core.outils_generation_commun import mcp_generation  # noqa: F401 (ré-exporté pour api/main.py)

# Les imports suivants n'ont l'air "inutilisés" que pour un linter : ils
# déclenchent, par effet de bord, l'enregistrement de chaque outil sur
# `mcp_generation` (décorateurs @mcp_generation.tool() exécutés à l'import).
import core.outils_generation_documents  # noqa: F401
import core.outils_generation_media  # noqa: F401
import core.outils_bibliotheque  # noqa: F401
import core.outils_memoire_profil  # noqa: F401
import core.outils_comportements_connaissance  # noqa: F401
import core.outils_mobile  # noqa: F401
