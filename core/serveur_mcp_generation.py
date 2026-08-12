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
"""

import logging

from mcp.server.mcpserver import MCPServer as FastMCP, Context

from core.generation_documents import (
    generer_pdf_depuis_markdown,
    generer_docx as _generer_docx,
    generer_xlsx as _generer_xlsx,
    generer_pptx as _generer_pptx,
)
from core.generation_code import generer_zip_depuis_fichiers
from core.generation_latex import generer_fichier_latex as _generer_fichier_latex
from core.generation_archives import generer_bundle as _generer_bundle
from core.generation_donnees import exporter_donnees as _exporter_donnees
from core.generation_signature import (
    envoyer_pour_signature as _envoyer_pour_signature,
    statut_signature as _statut_signature,
    signature_disponible,
)
from core.generation_audio import generer_audio as _generer_audio, audio_disponible
from core.generation_video import (
    lancer_generation_video as _lancer_generation_video,
    statut_video as _statut_video,
    video_disponible,
)
from core.generation_3d import (
    lancer_generation_3d as _lancer_generation_3d,
    statut_modele_3d as _statut_modele_3d,
    modele_3d_disponible,
)
from core.generation_images import generer_image as _generer_image, image_generation_disponible
from core.calcul_symbolique import calculer_symbolique as _calculer_symbolique, ErreurCalculSymbolique
from core.notifications_push import (
    planifier_rappel as _planifier_rappel,
    notifications_push_disponible,
)
from api.roles import (
    resoudre_destinataire_autorise as _resoudre_destinataire_autorise,
    _inserer_message,
)
from core.generation_site import (
    deployer_site as _deployer_site,
    site_deploiement_disponible,
)
from core.bibliotheque_fichiers import chercher_fichiers as _chercher_fichiers
from core.bibliotheque_rag import chercher_bibliotheque as _chercher_bibliotheque

mcp_generation = FastMCP(name="generation")


@mcp_generation.tool()
def generer_document(titre: str, contenu_markdown: str) -> str:
    """
    Génère un document PDF à partir d'un titre et d'un contenu au format
    markdown (titres, listes, tableaux, blocs de code supportés).
    Renvoie l'URL publique du PDF généré, prête à être partagée à
    l'étudiant.
    """
    try:
        return generer_pdf_depuis_markdown(titre, contenu_markdown)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du document a échoué, réessaie."


def _formater_resultat_document(resultat: dict) -> str:
    """
    Met en forme {"url": ..., "url_apercu": ...} en texte pour l'agent.
    url_apercu peut être None (CLOUDCONVERT_API_KEY absente, ou
    conversion échouée) -- dans ce cas on ne mentionne que le fichier
    original, pas d'aperçu à proposer.
    """
    if resultat.get("url_apercu"):
        return f"Document généré : {resultat['url']} (aperçu visuel : {resultat['url_apercu']})"
    return f"Document généré : {resultat['url']}"


@mcp_generation.tool()
def generer_document_word(titre: str, contenu_markdown: str) -> str:
    """
    Génère un document Word (.docx) à partir d'un titre et d'un contenu
    markdown (titres #/##/### et paragraphes supportés, pas de mise en
    forme avancée). Renvoie l'URL publique du fichier, et si possible un
    aperçu PDF prêt à afficher directement dans le chat.
    """
    try:
        return _formater_resultat_document(_generer_docx(titre, contenu_markdown))
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du document Word a échoué, réessaie."


@mcp_generation.tool()
def generer_document_excel(titre: str, en_tetes: list, lignes: list) -> str:
    """
    Génère un classeur Excel (.xlsx) à une feuille. `en_tetes` : liste
    de noms de colonnes, ex. ["Nom", "Note"]. `lignes` : liste de
    listes de valeurs, une sous-liste par ligne, ex.
    [["Awa", 15], ["Ibrahim", 12]]. Renvoie l'URL publique du fichier,
    et si possible un aperçu PDF prêt à afficher directement dans le
    chat.
    """
    try:
        return _formater_resultat_document(_generer_xlsx(titre, en_tetes, lignes))
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du classeur Excel a échoué, réessaie."


@mcp_generation.tool()
def generer_document_powerpoint(titre: str, diapositives: list) -> str:
    """
    Génère une présentation PowerPoint (.pptx). `diapositives` : liste
    de dicts {"titre": ..., "contenu": ...}, une diapositive titre+texte
    par élément (en plus d'une diapositive de titre générée
    automatiquement à partir de `titre`). Renvoie l'URL publique du
    fichier, et si possible un aperçu PDF prêt à afficher directement
    dans le chat.
    """
    try:
        return _formater_resultat_document(_generer_pptx(titre, diapositives))
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération de la présentation PowerPoint a échoué, réessaie."


@mcp_generation.tool()
def generer_code(nom_projet: str, fichiers: dict) -> str:
    """
    Génère un fichier de code téléchargeable à partir d'un ou plusieurs
    fichiers. `fichiers` est un dictionnaire {chemin: contenu}, ex.
    {"main.py": "print('hello')"}. Un seul fichier -> renvoie directement
    ce fichier (pas de zip). Plusieurs fichiers -> archive .zip. Renvoie
    l'URL publique du fichier ou de l'archive.
    """
    try:
        return generer_zip_depuis_fichiers(nom_projet, fichiers)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du fichier a échoué, réessaie."


@mcp_generation.tool()
def generer_document_latex(titre: str, contenu_latex: str) -> str:
    """
    Génère un fichier LaTeX (.tex) téléchargeable -- distinct de
    l'affichage à l'écran (les formules $...$/$$...$$ rendues en KaTeX
    dans le chat) : ici un vrai fichier source réutilisable dans
    Overleaf ou un éditeur LaTeX. `contenu_latex` : le corps du document
    (formules, texte, éventuellement \\ce{...} pour la chimie), OU un
    document complet si tu inclus toi-même \\documentclass (sinon un
    préambule standard "article" avec amsmath/amssymb/mhchem est ajouté
    automatiquement). Renvoie l'URL publique du fichier .tex.
    """
    try:
        return f"Fichier LaTeX généré : {_generer_fichier_latex(titre, contenu_latex)}"
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du fichier LaTeX a échoué, réessaie."


# Toujours actif, comme generer_image : SymPy est une dependance Python
# locale (voir requirements.txt), aucune cle API, aucun service tiers.
# Complementaire de wolfram (registre_outils.py, categorie 2) : celui-ci
# fait le calcul formel EXACT (simplifier/resoudre/deriver/integrer/
# developper/factoriser/limite), jamais de connaissance factuelle du
# monde reel -- ca reste le role de wolfram. Les descriptions des deux
# outils doivent rester precises pour eviter que le modele hesite entre
# les deux sur une meme question.
@mcp_generation.tool()
def calculer_symbolique(
    operation: str,
    expression: str,
    variable: str = "x",
    ordre: int = 1,
    borne_inf: str = None,
    borne_sup: str = None,
    point: str = None,
) -> str:
    """
    Calcul symbolique EXACT (pas une approximation) : simplifier,
    developper, factoriser, deriver, integrer, resoudre une equation,
    ou calculer une limite. Jamais pour une connaissance factuelle du
    monde reel (constantes physiques, chimie...) -- utilise wolfram pour
    ca. A utiliser des qu'un calcul a plusieurs etapes ou des nombres
    peu communs, PAS pour un calcul mental trivial que tu peux faire
    seul avec certitude (ex: derivee de x^2).

    `operation` : une des valeurs suivantes.
    - "simplifier" : simplifie une expression.
    - "developper" : developpe une expression (distributivite).
    - "factoriser" : factorise une expression.
    - "deriver" : derive `expression` par rapport a `variable`, a
      l'ordre `ordre` (1 par defaut).
    - "integrer" : primitive de `expression` (ajoute "+ C"), ou
      integrale definie si `borne_inf` ET `borne_sup` sont fournies.
    - "resoudre" : resout une equation. `expression` peut contenir un
      "=" (ex: "2x + 3 = 7") ou etre une expression seule supposee
      egale a zero (ex: "x^2 - 4").
    - "limite" : limite de `expression` en `variable` -> `point`.
      `point` accepte "oo" ou "-oo" pour l'infini.

    `expression` : notation naturelle acceptee (ex: "2x^2 + 3x - 5",
    "sin(x)*cos(x)"), pas besoin de syntaxe Python stricte.

    Renvoie le resultat en LaTeX (rendu automatiquement dans le chat,
    entoure de $$...$$) suivi de sa forme texte brute.
    """
    try:
        resultat = _calculer_symbolique(
            operation, expression, variable, ordre, borne_inf, borne_sup, point
        )
        return f"$${resultat['latex']}$$\n(texte : {resultat['texte']})"
    except ErreurCalculSymbolique as e:
        return f"Erreur : {e}"
    except Exception as e:
        logging.error(f"ERREUR outil calcul_symbolique : {e}")
        return "Erreur : le calcul a échoué, vérifie l'expression."


@mcp_generation.tool()
def chercher_fichier(recherche: str, agent_id: str = None, user_id: str = None) -> str:
    """
    Cherche un fichier déjà uploadé (image, PDF, audio, vidéo, autre)
    dans la bibliothèque -- uploadé soit par la plateforme (accessible à
    tous les agents), soit par le créateur de CET agent, soit par CET
    utilisateur lui-même dans une conversation passée. `recherche` est un
    mot-clé (nom de fichier ou sujet). `agent_id` et `user_id` doivent
    être exactement ceux donnés dans tes instructions système, pas
    inventés. Renvoie la liste des fichiers trouvés (nom, url, niveau)
    ou un message si rien n'est trouvé -- à toi ensuite d'inclure le
    lien dans ta réponse (![...](url) pour une image, [...](url) sinon).
    """
    try:
        resultats = _chercher_fichiers(recherche, agent_id=agent_id, user_id=user_id)
    except Exception:
        return "Erreur : la recherche de fichier a échoué, réessaie."

    if not resultats:
        return "Aucun fichier trouvé pour cette recherche."

    return "\n".join(
        f"- {f['nom_fichier']} ({f['niveau']}) : {f['url_publique']}"
        + (f" -- {f['description']}" if f.get("description") else "")
        for f in resultats
    )


@mcp_generation.tool()
def consulter_bibliotheque(question: str, user_id: str = None) -> str:
    """
    Cherche dans la bibliothèque personnelle de documents PDF de CET
    utilisateur (voir "Mon espace" côté app) les passages les plus
    pertinents pour répondre à `question`, quel que soit l'agent avec
    qui la conversation a lieu -- cette bibliothèque n'appartient à
    aucun agent en particulier, elle est propre à l'utilisateur, et
    invisible pour tout autre utilisateur. `user_id` doit être
    exactement celui donné dans tes instructions système, pas inventé.
    Renvoie les extraits trouvés (à utiliser directement pour répondre)
    ou un message si rien de pertinent n'a été trouvé.
    """
    if not user_id:
        return "Aucune bibliothèque disponible : utilisateur non connecté."

    try:
        resultats = _chercher_bibliotheque(question, user_id=user_id)
    except Exception:
        return "Erreur : la recherche dans la bibliothèque a échoué, réessaie."

    if not resultats:
        return "Rien de pertinent trouvé dans la bibliothèque pour cette question."

    return "\n\n---\n\n".join(r["contenu"] for r in resultats)


@mcp_generation.tool()
def generer_site_zip(nom_projet: str, fichiers: dict) -> str:
    """
    Génère une archive .zip téléchargeable d'un site web statique
    (HTML/CSS/JS). `fichiers` est un dictionnaire {chemin: contenu}, ex.
    {"index.html": "<html>...</html>", "style.css": "body {...}"}.
    À utiliser quand l'utilisateur veut le code source pour l'héberger
    lui-même ailleurs, plutôt qu'un lien en ligne (voir deployer_site
    pour ce second cas). Un seul fichier -> renvoyé directement (pas de
    zip) ; plusieurs -> archive .zip. Renvoie l'URL publique.
    """
    try:
        return generer_zip_depuis_fichiers(nom_projet, fichiers)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du site (zip) a échoué, réessaie."


@mcp_generation.tool()
def generer_bundle(nom_projet: str, elements: list) -> str:
    """
    Regroupe plusieurs fichiers hétérogènes (déjà générés ailleurs, ou
    fournis en brut) en une seule archive .zip téléchargeable.
    `elements` est une liste de dictionnaires, chacun avec "chemin" (le
    nom du fichier dans le zip) et soit "url" (URL publique d'un fichier
    déjà généré, ex. par generer_document ou generer_code), soit
    "contenu" (texte fourni directement). Ex. :
    [{"chemin": "rapport.pdf", "url": "https://..."},
     {"chemin": "donnees.csv", "contenu": "a,b\\n1,2"}]
    Renvoie l'URL publique du .zip.
    """
    try:
        return _generer_bundle(nom_projet, elements)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du bundle a échoué, réessaie."


@mcp_generation.tool()
def exporter_donnees(nom: str, donnees: dict, format: str = "json") -> str:
    """
    Exporte des données structurées (un dictionnaire, potentiellement
    imbriqué) vers un fichier JSON ou XML téléchargeable. `format` doit
    valoir "json" ou "xml". Renvoie l'URL publique du fichier généré.
    """
    try:
        return _exporter_donnees(nom, donnees, format)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : l'export des données a échoué, réessaie."


# Enregistré conditionnellement, gate par FAL_KEY (MEME cle que la
# video, voir generation_3d.py). Meme flux en 2 outils que la video,
# pour la meme raison (generation pas instantanee).
if modele_3d_disponible():
    @mcp_generation.tool()
    def lancer_generation_3d(prompt: str) -> str:
        """
        Lance une génération de modèle 3D (.glb) à partir d'une
        description textuelle. NE renvoie PAS le modèle immédiatement :
        renvoie un identifiant à donner à consulter_statut_3d un peu
        plus tard. Préviens l'étudiant que ça prend un peu de temps.
        """
        try:
            resultat = _lancer_generation_3d(prompt)
            return (
                f"Génération 3D lancée (id: {resultat['request_id']}). "
                f"Redemande le statut avec cet identifiant dans une minute ou deux."
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : le lancement de la génération 3D a échoué, réessaie."

    @mcp_generation.tool()
    def consulter_statut_3d(request_id: str) -> str:
        """
        Consulte l'état d'une génération 3D lancée avec
        lancer_generation_3d. Si terminée, renvoie l'URL publique du
        fichier .glb.
        """
        try:
            resultat = _statut_modele_3d(request_id)
            if resultat["statut"] == "COMPLETED":
                return f"Modèle 3D prêt : {resultat['url']}"
            return f"Toujours en cours (statut : {resultat['statut']}), redemande un peu plus tard."
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."


# Enregistré conditionnellement, gate par FAL_KEY (voir
# generation_video.py). IMPORTANT : la génération vidéo prend 1-3
# minutes, donc en 2 outils separes (lancer + consulter), jamais un
# seul outil bloquant -- l'agent doit dire a l'utilisateur de revenir
# verifier un peu plus tard, pas rester bloque a attendre.
if video_disponible():
    @mcp_generation.tool()
    def lancer_generation_video(prompt: str, duree_secondes: int = 5) -> str:
        """
        Lance une génération vidéo à partir d'une description
        textuelle. NE renvoie PAS la vidéo (elle prend 1 à 3 minutes à
        générer) : renvoie un identifiant à donner à
        consulter_statut_video un peu plus tard. Préviens l'étudiant
        que ça prend du temps et qu'il doit redemander le statut dans
        quelques minutes.
        """
        try:
            resultat = _lancer_generation_video(prompt, duree_secondes)
            return (
                f"Génération lancée (id: {resultat['request_id']}). "
                f"Ça prend 1 à 3 minutes -- redemande le statut avec cet identifiant un peu plus tard."
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : le lancement de la génération vidéo a échoué, réessaie."

    @mcp_generation.tool()
    def consulter_statut_video(request_id: str) -> str:
        """
        Consulte l'état d'une génération vidéo lancée avec
        lancer_generation_video. Si terminée, renvoie l'URL publique de
        la vidéo. Sinon, indique qu'elle est toujours en cours.
        """
        try:
            resultat = _statut_video(request_id)
            if resultat["statut"] == "COMPLETED":
                return f"Vidéo prête : {resultat['url']}"
            return f"Toujours en cours (statut : {resultat['statut']}), redemande dans une minute."
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."


# Enregistré conditionnellement, gate par interrupteur dédié (voir
# generation_audio.py : GROQ_API_KEY existe déjà pour le chat, donc ne
# peut pas servir de gate ici -- il faut qu'AUDIO_TTS_ACTIF="true" soit
# mis explicitement par Bourama).
if audio_disponible():
    @mcp_generation.tool()
    def generer_audio(texte: str, voix: str = "austin") -> str:
        """
        Convertit du texte en audio parlé (voix naturelle). Le texte
        peut inclure des indications vocales entre crochets, ex.
        "[cheerful] Bienvenue !". Renvoie l'URL publique du fichier
        audio généré.
        """
        try:
            return _generer_audio(texte, voix)
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : la génération audio a échoué, réessaie."


# Enregistré conditionnellement, même logique que generer_image ci-dessous :
# LUMIN_API_KEY absente -> l'agent ne voit tout simplement pas ces outils.
if signature_disponible():
    @mcp_generation.tool()
    def envoyer_pour_signature(titre: str, contenu_markdown: str, signataires: list) -> str:
        """
        Génère un document PDF à partir d'un contenu markdown et
        l'envoie pour signature électronique (via Lumin) à un ou
        plusieurs signataires. `signataires` : liste de
        {"nom": ..., "email": ...}. Chaque signataire reçoit un email
        avec un lien pour signer. Renvoie l'identifiant de la demande
        de signature et son statut.
        """
        try:
            resultat = _envoyer_pour_signature(titre, contenu_markdown, signataires)
            return (
                f"Demande de signature envoyée (id: {resultat['signature_request_id']}, "
                f"statut: {resultat['statut']}). Document : {resultat['url_document']}"
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : l'envoi pour signature a échoué, réessaie."

    @mcp_generation.tool()
    def consulter_statut_signature(signature_request_id: str) -> str:
        """
        Consulte l'état d'une demande de signature déjà envoyée
        (en attente, signé, expiré...).
        """
        try:
            return str(_statut_signature(signature_request_id))
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."


# Toujours actif : Pollinations (gratuit, sans clé) par défaut, bascule
# automatique vers Together AI (payant, meilleure qualité) si
# TOGETHER_API_KEY est configurée -- voir generation_images.py. Plus de
# condition ici, contrairement à la signature/audio/vidéo/3D qui, eux,
# n'ont pas d'équivalent gratuit connu.
@mcp_generation.tool()
def generer_image(prompt: str) -> str:
    """
    Génère une image à partir d'une description textuelle. Renvoie
    l'URL publique de l'image générée.
    """
    try:
        return _generer_image(prompt)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération de l'image a échoué, réessaie."


# Enregistré conditionnellement, gate par VERCEL_API_TOKEN (voir
# generation_site.py). generer_site_zip (juste au-dessus, non
# conditionnel) reste toujours disponible pour le cas "code seul" :
# seul ce second outil, le déploiement en ligne, dépend de la clé.
if site_deploiement_disponible():
    @mcp_generation.tool()
    def deployer_site(nom_projet: str, fichiers: dict) -> str:
        """
        Déploie un site web statique (HTML/CSS/JS) en ligne sur Vercel
        et renvoie l'URL publique directement utilisable. À utiliser
        quand l'utilisateur veut un lien en ligne plutôt que le code
        source (voir generer_site_zip pour ce second cas). `fichiers`
        est un dictionnaire {chemin: contenu}, ex.
        {"index.html": "<html>...</html>"}.
        """
        try:
            return _deployer_site(nom_projet, fichiers)
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : le déploiement du site a échoué, réessaie."


# Enregistré conditionnellement, gate par les clés VAPID (voir
# notifications_push.py). Outil de ce fichier qui a besoin de connaître
# l'identité de l'appelant (user_id/agent_id) : récupérés via
# ctx.request_context.request.query_params, transmis dans l'URL par
# _url_generation() (registre_outils.py) -- même mécanique reprise par
# envoyer_message ci-dessous. NON TESTÉ EN CONDITIONS RÉELLES : si ça
# échoue au premier essai, vérifier en premier que request_context.request
# est bien accessible dans ce mode (stateless_http) -- c'est le point
# d'incertitude documenté ici.
if notifications_push_disponible():
    @mcp_generation.tool()
    def planifier_rappel(contenu: str, dans_minutes: int, ctx: Context) -> str:
        """
        Planifie une notification push à envoyer à l'utilisateur après
        un délai donné (ex: "préviens-moi dans 3 jours de réviser").
        `contenu` est le texte de la notification, `dans_minutes` le
        délai en minutes (ex: 4320 pour 3 jours).
        """
        try:
            requete = ctx.request_context.request
            user_id = requete.query_params.get("user_id")
            agent_id = requete.query_params.get("agent_id")
            if not user_id:
                return "Erreur : impossible d'identifier l'utilisateur pour ce rappel."
            _planifier_rappel(user_id, agent_id, contenu, dans_minutes)
            return f"Rappel programmé dans {dans_minutes} minutes."
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : la planification du rappel a échoué, réessaie."


# Ajouté le 2026-08-04 (demande Bourama, hiérarchie de rôles) : contrairement
# aux autres outils de ce fichier, PAS de gate conditionnel -- toujours
# enregistré, car actif d'office sur les IA auto-créées à choix de rôle
# (voir _creer_agent_minimal dans api/roles.py) et sans dépendance à une
# clé API externe. Renvoie toujours du texte (jamais de génération de
# fichier), donc PAS ajouté à OUTILS_AUTONOMES (registre_outils.py) : le
# round-trip vers le modèle reste nécessaire pour qu'il relaie une erreur
# de destinataire ambigu/introuvable à l'utilisateur.
#
# TESTÉ le 2026-08-04 (tâche E) : voir test_envoyer_message_manuel.py,
# qui construit une vraie requête Starlette (même mécanisme que
# mcp/server/_streamable_http_modern.py) pour confirmer que
# ctx.request_context.request.query_params est bien accessible en mode
# stateless_http -- c'était le point d'incertitude non vérifié documenté
# ici avant cette date (voir aussi planifier_rappel juste au-dessus, qui
# repose sur le même mécanisme et reste à couvrir de la même façon).
@mcp_generation.tool()
def envoyer_message(nom_destinataire: str, contenu: str, ctx: Context) -> str:
    """
    Envoie un message direct à une personne autorisée de ta hiérarchie
    (ton établissement, ton enseignant, tes étudiants, ou un autre
    étudiant de ton établissement selon ton rôle). `nom_destinataire`
    est le nom affiché de la personne à qui écrire, `contenu` le texte
    du message.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        if not user_id:
            return "Erreur : impossible d'identifier l'expéditeur pour ce message."
        if not contenu.strip():
            return "Erreur : le message est vide."

        destinataire_id, erreur = _resoudre_destinataire_autorise(user_id, nom_destinataire)
        if erreur:
            return erreur

        _inserer_message(user_id, destinataire_id, contenu)
        return f"Message envoyé à {nom_destinataire}."
    except Exception as e:
        logging.error(f"ERREUR outil generation (envoyer_message) : {e}")
        return "Erreur : l'envoi du message a échoué, réessaie."
