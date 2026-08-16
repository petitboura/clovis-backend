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

import os
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
from core.comportements_etudiants import (
    obtenir_comportement_skill as _obtenir_comportement_skill,
    ajouter_comportement as _ajouter_comportement,
    modifier_comportement as _modifier_comportement,
    supprimer_comportement as _supprimer_comportement,
)
from core.programme_llm import obtenir_structure_programme as _obtenir_structure_programme
from core.programme_llm import obtenir_chapitres_matiere as _obtenir_chapitres_matiere
from core.programme_llm import obtenir_contenu_chapitre as _obtenir_contenu_chapitre
from core.programme_llm import obtenir_examens_programme as _obtenir_examens_programme
from core.programme_ecriture import (
    ajouter_programme as _ajouter_programme,
    modifier_programme as _modifier_programme,
    ajouter_matiere as _ajouter_matiere,
    modifier_matiere as _modifier_matiere,
    ajouter_chapitre as _ajouter_chapitre,
    modifier_chapitre as _modifier_chapitre,
    ajouter_document as _ajouter_document,
    modifier_document as _modifier_document,
    ajouter_exercice as _ajouter_exercice_programme,
    modifier_exercice as _modifier_exercice_programme,
    ajouter_examen as _ajouter_examen,
    modifier_examen as _modifier_examen,
    supprimer_programme as _supprimer_programme,
    supprimer_matiere as _supprimer_matiere,
    supprimer_chapitre as _supprimer_chapitre,
    supprimer_document as _supprimer_document,
    supprimer_exercice as _supprimer_exercice_programme,
    supprimer_examen as _supprimer_examen,
    annuler_derniere_modification as _annuler_derniere_modification,
)
from core.codes_partage import obtenir_comportement_skill_recu as _obtenir_comportement_skill_recu
from core.generation_site import (
    deployer_site as _deployer_site,
    site_deploiement_disponible,
)
from core.bibliotheque_fichiers import chercher_fichiers as _chercher_fichiers
from core.bibliotheque_rag import chercher_bibliotheque as _chercher_bibliotheque

# Clovis (12/08) : memoire/profil/RAG/matiere ne sont plus pre-fetches et
# injectes systematiquement dans le system prompt (voir core/main.py,
# _construire_system_prompt) -- ce sont maintenant des outils que le
# modele appelle lui-meme s'il juge pertinent, au meme titre que les
# outils generation/bibliotheque ci-dessus.
import json
from supabase import create_client
from retriever import chercher_candidats as _chercher_candidats
from contenu_dynamique_matiere import resoudre_system_prompt as _resoudre_system_prompt_matiere

_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
_supabase_memoire = create_client(_SUPABASE_URL, _SUPABASE_SECRET)

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
def consulter_bibliotheque(question: str, ctx: Context) -> str:
    """
    Cherche dans la bibliothèque personnelle de documents PDF de CET
    utilisateur (voir "Mon espace" côté app) les passages les plus
    pertinents pour répondre à `question`, quel que soit l'agent avec
    qui la conversation a lieu -- cette bibliothèque n'appartient à
    aucun agent en particulier, elle est propre à l'utilisateur, et
    invisible pour tout autre utilisateur.
    Renvoie les extraits trouvés (à utiliser directement pour répondre),
    chacun accompagné du nom et du lien de son document d'origine --
    si tu juges utile de montrer un de ces documents en entier plutôt
    que de le résumer, inclus son lien dans ta réponse (![...](url)
    pour une image, [...](url) pour les autres types), il s'affichera
    alors correctement selon son type. Renvoie un message si rien de
    pertinent n'a été trouvé.
    """
    # BUG corrigé le 14/08 (constaté en prod : "Rien de pertinent trouvé"
    # alors que la bibliothèque contenait bien des documents indexés) --
    # `user_id` était avant un paramètre texte laissé au LLM (`user_id:
    # str = None`), qui pouvait l'halluciner/inventer au lieu de recopier
    # celui de ses instructions système (constaté : un UUID totalement
    # inexistant en base). Même faille que consulter_memoire_utilisateur
    # évitait déjà : on récupère maintenant le vrai user_id authentifié
    # via ctx (query params de l'URL construite serveur-côté, voir
    # _url_generation dans registre_outils.py), jamais depuis un
    # paramètre que le modèle pourrait remplir lui-même -- ça fermait
    # aussi une fuite potentielle (un utilisateur aurait pu, en théorie,
    # demander à lire la bibliothèque d'un autre en donnant son id).
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    if not user_id:
        return "Aucune bibliothèque disponible : utilisateur non connecté."

    try:
        resultats = _chercher_bibliotheque(question, user_id=user_id)
    except Exception:
        return "Erreur : la recherche dans la bibliothèque a échoué, réessaie."

    if not resultats:
        return "Rien de pertinent trouvé dans la bibliothèque pour cette question."

    # Correction du 17/08 (Bourama : "il faut que l'IA puisse le
    # récupérer en entier aussi pour l'afficher s'il le décide, pas
    # uniquement les vecteurs") -- avant ça, seul le texte des extraits
    # était renvoyé, sans jamais dire de quel fichier ça venait. Chaque
    # extrait porte maintenant sa source (nom + lien, voir
    # recherche_bibliotheque et bibliotheque_rag.py) : à toi de choisir,
    # une fois la question répondue à partir des extraits, si montrer le
    # document original en entier apporte quelque chose -- pas besoin
    # de le relire en entier pour ça, uniquement d'inclure son lien dans
    # ta réponse (![...](url) pour une image, [...](url) pour les
    # autres types) pour qu'il s'affiche correctement selon son type.
    blocs = []
    for r in resultats:
        bloc = r["contenu"]
        if r.get("nom_fichier") and r.get("url_publique"):
            bloc += f"\n(Source : {r['nom_fichier']} -- {r['url_publique']})"
        blocs.append(bloc)

    return "\n\n---\n\n".join(blocs)


@mcp_generation.tool()
def consulter_memoire_utilisateur(ctx: Context) -> str:
    """
    Consulte ce que tu sais déjà de CET utilisateur d'une conversation à
    l'autre (mémoire long-terme structurée -- préférences, matières
    suivies, difficultés récurrentes, projets en cours, etc.).
    À utiliser au début d'une conversation si ça peut aider à mieux
    répondre, ou dès que tu sens qu'un élément de contexte passé serait
    utile. Renvoie un JSON (peut être vide si rien n'a encore été noté).
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        if not user_id:
            return "Aucune mémoire disponible : utilisateur non connecté."
        res = (
            _supabase_memoire.table("conversation_summaries")
            .select("donnees")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        donnees = (res.data or {}).get("donnees") or {}
        if not donnees:
            return "Rien en mémoire pour cet utilisateur pour l'instant."
        return json.dumps(donnees, ensure_ascii=False)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_memoire_utilisateur : {e}")
        return "Erreur : impossible de consulter la mémoire, réessaie."


@mcp_generation.tool()
def mettre_a_jour_memoire_utilisateur(champs_json: str, ctx: Context) -> str:
    """
    Note ou met à jour un ou plusieurs éléments dans la mémoire
    long-terme de CET utilisateur, à utiliser dès que tu apprends
    quelque chose d'utile à retenir pour les prochaines conversations
    (préférence, difficulté récurrente, projet en cours...). Le schéma
    est libre : garde les clés déjà utilisées si elles collent (ex.
    "profil_personnel", "preferences_pedagogiques",
    "matieres_ou_sujets", "objectifs_et_projets",
    "points_de_continuite"), ou crée-en de nouvelles si aucune ne
    convient. `champs_json` est un objet JSON, ex.
    '{"preferences_pedagogiques": {"style_explication": "avec des exemples concrets"}}'
    -- fusionné avec la mémoire existante (les clés de premier niveau
    fournies remplacent leur ancienne valeur, le reste est conservé tel
    quel). N'écris ici que ce qui a une vraie valeur à long terme, pas
    le contenu d'un seul message.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        if not user_id:
            return "Erreur : impossible d'identifier l'utilisateur pour mettre à jour la mémoire."
        try:
            patch = json.loads(champs_json)
        except Exception:
            return "Erreur : champs_json doit être un objet JSON valide."
        if not isinstance(patch, dict):
            return "Erreur : champs_json doit être un objet JSON (pas une liste ou une valeur simple)."

        res = (
            _supabase_memoire.table("conversation_summaries")
            .select("donnees")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        actuel = (res.data or {}).get("donnees") or {}
        actuel.update(patch)

        _supabase_memoire.table("conversation_summaries").upsert(
            {"user_id": user_id, "donnees": actuel}, on_conflict="user_id"
        ).execute()
        return "Mémoire mise à jour."
    except Exception as e:
        logging.error(f"ERREUR outil mettre_a_jour_memoire_utilisateur : {e}")
        return "Erreur : la mise à jour de la mémoire a échoué, réessaie."


@mcp_generation.tool()
def consulter_profil_utilisateur(ctx: Context) -> str:
    """
    Consulte le profil connu de CET utilisateur pour Clovis (données
    déjà extraites au fil des conversations : qui il est, son contexte
    scolaire, etc.). À utiliser si ça peut aider à personnaliser ta
    réponse. Renvoie un JSON (peut être vide).
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Aucun profil disponible."
        res = (
            _supabase_memoire.table("agent_user_profiles")
            .select("donnees")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .maybe_single()
            .execute()
        )
        donnees = (res.data or {}).get("donnees") or {}
        if not donnees:
            return "Rien dans le profil de cet utilisateur pour l'instant."
        return json.dumps(donnees, ensure_ascii=False)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_profil_utilisateur : {e}")
        return "Erreur : impossible de consulter le profil, réessaie."


@mcp_generation.tool()
def consulter_comportement(comportement_id: str, ctx: Context) -> str:
    """
    Lit le skill COMPLET (format Claude, frontmatter + instructions) d'une
    instruction personnelle -- que cet utilisateur l'ait écrite lui-même
    (section "Mes comportements"), ou qu'il l'ait reçue d'un autre
    utilisateur via un code (id préfixé "recu:", voir
    core/codes_partage.py) -- à partir de son id. Le message système t'a
    déjà donné une courte description de ceux qui semblent pertinents
    pour ce message -- utilise cet outil quand l'un d'eux semble
    s'appliquer, AVANT de répondre, pour lire son contenu réel plutôt que
    de deviner à partir de la description seule.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Erreur : impossible d'identifier l'étudiant ou l'agent."
        if comportement_id.startswith("recu:"):
            skill_md = _obtenir_comportement_skill_recu(user_id, comportement_id)
        else:
            skill_md = _obtenir_comportement_skill(agent_id, user_id, comportement_id)
        if skill_md is None:
            return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return skill_md
    except Exception as e:
        logging.error(f"ERREUR outil consulter_comportement : {e}")
        return "Erreur : impossible de consulter ce comportement, réessaie."


@mcp_generation.tool()
def ajouter_comportement(texte: str, ctx: Context) -> str:
    """
    Enregistre une nouvelle instruction personnelle pour CET étudiant
    (section "Mes comportements"), à utiliser SEULEMENT quand il exprime
    CLAIREMENT et EXPLICITEMENT une préférence ou une règle à retenir
    pour la suite (ex: "explique-moi toujours avec des schémas", "ne me
    donne jamais la réponse directe, guide-moi"). S'ajoute EN PLUS de ses
    autres comportements, ne les remplace pas.

    N'UTILISE JAMAIS CET OUTIL SUR UNE SUPPOSITION. Si la demande est
    vague, ambiguë, ou que tu devines seulement ce que l'étudiant
    voudrait retenir sans qu'il l'ait dit clairement, NE CRÉE RIEN --
    demande-lui d'abord de préciser ce qu'il veut que tu retiennes
    exactement. Ne crée jamais un comportement "au cas où", pour
    anticiper un besoin non exprimé, ou à partir d'une remarque en
    passant qui n'était pas une vraie demande de mémorisation. Une
    création hâtive et mal comprise est pire qu'aucune création : elle
    pollue durablement ses instructions et influence toutes ses
    conversations futures avec toi.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Erreur : impossible d'identifier l'étudiant ou l'agent."
        ligne = _ajouter_comportement(agent_id, user_id, texte)
        return f"Comportement enregistré (id {ligne['id']}) : {ligne['description']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_comportement : {e}")
        return "Erreur : impossible d'enregistrer ce comportement, réessaie."


@mcp_generation.tool()
def modifier_comportement(comportement_id: str, texte: str, ctx: Context) -> str:
    """
    Remplace le texte COMPLET d'un comportement existant de CET étudiant
    (à partir de son id, vu via consulter_comportement ou la description
    courte donnée dans le message système). Utilise cet outil quand
    l'étudiant veut corriger ou préciser une instruction déjà enregistrée
    -- pas pour en ajouter une nouvelle (voir ajouter_comportement).
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Erreur : impossible d'identifier l'étudiant ou l'agent."
        ligne = _modifier_comportement(agent_id, user_id, comportement_id, texte)
        if ligne is None:
            return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Comportement modifié : {ligne['description']}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_comportement : {e}")
        return "Erreur : impossible de modifier ce comportement, réessaie."


@mcp_generation.tool()
def supprimer_comportement(comportement_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un comportement de CET étudiant (à partir de
    son id). SENSIBLE : demande toujours confirmation à l'étudiant avant
    d'être exécuté, quelle que soit la formulation de sa demande.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Erreur : impossible d'identifier l'étudiant ou l'agent."
        ok = _supprimer_comportement(agent_id, user_id, comportement_id)
        if not ok:
            return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Comportement supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_comportement : {e}")
        return "Erreur : impossible de supprimer ce comportement, réessaie."


@mcp_generation.tool()
def consulter_programme(programme_id: str, ctx: Context) -> str:
    """
    Lit les matières (avec leurs limites de cadre officiel si
    renseignées) d'un programme que cet étudiant a créé lui-même
    (section "Programme" de son espace), à partir de son id. Ne contient
    PAS les chapitres de ces matières, ni les examens/devoirs : une fois
    que tu as choisi une matière précise dans cette liste, utilise
    consulter_matiere_programme pour voir ses chapitres ; pour les
    examens/devoirs de ce programme (qui peuvent couvrir plusieurs
    matières/chapitres à la fois), utilise consulter_examens_programme.
    Le message système t'a déjà donné la liste légère (id/niveau/nom)
    des programmes de cet étudiant -- utilise cet outil quand tu as
    besoin de savoir quelles matières existent dans un programme précis,
    plutôt que de deviner.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        structure = _obtenir_structure_programme(user_id, programme_id)
        if structure is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return structure
    except Exception as e:
        logging.error(f"ERREUR outil consulter_programme : {e}")
        return "Erreur : impossible de consulter ce programme, réessaie."


def _user_id_ou_erreur(ctx: Context) -> str | None:
    """Petit helper commun à tous les outils programme ci-dessous (pas
    besoin d'agent_id, contrairement aux comportements -- un programme
    appartient à l'utilisateur, pas à un agent précis)."""
    return ctx.request_context.request.query_params.get("user_id")


@mcp_generation.tool()
def ajouter_programme(niveau: str, ctx: Context, nom: str = "") -> str:
    """
    Crée un nouveau programme (ex: "Terminale S", "3ème") pour CET
    étudiant, dans sa section "Programme". `niveau` est le texte libre du
    niveau scolaire, `nom` un label optionnel s'il en donne un. Utilise
    cet outil quand l'étudiant veut structurer une nouvelle année/classe,
    pas pour ajouter une matière à un programme déjà existant (voir
    ajouter_matiere). N'utilise JAMAIS cet outil sur une supposition --
    si tu n'es pas sûr que l'étudiant veut vraiment créer un nouveau
    programme (plutôt que, par exemple, ajouter une matière à un
    programme existant), demande-lui de préciser avant d'agir.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _ajouter_programme(user_id, niveau, nom or None)
        return f"Programme créé (id {ligne['id']}) : {ligne['niveau']}" + (f" — {ligne['nom']}" if ligne.get("nom") else "")
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_programme : {e}")
        return "Erreur : impossible de créer ce programme, réessaie."


@mcp_generation.tool()
def modifier_programme(programme_id: str, ctx: Context, niveau: str = "", nom: str = "") -> str:
    """
    Modifie le niveau et/ou le nom d'un programme existant de CET
    étudiant. Laisse un champ vide ("") pour ne pas le changer -- ne
    touche QUE les champs fournis. Ne modifie pas les matières/chapitres
    à l'intérieur (voir modifier_matiere, modifier_chapitre).
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _modifier_programme(user_id, programme_id, niveau or None, nom if nom else None)
        if ligne is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Programme modifié : {ligne.get('niveau')}" + (f" — {ligne['nom']}" if ligne.get("nom") else "")
    except Exception as e:
        logging.error(f"ERREUR outil modifier_programme : {e}")
        return "Erreur : impossible de modifier ce programme, réessaie."


@mcp_generation.tool()
def supprimer_programme(programme_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un programme de CET étudiant, ainsi que TOUT
    son contenu (matières, chapitres, documents, exercices). SENSIBLE :
    demande toujours confirmation avant exécution, quelle que soit la
    formulation de la demande.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ok = _supprimer_programme(user_id, programme_id)
        if not ok:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Programme supprimé, avec tout son contenu."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_programme : {e}")
        return "Erreur : impossible de supprimer ce programme, réessaie."


@mcp_generation.tool()
def ajouter_matiere(programme_id: str, nom: str, ctx: Context, limites: str = "") -> str:
    """
    Ajoute une matière à un programme existant de CET étudiant (ex:
    "Mathématiques" dans son programme "Terminale S"). `limites` est une
    description optionnelle du cadre officiel (pour savoir ce qui est
    "hors programme"). N'utilise JAMAIS cet outil sur une supposition --
    si l'étudiant n'a pas clairement demandé d'ajouter CETTE matière à
    CE programme, demande-lui de confirmer avant d'agir.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _ajouter_matiere(user_id, programme_id, nom, limites or None)
        if ligne is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Matière ajoutée (id {ligne['id']}) : {ligne['nom']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_matiere : {e}")
        return "Erreur : impossible d'ajouter cette matière, réessaie."


@mcp_generation.tool()
def modifier_matiere(matiere_id: str, ctx: Context, nom: str = "", limites: str = "") -> str:
    """
    Modifie le nom et/ou les limites de cadre officiel d'une matière
    existante de CET étudiant. Laisse un champ vide ("") pour ne pas le
    changer.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _modifier_matiere(user_id, matiere_id, nom or None, limites if limites else None)
        if ligne is None:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Matière modifiée : {ligne.get('nom')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_matiere : {e}")
        return "Erreur : impossible de modifier cette matière, réessaie."


@mcp_generation.tool()
def supprimer_matiere(matiere_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT une matière de CET étudiant, avec tous ses
    chapitres/documents/exercices. SENSIBLE : demande toujours
    confirmation avant exécution.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ok = _supprimer_matiere(user_id, matiere_id)
        if not ok:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Matière supprimée, avec tout son contenu."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_matiere : {e}")
        return "Erreur : impossible de supprimer cette matière, réessaie."


@mcp_generation.tool()
def ajouter_chapitre(matiere_id: str, nom: str, ctx: Context, ordre: int = 0, limites: str = "") -> str:
    """
    Ajoute un chapitre à une matière existante de CET étudiant. `ordre`
    contrôle sa position d'affichage (0 = premier). `limites` est une
    description optionnelle du cadre officiel pour ce chapitre.
    N'utilise JAMAIS cet outil sur une supposition -- si l'étudiant n'a
    pas clairement demandé d'ajouter CE chapitre à CETTE matière,
    demande-lui de confirmer avant d'agir.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _ajouter_chapitre(user_id, matiere_id, nom, ordre, limites or None)
        if ligne is None:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Chapitre ajouté (id {ligne['id']}) : {ligne['nom']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_chapitre : {e}")
        return "Erreur : impossible d'ajouter ce chapitre, réessaie."


@mcp_generation.tool()
def modifier_chapitre(chapitre_id: str, ctx: Context, nom: str = "", ordre: int = -1, limites: str = "") -> str:
    """
    Modifie le nom, l'ordre d'affichage et/ou les limites d'un chapitre
    existant de CET étudiant. Laisse `nom`/`limites` vides ("") et
    `ordre` à -1 pour ne pas changer le champ correspondant.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _modifier_chapitre(user_id, chapitre_id, nom or None, ordre if ordre >= 0 else None, limites if limites else None)
        if ligne is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Chapitre modifié : {ligne.get('nom')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_chapitre : {e}")
        return "Erreur : impossible de modifier ce chapitre, réessaie."


@mcp_generation.tool()
def supprimer_chapitre(chapitre_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un chapitre de CET étudiant, avec ses
    documents/exercices. SENSIBLE : demande toujours confirmation avant
    exécution.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ok = _supprimer_chapitre(user_id, chapitre_id)
        if not ok:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Chapitre supprimé, avec son contenu."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_chapitre : {e}")
        return "Erreur : impossible de supprimer ce chapitre, réessaie."


@mcp_generation.tool()
def ajouter_document_programme(chapitre_id: str, titre: str, url_ou_contenu: str, ctx: Context) -> str:
    """
    Ajoute un document à un chapitre du programme de CET étudiant :
    `url_ou_contenu` est SOIT un lien (ex: une URL de cours en ligne),
    SOIT un texte direct (ex: un résumé de cours écrit dans le message).
    Pour un fichier déjà uploadé par l'étudiant dans le chat, cherche
    d'abord son URL avec chercher_fichier avant d'appeler cet outil.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _ajouter_document(user_id, chapitre_id, titre, url_ou_contenu)
        if ligne is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Document ajouté (id {ligne['id']}) : {ligne['titre']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_document_programme : {e}")
        return "Erreur : impossible d'ajouter ce document, réessaie."


@mcp_generation.tool()
def modifier_document_programme(document_id: str, ctx: Context, titre: str = "", url_ou_contenu: str = "") -> str:
    """
    Modifie le titre et/ou le contenu (texte ou lien) d'un document
    existant du programme de CET étudiant. Laisse un champ vide ("")
    pour ne pas le changer.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _modifier_document(user_id, document_id, titre or None, url_ou_contenu or None)
        if ligne is None:
            return "Ce document est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Document modifié : {ligne.get('titre')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_document_programme : {e}")
        return "Erreur : impossible de modifier ce document, réessaie."


@mcp_generation.tool()
def supprimer_document_programme(document_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un document du programme de CET étudiant.
    SENSIBLE : demande toujours confirmation avant exécution.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ok = _supprimer_document(user_id, document_id)
        if not ok:
            return "Ce document est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Document supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_document_programme : {e}")
        return "Erreur : impossible de supprimer ce document, réessaie."


@mcp_generation.tool()
def ajouter_exercice_programme(chapitre_id: str, enonce: str, ctx: Context) -> str:
    """
    Ajoute un exercice (rattaché à UN SEUL chapitre) au programme de CET
    étudiant. Pour un exercice/devoir couvrant PLUSIEURS chapitres,
    utilise ajouter_examen à la place.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _ajouter_exercice_programme(user_id, chapitre_id, enonce)
        if ligne is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return f"Exercice ajouté (id {ligne['id']})."
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_exercice_programme : {e}")
        return "Erreur : impossible d'ajouter cet exercice, réessaie."


@mcp_generation.tool()
def modifier_exercice_programme(exercice_id: str, enonce: str, ctx: Context) -> str:
    """
    Remplace l'énoncé COMPLET d'un exercice existant du programme de CET
    étudiant.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ligne = _modifier_exercice_programme(user_id, exercice_id, enonce)
        if ligne is None:
            return "Cet exercice est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Exercice modifié."
    except Exception as e:
        logging.error(f"ERREUR outil modifier_exercice_programme : {e}")
        return "Erreur : impossible de modifier cet exercice, réessaie."


@mcp_generation.tool()
def supprimer_exercice_programme(exercice_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un exercice du programme de CET étudiant.
    SENSIBLE : demande toujours confirmation avant exécution.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ok = _supprimer_exercice_programme(user_id, exercice_id)
        if not ok:
            return "Cet exercice est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Exercice supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_exercice_programme : {e}")
        return "Erreur : impossible de supprimer cet exercice, réessaie."


@mcp_generation.tool()
def ajouter_examen(titre: str, type: str, chapitre_ids: list[str], ctx: Context) -> str:
    """
    Crée un examen/devoir/problème composite pour CET étudiant, couvrant
    UN OU PLUSIEURS chapitres (potentiellement de matières différentes,
    dans le même programme). `type` doit valoir "examen", "devoir" ou
    "probleme_composite". `chapitre_ids` est la liste des ids de
    chapitres concernés -- tous doivent appartenir à cet étudiant.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        if type not in ("examen", "devoir", "probleme_composite"):
            return 'Erreur : `type` doit valoir "examen", "devoir" ou "probleme_composite".'
        ligne = _ajouter_examen(user_id, titre, type, chapitre_ids)
        if ligne is None:
            return "Un ou plusieurs chapitres sont introuvables, ou ne correspondent pas à cet étudiant."
        return f"Examen créé (id {ligne['id']}) : {ligne['titre']} ({len(chapitre_ids)} chapitre(s))."
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_examen : {e}")
        return "Erreur : impossible de créer cet examen, réessaie."


@mcp_generation.tool()
def modifier_examen(examen_id: str, ctx: Context, titre: str = "", type: str = "", chapitre_ids: list[str] | None = None) -> str:
    """
    Modifie le titre, le type et/ou la liste des chapitres couverts d'un
    examen existant de CET étudiant. Laisse `titre`/`type` vides ("") et
    `chapitre_ids` non fourni pour ne pas changer le champ correspondant
    -- fournir `chapitre_ids` REMPLACE la liste entière, pas un ajout.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        if type and type not in ("examen", "devoir", "probleme_composite"):
            return 'Erreur : `type` doit valoir "examen", "devoir" ou "probleme_composite".'
        ligne = _modifier_examen(user_id, examen_id, titre or None, type or None, chapitre_ids)
        if ligne is None:
            return "Cet examen est introuvable, ou un chapitre fourni ne correspond pas à cet étudiant."
        return f"Examen modifié : {ligne.get('titre')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_examen : {e}")
        return "Erreur : impossible de modifier cet examen, réessaie."


@mcp_generation.tool()
def supprimer_examen(examen_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un examen/devoir/problème composite de CET
    étudiant (ne supprime PAS les chapitres qu'il couvrait, juste
    l'examen lui-même). SENSIBLE : demande toujours confirmation avant
    exécution.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        ok = _supprimer_examen(user_id, examen_id)
        if not ok:
            return "Cet examen est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return "Examen supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_examen : {e}")
        return "Erreur : impossible de supprimer cet examen, réessaie."


@mcp_generation.tool()
def annuler_derniere_modification(ctx: Context) -> str:
    """
    Annule le DERNIER ajout ou la dernière modification faite par toi
    (via ajouter_programme, modifier_matiere, ajouter_chapitre,
    ajouter_comportement, etc.) pour CET étudiant -- ne concerne PAS les
    suppressions, qui demandent déjà une confirmation avant d'être
    exécutées. À utiliser quand l'étudiant dit explicitement vouloir
    annuler/revenir en arrière sur ta dernière écriture.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        resultat = _annuler_derniere_modification(user_id)
        if resultat is None:
            return "Rien à annuler : aucune modification récente trouvée."
        return f"Dernière modification annulée ({resultat['type_cible']}, action initiale : {resultat['action']})."
    except Exception as e:
        logging.error(f"ERREUR outil annuler_derniere_modification : {e}")
        return "Erreur : impossible d'annuler, réessaie."


@mcp_generation.tool()
def consulter_matiere_programme(matiere_id: str, ctx: Context) -> str:
    """
    Lit les chapitres (avec leurs limites de cadre officiel si
    renseignées) d'UNE matière précise d'un programme de cet étudiant, à
    partir de son id. Ne contient PAS le contenu des chapitres
    (documents/exercices) : une fois que tu as choisi un chapitre précis
    dans cette liste, utilise consulter_chapitre_programme. Utilise cet
    outil seulement après avoir consulté consulter_programme et choisi
    la matière qui t'intéresse -- jamais à l'aveugle sans connaître l'id
    de la matière au préalable.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        chapitres = _obtenir_chapitres_matiere(user_id, matiere_id)
        if chapitres is None:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return chapitres
    except Exception as e:
        logging.error(f"ERREUR outil consulter_matiere_programme : {e}")
        return "Erreur : impossible de consulter cette matière, réessaie."


@mcp_generation.tool()
def consulter_chapitre_programme(chapitre_id: str, ctx: Context) -> str:
    """
    Lit le contenu réel (documents + exercices) d'UN chapitre précis d'un
    programme de cet étudiant, à partir de son id. Utilise cet outil
    seulement après avoir consulté consulter_matiere_programme et choisi
    le chapitre qui t'intéresse dans sa liste -- jamais à l'aveugle sans
    connaître l'id du chapitre au préalable.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        contenu = _obtenir_contenu_chapitre(user_id, chapitre_id)
        if contenu is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return contenu
    except Exception as e:
        logging.error(f"ERREUR outil consulter_chapitre_programme : {e}")
        return "Erreur : impossible de consulter ce chapitre, réessaie."


@mcp_generation.tool()
def consulter_examens_programme(programme_id: str, ctx: Context) -> str:
    """
    Lit les examens/devoirs (titre, type, chapitres couverts) d'un
    programme de cet étudiant, à partir de son id. Un examen peut couvrir
    plusieurs chapitres à la fois, c'est pourquoi il se consulte au
    niveau du programme entier et non via consulter_chapitre_programme.
    Aucun contenu/énoncé détaillé n'existe pour un examen -- seulement
    son titre, son type et les chapitres qu'il couvre.
    """
    try:
        user_id = _user_id_ou_erreur(ctx)
        if not user_id:
            return "Erreur : impossible d'identifier l'étudiant."
        texte = _obtenir_examens_programme(user_id, programme_id)
        if texte is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
        return texte
    except Exception as e:
        logging.error(f"ERREUR outil consulter_examens_programme : {e}")
        return "Erreur : impossible de consulter les examens de ce programme, réessaie."


@mcp_generation.tool()
def mettre_a_jour_profil_utilisateur(champs_json: str, ctx: Context) -> str:
    """
    Met à jour le profil de CET utilisateur dès que tu apprends une
    information utile à retenir sur qui il est (pas sur ce qu'il sait
    ou apprend -- ça, c'est la mémoire, voir mettre_a_jour_memoire_utilisateur).
    Schéma libre, mêmes règles que pour la mémoire : `champs_json` est
    un objet JSON fusionné avec le profil existant.
    """
    try:
        requete = ctx.request_context.request
        user_id = requete.query_params.get("user_id")
        agent_id = requete.query_params.get("agent_id")
        if not user_id or not agent_id:
            return "Erreur : impossible d'identifier l'utilisateur pour mettre à jour le profil."
        try:
            patch = json.loads(champs_json)
        except Exception:
            return "Erreur : champs_json doit être un objet JSON valide."
        if not isinstance(patch, dict):
            return "Erreur : champs_json doit être un objet JSON (pas une liste ou une valeur simple)."

        res = (
            _supabase_memoire.table("agent_user_profiles")
            .select("donnees")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .maybe_single()
            .execute()
        )
        actuel = (res.data or {}).get("donnees") or {}
        actuel.update(patch)

        _supabase_memoire.table("agent_user_profiles").upsert(
            {"user_id": user_id, "agent_id": agent_id, "donnees": actuel}, on_conflict="agent_id,user_id"
        ).execute()
        return "Profil mis à jour."
    except Exception as e:
        logging.error(f"ERREUR outil mettre_a_jour_profil_utilisateur : {e}")
        return "Erreur : la mise à jour du profil a échoué, réessaie."


@mcp_generation.tool()
def chercher_dans_base_connaissances(question: str, ctx: Context) -> str:
    """
    Cherche dans la base de connaissances de l'agent (documents et
    instructions spécifiques ajoutés par l'équipe Clovis) les passages
    pertinents pour répondre à `question`. À utiliser quand la question
    touche un sujet précis où un contenu de référence a pu être préparé
    à l'avance -- pas systématique, seulement si pertinent. Renvoie les
    extraits trouvés ou un message si rien de pertinent.
    """
    try:
        requete = ctx.request_context.request
        agent_id = requete.query_params.get("agent_id")
        candidats = _chercher_candidats(question, agent_id=agent_id)
        morceaux = [c["contenu"] for c in candidats.get("prompts", [])] + [
            c["contenu"] for c in candidats.get("documents", [])
        ]
        if not morceaux:
            return "Rien de pertinent trouvé dans la base de connaissances pour cette question."
        return "\n\n---\n\n".join(morceaux)
    except Exception as e:
        logging.error(f"ERREUR outil chercher_dans_base_connaissances : {e}")
        return "Erreur : la recherche a échoué, réessaie."


@mcp_generation.tool()
def consulter_matiere_active(message_utilisateur: str, ctx: Context) -> str:
    """
    Consulte le contenu pédagogique spécifique (cours, consignes d'un
    enseignant) débloqué par CET utilisateur pour la matière la plus
    pertinente par rapport à `message_utilisateur` (le message en cours
    de l'utilisateur, tel quel). À utiliser si la question ressemble à
    une question de cours et que l'utilisateur a pu débloquer une
    matière avec un code. Ce contenu est un COMPLÉMENT à tes
    instructions habituelles, pas un remplacement -- utilise-le comme
    référence, pas comme un bloc à recopier tel quel. Peut renvoyer un
    message générique si aucune matière n'est débloquée.
    """
    try:
        requete = ctx.request_context.request
        agent_id = requete.query_params.get("agent_id")
        user_id = requete.query_params.get("user_id")
        return _resoudre_system_prompt_matiere(message_utilisateur, agent_id, user_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_matiere_active : {e}")
        return "Erreur : impossible de consulter le contenu de la matière, réessaie."


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
# fichier) : le round-trip vers le modèle (standard pour tous les outils
# depuis le 15/08, voir registre_outils.py) reste de toute façon
# nécessaire pour qu'il relaie une erreur de destinataire ambigu/introuvable
# à l'utilisateur.
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
