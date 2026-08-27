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
        # Un seul outil consolidé le 26/08 (ex explorer_depot_github,
        # lire_fichier_depot_github, modifier_fichier_depot_github) :
        # gerer_depot_github. Les actions "explorer" et "lire_fichier"
        # sont sans risque (lecture seule). "modifier_fichier" ÉCRIT
        # réellement sur un dépôt, dans OUTILS_SENSIBLES plus bas (format
        # "nom_outil:action"), donc TOUJOURS interrompu pour confirmation
        # avant exécution, quel que soit le mode (direct ou branche+PR).
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
    # core/serveur_mcp_github.py, gerer_depot_github action
    # "modifier_fichier", consolidé le 26/08, ex modifier_fichier_depot_github) :
    # TOUJOURS interrompu pour confirmation, que ce soit en mode "direct"
    # (commit sur la branche de base) ou "branche_pr" (nouvelle branche,
    # Pull Request). Aucun des deux modes n'est silencieux. Format
    # "nom_outil:action" (voir plus bas pour gerer_document_bibliotheque) :
    # seule cette action est sensible, "explorer" et "lire_fichier" non.
    "gerer_depot_github:modifier_fichier",
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
    # Consolidé le 26/08 en une action de gerer_comportement (voir format
    # "nom_outil:action" expliqué plus bas pour gerer_document_bibliotheque).
    "gerer_comportement:supprimer",
    # Portés depuis core/serveur_mcp_espace.py le 17/08 (demande Bourama :
    # "ajoute à Clovis tout ce que Claude peut faire") -- mêmes garanties
    # que côté MCP externe (destructive_hint=True là-bas), transposées
    # ici via OUTILS_SENSIBLES puisque c'est le mécanisme propre à
    # l'agent interne.
    # Consolidé le 26/08 en une action de gerer_document_bibliotheque (et
    # non plus un outil séparé) : clé "nom_outil:action" -- seule cette
    # action précise est sensible, pas les 11 autres du même outil (voir
    # _est_outil_sensible dans main.py, qui sait lire ce format composite).
    "gerer_document_bibliotheque:supprimer",
    # Consolidé le 26/08 en une action de gerer_memoire_utilisateur (même
    # format composite "nom_outil:action").
    "gerer_memoire_utilisateur:effacer",
    # Section "Notion-like" (Partie 2, lot 1/5, 20/08) -- même logique que
    # les suppressions programme ci-dessus : irréversible, toujours confirmé.
    "supprimer_page",
    "supprimer_bloc",
    # Dossiers de la bibliothèque personnelle (22/08, demande Bourama) :
    # peut supprimer des fichiers avec le dossier (voir
    # core/dossiers_bibliotheque.py:supprimer_dossier) -- irréversible,
    # toujours confirmé, même logique que les autres suppressions ci-dessus.
    # Consolidé le 26/08 en une action de gerer_dossier_bibliotheque (voir
    # format "nom_outil:action" expliqué au-dessus pour
    # gerer_document_bibliotheque:supprimer).
    "gerer_dossier_bibliotheque:supprimer",
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
    "lancer_generation_3d": {"label": "Génération d'un modèle 3D", "icone": "Box", "onglet": "generer"},
    "envoyer_pour_signature": {"label": "Envoi pour signature", "icone": "FileSignature", "onglet": "generer"},
    "consulter_statut_generation": {"label": "Vérification du statut d'une génération", "icone": "RefreshCw", "onglet": "generer"},
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
    # gerer_document_bibliotheque (consolidé le 26/08, ex 12 outils
    # séparés -- consulter_bibliotheque, consulter_bibliotheque_publique,
    # lister/ajouter/supprimer/classer/déclasser/ranger/retirer/lire_entier,
    # voir serveur_mcp_generation.py) : une seule entrée d'affichage
    # désormais, même onglet "rechercher" que l'ancien consulter_bibliotheque
    # (seule action manuellement cliquable, les autres restent onglet=None
    # en pratique côté modèle -- pas besoin de doublon d'entrée pour ça).
    "gerer_document_bibliotheque": {"label": "Bibliothèque personnelle", "icone": "Library", "onglet": "rechercher"},
    "gerer_base_connaissance": {"label": "Base de connaissances de Clovis", "icone": "BookMarked", "onglet": "rechercher"},

    # --- Action dans l'app : GitHub ---
    "gerer_depot_github": {"label": "Dépôt GitHub", "icone": "Github", "onglet": "action_app", "appli": "github"},

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
    #
    # !!! LIRE AVANT DE TOUCHER À CETTE SECTION (18/08, consigne explicite
    # de Bourama, à respecter systématiquement, séance après séance) !!!
    # Ce bouton s'est déjà fait polluer/casser plusieurs fois sans qu'on
    # le lui demande : le 17/08, 22 outils de programme s'y sont
    # retrouvés étiquetés "utilitaires" par erreur (28 entrées au lieu de
    # 6), et un simple crash de prod ailleurs dans le backend a suffi à le
    # faire disparaître entièrement. Consigne : NE JAMAIS ajouter un
    # outil ici (nouveau ou existant) juste parce qu'il EXISTE ou qu'il
    # semble "logique" de l'y mettre -- onglet="utilitaires" est une
    # décision consciente à chaque fois, prise avec Bourama, jamais un
    # défaut. Un nouvel outil backend (programme, bibliothèque, mémoire,
    # profil, etc.) doit être onglet=None SAUF instruction explicite du
    # contraire. Avant toute modif de ce fichier : relire ce bloc en
    # entier, et vérifier après coup que ce bouton affiche encore
    # exactement ce qu'il doit afficher (ne jamais faire confiance à un
    # "ça devrait marcher" sans revérifier la liste réelle).
    #
    # Mémoire/profil/message RETIRES d'ici le 18/08 (demande Bourama :
    # "enlève tout ce qui a lien avec mémoire ou profil et envoi d'un
    # message aussi") -- onglet=None comme les blocs Programme/
    # Bibliothèque plus bas : gardent leur icône pour la bulle "résultat
    # d'outil", mais ne sont plus des boutons cliquables ici. planifier_
    # rappel, lui, avait déjà été retiré le 17/08 (voir plus bas dans
    # l'historique git de ce fichier).
    "envoyer_message": {"label": "Envoi d'un message", "icone": "Send", "onglet": None},
    "gerer_memoire_utilisateur": {"label": "Ta mémoire", "icone": "Brain", "onglet": None},
    "consulter_profil_utilisateur": {"label": "Consultation de ton profil", "icone": "UserCircle", "onglet": None},
    "mettre_a_jour_profil_utilisateur": {"label": "Mise à jour de ton profil", "icone": "UserCog", "onglet": None},

    # --- Actions locales UI (préfixe "ui_") ---
    # Ajoutées ici le 17/08 (bug signalé par Bourama) : ces 6 entrées
    # existaient avant le 15/08 dans le bouton Utilitaires (venaient alors
    # de l'ancienne liste statique OUTILS_DISPONIBLES côté frontend), mais
    # ont disparu quand ce bouton est passé à ce registre backend comme
    # source vivante -- elles n'y avaient jamais été migrées. PAS de vrais
    # outils MCP (interceptées par le préfixe "ui_" dans BarreDeSaisie.tsx,
    # estOutilActif/executerActionOutil -- jamais envoyées au routeur ni au
    # modèle), donc aucun risque à les lister ici : ce registre est de
    # l'affichage pur (label/icône/onglet), pas la liste réelle des outils
    # exécutables. Autorisation par agent déjà en base (agents_actions_
    # locales + registre_outils_plateforme catégorie 4, vérifié le 17/08
    # pour "clovis" : les 6 y sont déjà cochées et disponibles).
    #
    # Depuis le 18/08 (voir avertissement en tête de section), CE SONT
    # LES 6 SEULES ENTRÉES QUE CE BOUTON DOIT AFFICHER. Si tu envisages
    # d'en ajouter une 7e, relis d'abord l'avertissement ci-dessus.
    "ui_localisation": {"label": "Joindre ma position", "icone": "MapPin", "onglet": "utilitaires"},
    "ui_formule": {"label": "Insérer une formule / réaction chimique", "icone": "Sigma", "onglet": "utilitaires"},
    "ui_editeur_maths": {"label": "Éditeur maths live (texte + formules)", "icone": "Calculator", "onglet": "utilitaires"},
    "ui_recherche": {"label": "Forcer une recherche web", "icone": "Search", "onglet": "utilitaires"},
    "ui_dessin": {"label": "Dessiner (géométrie, graphe, croquis)", "icone": "PenLine", "onglet": "utilitaires"},
    "ui_mode_vocal": {"label": "Mode vocal (bientôt disponible)", "icone": "AudioLines", "onglet": "utilitaires"},

    # --- Bibliothèque (gestion) --- toutes les actions de gestion
    # (lister/ajouter/supprimer/classer/déclasser/ranger/retirer/lire_entier)
    # sont désormais dans gerer_document_bibliotheque ci-dessus (fusion du
    # 26/08) -- plus d'entrées séparées ici, onglet=None n'avait de toute
    # façon aucun effet visuel puisque ces actions n'étaient jamais des
    # boutons cliquables (autonomie du modèle).

    # --- Dossiers de la bibliothèque (22/08, demande Bourama) : NON
    # consolidés (groupe distinct, resource "dossier" plutôt que
    # "document"), onglet=None (autonomie du modèle, pas des boutons
    # cliqués par l'utilisateur).
    # --- Dossiers de la bibliothèque (22/08, demande Bourama ; consolidé
    # le 26/08 en un seul outil gerer_dossier_bibliotheque, ex 5 outils
    # séparés) : onglet=None (autonomie du modèle, pas des boutons cliqués
    # par l'utilisateur).
    "gerer_dossier_bibliotheque": {"label": "Dossiers de la bibliothèque", "icone": "FolderTree", "onglet": None},

    # --- Historique (porté le 17/08 depuis serveur_mcp_espace.py) ---
    # Même onglet=None : section "Historique" à part entière de "Mon
    # espace", pas un bouton du menu Outils du chat.
    "lister_conversations_historique": {"label": "Liste des conversations passées", "icone": "History", "onglet": None},
    "lire_conversation_historique": {"label": "Lecture d'une conversation passée", "icone": "History", "onglet": None},

    # --- Programme adaptatif (interne) ---
    # onglet=None (17/08, bug signalé par Bourama) : ces 22 outils étaient
    # étiquetés "utilitaires" comme les 6 vrais utilitaires juste
    # au-dessus, ce qui les faisait apparaître comme boutons cliquables
    # dans le bouton Utilitaires (jusqu'à 28 entrées au total). Ce sont
    # des outils que le modèle appelle lui-même en autonomie pendant la
    # conversation (édition de programme), jamais censés être des boutons
    # que l'étudiant clique. onglet=None les garde dans ce registre (donc
    # toujours une icône propre dans la bulle "résultat d'outil", voir
    # OutilResultatBulle.tsx) sans les faire apparaître dans AUCUN menu
    # (ni Outils, ni Utilitaires) -- voir api/outils_registre.py (.get()
    # au lieu d'un accès direct) et lib/outils.ts (onglet optionnel) côté
    # frontend pour la partie qui rend ça possible sans planter.
    "lister_mes_programmes": {"label": "Liste de tes programmes", "icone": "GraduationCap", "onglet": None},
    "consulter_matiere_active": {"label": "Consultation de la matière active", "icone": "BookOpen", "onglet": None},
    "consulter_matiere_programme": {"label": "Consultation des chapitres d'une matière", "icone": "BookOpen", "onglet": None},
    "ajouter_programme": {"label": "Création d'un programme", "icone": "GraduationCap", "onglet": None},
    "modifier_programme": {"label": "Modification d'un programme", "icone": "GraduationCap", "onglet": None},
    "consulter_programme": {"label": "Consultation d'un programme", "icone": "GraduationCap", "onglet": None},
    "supprimer_programme": {"label": "Suppression d'un programme", "icone": "Trash2", "onglet": None},
    "ajouter_matiere": {"label": "Ajout d'une matière", "icone": "BookOpen", "onglet": None},
    "modifier_matiere": {"label": "Modification d'une matière", "icone": "BookOpen", "onglet": None},
    "supprimer_matiere": {"label": "Suppression d'une matière", "icone": "Trash2", "onglet": None},
    "ajouter_chapitre": {"label": "Ajout d'un chapitre", "icone": "Layers", "onglet": None},
    "modifier_chapitre": {"label": "Modification d'un chapitre", "icone": "Layers", "onglet": None},
    "consulter_chapitre_programme": {"label": "Consultation d'un chapitre", "icone": "Layers", "onglet": None},
    "supprimer_chapitre": {"label": "Suppression d'un chapitre", "icone": "Trash2", "onglet": None},
    "ajouter_document_programme": {"label": "Ajout d'un document au programme", "icone": "FileText", "onglet": None},
    "modifier_document_programme": {"label": "Modification d'un document du programme", "icone": "FileText", "onglet": None},
    "supprimer_document_programme": {"label": "Suppression d'un document du programme", "icone": "Trash2", "onglet": None},
    "ajouter_exercice_programme": {"label": "Ajout d'un exercice", "icone": "ListChecks", "onglet": None},
    "modifier_exercice_programme": {"label": "Modification d'un exercice", "icone": "ListChecks", "onglet": None},
    "supprimer_exercice_programme": {"label": "Suppression d'un exercice", "icone": "Trash2", "onglet": None},
    "ajouter_examen": {"label": "Ajout d'un examen", "icone": "ClipboardList", "onglet": None},
    "modifier_examen": {"label": "Modification d'un examen", "icone": "ClipboardList", "onglet": None},
    "consulter_examens_programme": {"label": "Consultation des examens", "icone": "ClipboardList", "onglet": None},
    "supprimer_examen": {"label": "Suppression d'un examen", "icone": "Trash2", "onglet": None},
    "annuler_derniere_modification": {"label": "Annulation de la dernière modification", "icone": "Undo2", "onglet": None},
    # Section "Notion-like" (Partie 2, lot 1/5, 20/08)
    "lister_mes_pages": {"label": "Liste de tes pages", "icone": "FileText", "onglet": None},
    "consulter_page": {"label": "Consultation d'une page", "icone": "FileText", "onglet": None},
    "ajouter_page": {"label": "Création d'une page", "icone": "FileText", "onglet": None},
    "modifier_page": {"label": "Modification d'une page", "icone": "FileText", "onglet": None},
    "supprimer_page": {"label": "Suppression d'une page", "icone": "Trash2", "onglet": None},
    "ajouter_bloc": {"label": "Ajout d'un bloc", "icone": "FileText", "onglet": None},
    "modifier_bloc": {"label": "Modification d'un bloc", "icone": "FileText", "onglet": None},
    "supprimer_bloc": {"label": "Suppression d'un bloc", "icone": "Trash2", "onglet": None},
    "gerer_comportement": {"label": "Skills personnels", "icone": "ScrollText", "onglet": None},
    # Routage en deux niveaux (22/08/2026, demande Bourama) : jamais un
    # outil que le grand LLM appelle lui-même (pas de tool MCP réel), c'est
    # le petit routeur "à la skill" (core/main.py) qui déclenche ça en
    # coulisse -- mais on veut quand même que ça s'affiche comme un
    # résultat d'outil normal dans le fil de conversation (voir
    # OutilResultatBulle.tsx), d'où cette entrée dans ce registre bien qu'il
    # n'existe aucun outil MCP de ce nom.
    "consulter_skills_chapitres_matiere": {"label": "Consultation des skills des chapitres", "icone": "ScrollText", "onglet": None},

    # --- Actions sur le téléphone de l'étudiant (26/08/2026) ---
    # onglet=None, même logique que les blocs "Programme adaptatif"/
    # "Bibliothèque" plus haut : outils que le modèle appelle lui-même en
    # autonomie pendant la conversation, jamais des boutons cliqués.
    "gerer_action_mobile": {"label": "Action sur ton téléphone", "icone": "Smartphone", "onglet": None},
}

