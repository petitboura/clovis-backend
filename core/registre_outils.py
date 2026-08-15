"""
Registre des outils (bras) MCP actifs.

POUR AJOUTER UN NOUVEL OUTIL :
Ajoute une entree dans SERVEURS_MCP ci-dessous. C'est le seul fichier a
modifier. Ni mcp_tools.py (le moteur generique) ni main.py n'ont besoin
d'etre touches.

Deux modes d'authentification sont supportes, car les serveurs MCP ne
s'authentifient pas tous pareil :
- pas de cle du tout (ex: Wolfram)          -> url_builder seul
- cle glissee dans l'URL (ex: Tavily)       -> url_builder seul
- cle envoyee en header HTTP (si besoin un jour) -> url_builder + headers_builder

Chaque *_builder est une fonction qui recoit (get_secret, user_id, agent_id)
et retourne soit une URL (str), soit des headers (dict), soit None. Les
parametres user_id/agent_id sont ignores par la plupart des outils (cle
API globale, comme Tavily/Wolfram) ; ils ne sont utiles que pour un outil
"par utilisateur" (cle "necessite_utilisateur": True), ou chaque utilisateur
connecte son propre compte plutot que d'utiliser une cle partagee par
toute l'app. Pour Notion specifiquement, la connexion est scopee par
user_id seul (compte unifie, juillet 2026) : un utilisateur connecte a
Notion depuis n'importe quel agent l'est automatiquement pour tous les
autres agents de la plateforme -> voir connexions/notion.py.

POUR UN OUTIL "PAR UTILISATEUR" (ex: Notion) :
Ajoute "necessite_utilisateur": True dans son entree. Le dispatcher
(mcp_tools.py) l'ignore alors automatiquement si aucun utilisateur n'est
connecte a l'app, ou si headers_builder renvoie None (utilisateur connecte a
l'app mais pas encore a CET outil POUR CET AGENT) -> pas de bloc if/else
a ecrire ici.
"""

import os

from connexions.notion import obtenir_token_valide

def _url_generation(get_secret, user_id, agent_id):
    # Serveur MCP interne, pas un tiers externe (voir
    # core/serveur_mcp_generation.py, monté dans api/main.py). C'est
    # TOUJOURS le même process/port que celui qui répond à cette
    # requête (localhost, jamais un vrai domaine externe), donc pas
    # besoin de BACKEND_URL ici : on lit directement $PORT, la variable
    # que Railway fournit et qu'uvicorn utilise pour écouter.
    #
    # user_id/agent_id ajoutés en query params (2026-07-22) : nécessaire
    # pour planifier_rappel (notifications push), le premier outil de ce
    # serveur qui a besoin de savoir QUI l'appelle -- récupérés côté
    # serveur via ctx.request_context.request.query_params (voir
    # serveur_mcp_generation.py). Inoffensif pour les autres outils qui
    # n'en ont pas besoin.
    port = os.environ.get("PORT", "8000")
    return f"http://localhost:{port}/mcp/generation?user_id={user_id}&agent_id={agent_id}"


def _url_github(get_secret, user_id, agent_id):
    # Même logique que _url_generation ci-dessus -- serveur MCP interne
    # (core/serveur_mcp_github.py), pas un tiers externe.
    port = os.environ.get("PORT", "8000")
    return f"http://localhost:{port}/mcp/github"


def _url_tavily(get_secret, user_id, agent_id):
    return f"https://mcp.tavily.com/mcp/?tavilyApiKey={get_secret('TAVILY_API_KEY')}"


def _url_wolfram(get_secret, user_id, agent_id):
    # CORRECTIF 2026-08-01 bis (Bourama : "j'ai pas mis de clé wolfram
    # hein" -> creuse). L'URL precedente (services.wolfram.com/api/mcp,
    # "Wolfram MCP Service") est une offre PAYANTE necessitant un
    # abonnement + une cle API (voir support.wolfram.com/73463) -- pas la
    # bonne offre pour ce cas d'usage. La veritable offre gratuite est un
    # produit DIFFERENT, "Wolfram Cloud MCP", confirme sans authentification
    # par DEUX pages officielles distinctes (support.wolfram.com/75237 et
    # wolfram.com/artificial-intelligence/mcp/cloud) : "free to use and
    # does not require any authentication". Limite connue et acceptee pour
    # cet usage (question -> reponse, pas de session multi-etapes) : usage
    # personnel limite, une seule requete a la fois, pas d'upload/download
    # de fichier, pas d'interaction locale.
    return "https://agenttools.wolfram.com/mcp"


def _url_notion(get_secret, user_id, agent_id):
    return "https://mcp.notion.com/mcp"


def _headers_notion(get_secret, user_id, agent_id):
    # agent_id fait partie de la signature commune a tous les *_builder
    # (voir docstring en tete de fichier) mais n'est plus utilise ici :
    # la connexion Notion est scopee par user_id seul (compte unifie).
    token = obtenir_token_valide(user_id)
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


SERVEURS_MCP = [
    {"nom": "wolfram", "url_builder": _url_wolfram},
    {
        "nom": "tavily",
        "url_builder": _url_tavily,
        # Réactivé 2026-07-23 (demande de Bourama) -- la plomberie
        # existait déjà (builder d'URL ci-dessus, libellés de statut
        # tavily_search/tavily_extract/... dans core/main.py, option
        # dans le créateur d'agent) mais l'entrée manquait ici, donc
        # injoignable même pour un agent l'ayant coché dans ses droits.
        #
        # ATTENTION connue (voir commentaire sur "notion" plus bas) :
        # Notion (20 outils) + Tavily cumulés dépassaient la limite
        # 8000 TPM du tier Groq gratuit -> 413 Payload Too Large, qui
        # faisait basculer sur le fallback Gemini SANS AUCUN outil.
        # Le nouveau système de droits par agent (filtrage par outil,
        # plus par serveur) limite le risque -- mais évite quand même
        # d'activer Notion ET Tavily ensemble pour un même agent tant
        # que ce n'est pas revérifié en conditions réelles.
    },
    {
        "nom": "generation",
        "url_builder": _url_generation,
        # Pas de "outils_autorises" fixe ici : categorie 1, filtree
        # dynamiquement par agent (agents_outils_generation croise avec
        # registre_outils_plateforme.disponible), voir mcp_tools.py ->
        # _outils_generation_actifs_pour_agent. Ce serveur est TOUJOURS
        # interroge (voir lister_tous_les_outils), contrairement a
        # wolfram/github/notion qui dependent de agents_serveurs.
    },
    {
        "nom": "github",
        "url_builder": _url_github,
        # explorer_depot_github et lire_fichier_depot_github sont sans
        # risque (lecture seule). modifier_fichier_depot_github ÉCRIT
        # réellement sur un dépôt -> dans OUTILS_SENSIBLES plus bas,
        # donc TOUJOURS interrompu pour confirmation avant exécution,
        # quel que soit le mode (direct ou branche+PR).
    },
    {
        "nom": "notion",
        "url_builder": _url_notion,
        "headers_builder": _headers_notion,
        "necessite_utilisateur": True,
        # Notion active a 100% (01/08, demande Bourama) -- plus de
        # restriction "outils_autorises" : les 20 outils Notion
        # (recherche + creation/edition de pages, bases de donnees,
        # commentaires, equipes...) sont desormais tous disponibles.
        # L'ancienne restriction a notion-search seul datait d'une
        # inquietude sur le budget 8000 TPM du tier Groq gratuit
        # (Notion + Tavily cumules -> 413 Payload Too Large) ; elle est
        # bien moins critique depuis le systeme "bouton Outils"
        # (25-26/07) qui n'envoie de toute facon plus qu'UN OU PLUSIEURS
        # outils selectionnes explicitement au LLM, jamais le catalogue
        # entier. Les outils d'ecriture (notion-create-pages,
        # notion-update-page, notion-move-pages, etc.) restent proteges :
        # ils sont tous dans OUTILS_SENSIBLES plus bas, donc TOUJOURS
        # interrompus pour confirmation utilisateur avant execution.
    },
]

# Outils qui MODIFIENT reellement quelque chose chez l'utilisateur (creation,
# edition, suppression, deplacement...). main.py interrompt le flux et
# demande une confirmation explicite avant d'executer l'un de ces outils,
# quel que soit le serveur MCP dont il provient. Pour l'instant aucun
# outil d'ecriture n'est dans `outils_autorises` ci-dessus (donc cette
# liste n'a pas encore d'effet visible) : elle sert de garde-fou pret a
# l'emploi le jour ou on active par ex. "notion-create-pages".
OUTILS_SENSIBLES = {
    "notion-create-pages",
    "notion-update-page",
    "notion-move-pages",
    "notion-duplicate-page",
    "notion-create-database",
    "notion-update-data-source",
    "notion-create-comment",
    "notion-create-view",
    "notion-update-view",
    "notion-create-attachment",
    # ÉCRIT réellement sur un dépôt GitHub (voir
    # core/serveur_mcp_github.py, modifier_fichier_depot_github) --
    # TOUJOURS interrompu pour confirmation, que ce soit en mode "direct"
    # (commit sur la branche de base) ou "branche_pr" (nouvelle branche +
    # Pull Request). Aucun des deux modes n'est silencieux.
    "modifier_fichier_depot_github",
    # Suppressions programme/comportement (14/08, demande Bourama :
    # "confirmer pour sensible et irréversible") -- contrairement aux
    # ajouts/modifications de ces mêmes ressources (voir
    # core/programme_ecriture.py, annuler_derniere_modification), une
    # suppression n'a pas de filet de rattrapage, donc TOUJOURS confirmée.
    "supprimer_programme",
    "supprimer_matiere",
    "supprimer_chapitre",
    "supprimer_document_programme",
    "supprimer_exercice_programme",
    "supprimer_examen",
    "supprimer_comportement",
}


# Registre d'affichage unique (2026-08-15, demande Bourama : "qu'à un
# nouvel outil, on ne touche pas au frontend" + "beaucoup d'outils
# affichent leur nom brut / une icône générique").
#
# AVANT : deux listes tenues à la main en parallèle -- NOMS_OUTILS_LISIBLES
# dans core/main.py (12 entrées sur 83 outils réels) côté backend, et
# OUTILS_DISPONIBLES dans classgpt-frontend/lib/outils.ts (~24/83) côté
# frontend -- d'où le nom brut ("generer_document_word") ou l'icône
# générique (Wrench) pour tout le reste.
#
# MAINTENANT : ce dict est la SEULE source de vérité pour les deux, sur
# les deux dépôts. Pour ajouter un nouvel outil, une seule ligne à ajouter
# ICI, rien d'autre :
# - Backend (core/main.py, _nom_lisible) : lit ce dict directement.
# - Frontend (classgpt-frontend/lib/outils.ts) : va chercher ce dict via
#   GET /api/outils/registre (voir api/outils_registre.py) au chargement
#   du chat -- aucune modification du frontend nécessaire, aucun rebuild
#   ni redéploiement du dépôt frontend.
#
# `icone` est une chaîne = un nom d'export de lucide-react (vérifié
# présent dans la version installée, classgpt-frontend/package.json).
# Exception : "notion-logo", un cas spécial connu du frontend (logo
# Notion, pas un export lucide-react).
#
# `onglet` correspond aux catégories du menu "Outils" du frontend :
# "generer" / "rechercher" / "action_app" / "utilitaires".
# `appli` (optionnel) : regroupe sous un connecteur externe ("github" ou
# "notion") dans l'onglet "action_app" -- absent pour tout le reste.
REGISTRE_AFFICHAGE_OUTILS = {
    # --- Génération ---
    "generer_document": {"label": "Génération d'un PDF/texte", "icone": "FileText", "onglet": "generer"},
    "generer_document_word": {"label": "Génération d'un Word", "icone": "FileType", "onglet": "generer"},
    "generer_document_excel": {"label": "Génération d'un Excel", "icone": "FileSpreadsheet", "onglet": "generer"},
    "generer_document_powerpoint": {"label": "Génération d'un PowerPoint", "icone": "Presentation", "onglet": "generer"},
    "generer_document_latex": {"label": "Génération d'un document LaTeX", "icone": "FileDigit", "onglet": "generer"},
    "generer_code": {"label": "Génération de code", "icone": "Code", "onglet": "generer"},
    "generer_site_zip": {"label": "Génération d'un site (zip)", "icone": "Package", "onglet": "generer"},
    "generer_bundle": {"label": "Génération d'une archive", "icone": "Archive", "onglet": "generer"},
    "generer_image": {"label": "Génération d'une image", "icone": "Image", "onglet": "generer"},
    "generer_audio": {"label": "Génération audio", "icone": "AudioLines", "onglet": "generer"},
    "lancer_generation_video": {"label": "Génération d'une vidéo", "icone": "Video", "onglet": "generer"},
    "consulter_statut_video": {"label": "Vérification du statut de la vidéo", "icone": "RefreshCw", "onglet": "generer"},
    "lancer_generation_3d": {"label": "Génération d'un modèle 3D", "icone": "Box", "onglet": "generer"},
    "consulter_statut_3d": {"label": "Vérification du statut de génération 3D", "icone": "RefreshCw", "onglet": "generer"},
    "envoyer_pour_signature": {"label": "Envoi pour signature", "icone": "FileSignature", "onglet": "generer"},
    "consulter_statut_signature": {"label": "Vérification du statut de signature", "icone": "RefreshCw", "onglet": "generer"},
    "deployer_site": {"label": "Déploiement d'un site", "icone": "Rocket", "onglet": "generer"},
    "exporter_donnees": {"label": "Export de données", "icone": "FileOutput", "onglet": "generer"},
    "calculer_symbolique": {"label": "Calcul symbolique (résoudre, dériver, intégrer)", "icone": "Divide", "onglet": "generer"},

    # --- Recherche ---
    "tavily_search": {"label": "Recherche web", "icone": "Search", "onglet": "rechercher"},
    "tavily_extract": {"label": "Extraction d'une page", "icone": "FileSearch", "onglet": "rechercher"},
    "tavily_crawl": {"label": "Exploration d'un site", "icone": "Globe", "onglet": "rechercher"},
    "tavily_map": {"label": "Cartographie d'un site", "icone": "Map", "onglet": "rechercher"},
    "tavily_research": {"label": "Recherche approfondie", "icone": "BookOpen", "onglet": "rechercher"},
    "chercher_fichier": {"label": "Recherche d'un fichier", "icone": "FolderSearch", "onglet": "rechercher"},
    "consulter_bibliotheque": {"label": "Consultation de la bibliothèque", "icone": "Library", "onglet": "rechercher"},
    "chercher_dans_base_connaissances": {"label": "Recherche dans la base de connaissances", "icone": "BookMarked", "onglet": "rechercher"},

    # --- Action dans l'app : GitHub ---
    "explorer_depot_github": {"label": "Exploration d'un dépôt GitHub", "icone": "FolderTree", "onglet": "action_app", "appli": "github"},
    "lire_fichier_depot_github": {"label": "Lecture d'un fichier GitHub", "icone": "FileCode", "onglet": "action_app", "appli": "github"},
    "modifier_fichier_depot_github": {"label": "Modification d'un fichier GitHub", "icone": "Edit3", "onglet": "action_app", "appli": "github"},

    # --- Action dans l'app : Notion ---
    "notion-search": {"label": "Recherche dans Notion", "icone": "notion-logo", "onglet": "action_app", "appli": "notion"},
    "notion-fetch": {"label": "Ouverture d'une page/base Notion", "icone": "FileSearch", "onglet": "action_app", "appli": "notion"},
    "notion-query-data-sources": {"label": "Interrogation d'une base Notion (SQL)", "icone": "Table2", "onglet": "action_app", "appli": "notion"},
    "notion-query-database-view": {"label": "Interrogation d'une vue Notion", "icone": "LayoutGrid", "onglet": "action_app", "appli": "notion"},
    "notion-query-meeting-notes": {"label": "Recherche dans les notes de réunion", "icone": "StickyNote", "onglet": "action_app", "appli": "notion"},
    "notion-get-comments": {"label": "Lecture des commentaires Notion", "icone": "MessagesSquare", "onglet": "action_app", "appli": "notion"},
    "notion-get-async-task": {"label": "Suivi d'une tâche Notion en cours", "icone": "Clock", "onglet": "action_app", "appli": "notion"},
    "notion-get-teams": {"label": "Liste des équipes Notion", "icone": "Users", "onglet": "action_app", "appli": "notion"},
    "notion-get-users": {"label": "Liste des utilisateurs Notion", "icone": "UserCog", "onglet": "action_app", "appli": "notion"},
    "notion-download-attachment": {"label": "Téléchargement d'une pièce jointe Notion", "icone": "Download", "onglet": "action_app", "appli": "notion"},
    "notion-create-pages": {"label": "Création d'une page Notion", "icone": "FilePlus", "onglet": "action_app", "appli": "notion"},
    "notion-update-page": {"label": "Modification d'une page Notion", "icone": "Edit3", "onglet": "action_app", "appli": "notion"},
    "notion-move-pages": {"label": "Déplacement d'une page Notion", "icone": "Move", "onglet": "action_app", "appli": "notion"},
    "notion-duplicate-page": {"label": "Duplication d'une page Notion", "icone": "Copy", "onglet": "action_app", "appli": "notion"},
    "notion-create-database": {"label": "Création d'une base Notion", "icone": "Database", "onglet": "action_app", "appli": "notion"},
    "notion-update-data-source": {"label": "Modification du schéma d'une base Notion", "icone": "Settings2", "onglet": "action_app", "appli": "notion"},
    "notion-create-comment": {"label": "Commentaire dans Notion", "icone": "MessageSquare", "onglet": "action_app", "appli": "notion"},
    "notion-create-attachment": {"label": "Ajout d'une pièce jointe Notion", "icone": "Paperclip", "onglet": "action_app", "appli": "notion"},
    "notion-create-view": {"label": "Création d'une vue Notion", "icone": "PanelsTopLeft", "onglet": "action_app", "appli": "notion"},
    "notion-update-view": {"label": "Modification d'une vue Notion", "icone": "SlidersHorizontal", "onglet": "action_app", "appli": "notion"},

    # --- Utilitaires ---
    "planifier_rappel": {"label": "Planification d'un rappel", "icone": "Bell", "onglet": "utilitaires"},
    "envoyer_message": {"label": "Envoi d'un message", "icone": "Send", "onglet": "utilitaires"},
    "consulter_memoire_utilisateur": {"label": "Consultation de ta mémoire", "icone": "Brain", "onglet": "utilitaires"},
    "mettre_a_jour_memoire_utilisateur": {"label": "Mise à jour de ta mémoire", "icone": "Brain", "onglet": "utilitaires"},
    "consulter_profil_utilisateur": {"label": "Consultation de ton profil", "icone": "UserCircle", "onglet": "utilitaires"},
    "mettre_a_jour_profil_utilisateur": {"label": "Mise à jour de ton profil", "icone": "UserCog", "onglet": "utilitaires"},

    # --- Programme adaptatif (interne) ---
    "consulter_matiere_active": {"label": "Consultation de la matière active", "icone": "BookOpen", "onglet": "utilitaires"},
    "ajouter_programme": {"label": "Création d'un programme", "icone": "GraduationCap", "onglet": "utilitaires"},
    "modifier_programme": {"label": "Modification d'un programme", "icone": "GraduationCap", "onglet": "utilitaires"},
    "consulter_programme": {"label": "Consultation d'un programme", "icone": "GraduationCap", "onglet": "utilitaires"},
    "supprimer_programme": {"label": "Suppression d'un programme", "icone": "Trash2", "onglet": "utilitaires"},
    "ajouter_matiere": {"label": "Ajout d'une matière", "icone": "BookOpen", "onglet": "utilitaires"},
    "modifier_matiere": {"label": "Modification d'une matière", "icone": "BookOpen", "onglet": "utilitaires"},
    "supprimer_matiere": {"label": "Suppression d'une matière", "icone": "Trash2", "onglet": "utilitaires"},
    "ajouter_chapitre": {"label": "Ajout d'un chapitre", "icone": "Layers", "onglet": "utilitaires"},
    "modifier_chapitre": {"label": "Modification d'un chapitre", "icone": "Layers", "onglet": "utilitaires"},
    "consulter_chapitre_programme": {"label": "Consultation d'un chapitre", "icone": "Layers", "onglet": "utilitaires"},
    "supprimer_chapitre": {"label": "Suppression d'un chapitre", "icone": "Trash2", "onglet": "utilitaires"},
    "ajouter_document_programme": {"label": "Ajout d'un document au programme", "icone": "FileText", "onglet": "utilitaires"},
    "modifier_document_programme": {"label": "Modification d'un document du programme", "icone": "FileText", "onglet": "utilitaires"},
    "supprimer_document_programme": {"label": "Suppression d'un document du programme", "icone": "Trash2", "onglet": "utilitaires"},
    "ajouter_exercice_programme": {"label": "Ajout d'un exercice", "icone": "ListChecks", "onglet": "utilitaires"},
    "modifier_exercice_programme": {"label": "Modification d'un exercice", "icone": "ListChecks", "onglet": "utilitaires"},
    "supprimer_exercice_programme": {"label": "Suppression d'un exercice", "icone": "Trash2", "onglet": "utilitaires"},
    "ajouter_examen": {"label": "Ajout d'un examen", "icone": "ClipboardList", "onglet": "utilitaires"},
    "modifier_examen": {"label": "Modification d'un examen", "icone": "ClipboardList", "onglet": "utilitaires"},
    "consulter_examens_programme": {"label": "Consultation des examens", "icone": "ClipboardList", "onglet": "utilitaires"},
    "supprimer_examen": {"label": "Suppression d'un examen", "icone": "Trash2", "onglet": "utilitaires"},
    "annuler_derniere_modification": {"label": "Annulation de la dernière modification", "icone": "Undo2", "onglet": "utilitaires"},
    "ajouter_comportement": {"label": "Ajout d'un comportement", "icone": "Sparkles", "onglet": "utilitaires"},
    "modifier_comportement": {"label": "Modification d'un comportement", "icone": "Sparkles", "onglet": "utilitaires"},
    "consulter_comportement": {"label": "Consultation d'un comportement", "icone": "Sparkles", "onglet": "utilitaires"},
    "supprimer_comportement": {"label": "Suppression d'un comportement", "icone": "Trash2", "onglet": "utilitaires"},
}

