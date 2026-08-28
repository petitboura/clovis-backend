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
import tempfile
import base64
import requests

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
    un_canal_push_disponible,
)
from core.actions_appareil_mobile import creer_action as _creer_action_mobile
from core.dossiers_designes_mobile import lire_dossiers_designes as _lire_dossiers_designes
from api.roles import (
    resoudre_destinataire_autorise as _resoudre_destinataire_autorise,
    _inserer_message,
)
from core.comportements_etudiants import (
    obtenir_comportement_skill as _obtenir_comportement_skill,
    lister_comportements as _lister_comportements,
    ajouter_comportement as _ajouter_comportement,
    modifier_comportement as _modifier_comportement,
    supprimer_comportement as _supprimer_comportement,
)
from core.programme_llm import obtenir_structure_programme as _obtenir_structure_programme
from core.programme_llm import obtenir_chapitres_matiere as _obtenir_chapitres_matiere
from core.programme_llm import obtenir_contenu_chapitre as _obtenir_contenu_chapitre
from core.programme_llm import obtenir_examens_programme as _obtenir_examens_programme
from core.programme_llm import lister_mes_programmes_legers as _lister_mes_programmes_legers
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
from core.codes_partage import (
    obtenir_comportement_skill_recu as _obtenir_comportement_skill_recu,
    propager_fichier_bibliotheque as _propager_fichier_bibliotheque,
    propager_lien_bibliotheque as _propager_lien_bibliotheque,
)
from core.generation_site import (
    deployer_site as _deployer_site,
    site_deploiement_disponible,
)
from core.bibliotheque_fichiers import (
    chercher_fichiers as _chercher_fichiers,
    enregistrer_fichier as _enregistrer_fichier,
    enregistrer_lien as _enregistrer_lien,
    lister_fichiers as _lister_fichiers,
    supprimer_fichier as _supprimer_fichier,
)
from core.bibliotheque_rag import (
    chercher_bibliotheque as _chercher_bibliotheque,
    chercher_bibliotheque_publique as _chercher_bibliotheque_publique,
    lire_document_bibliotheque_en_entier as _lire_document_bibliotheque_en_entier,
    indexer_pdf_bibliotheque as _indexer_pdf_bibliotheque,
    indexer_texte_bibliotheque as _indexer_texte_bibliotheque,
    indexer_transcription_bibliotheque as _indexer_transcription_bibliotheque,
    formater_source_bibliotheque as _formater_source_bibliotheque,
)
from core.catalogue_public_rag import (
    chercher_catalogue_public as _chercher_catalogue_public,
    lire_document_catalogue_public as _lire_document_catalogue_public,
)
from core.bibliotheque_programme import (
    classer_document as _classer_document,
    declasser_document as _declasser_document,
    lister_emplacements_document as _lister_emplacements_document,
    libelle_emplacement as _libelle_emplacement,
    fichiers_des_plugins_publics as _fichiers_des_plugins_publics,
    TYPES_EMPLACEMENT_BIBLIOTHEQUE,
)
from core.dossiers_bibliotheque import (
    _proprietaire_dossier,
    creer_dossier as _creer_dossier,
    lister_dossiers as _lister_dossiers,
    lister_fichiers_ids_dossier as _lister_fichiers_ids_dossier,
    ranger_fichier as _ranger_fichier,
    renommer_dossier as _renommer_dossier,
    retirer_fichier as _retirer_fichier,
    supprimer_dossier as _supprimer_dossier,
)
from core.description_multimedia import (
    decrire_image_bibliotheque as _decrire_image_bibliotheque,
    transcrire_audio_bibliotheque as _transcrire_audio_bibliotheque,
)

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
def gerer_document_bibliotheque(
    action: str,
    ctx: Context,
    question: str = "",
    fichier_id: str = "",
    url: str = "",
    titre: str = "",
    contenu: str = "",
    nom_fichier: str = "",
    type_mime: str = "",
    description: str = "",
    contenu_base64: str = "",
    url_fichier: str = "",
    type_emplacement: str = "",
    emplacement_id: str = "",
    dossier_id: str = "",
) -> str:
    """
    Gère la bibliothèque personnelle de CET utilisateur, et permet aussi de
    chercher/localiser des documents dans le catalogue public et les
    plugins communautaires partagés. Section "Bibliothèque" de "Mon
    espace" -- un seul outil, plusieurs actions, au lieu de 12 outils
    séparés (consolidé le 26/08, demande Bourama : "un outil puis
    paramètres c'est pas mieux que plusieurs outils si on peut regrouper",
    pattern "action + paramètres" -- réduit le catalogue envoyé au
    routeur/modèle et limite les confusions de noms d'outils proches).

    `action` doit être l'une de :
    - "chercher" : cherche par contenu dans la bibliothèque PERSONNELLE.
      Paramètre : `question`.
    - "chercher_publique" : cherche par contenu dans les PLUGINS PUBLICS
      (bibliothèques partagées par la communauté). Paramètre : `question`.
    - "trouver_catalogue_public" : LOCALISE un document dans le
      CATALOGUE PUBLIC (section "Bibliothèque publique", ouvert à tout
      le monde -- différent des plugins publics ci-dessus). Renvoie
      uniquement le nom, la description et le lien de chaque document
      trouvé, JAMAIS son contenu : ne sert qu'à dire à l'utilisateur où
      trouver un document, jamais à citer ou paraphraser ce document
      dans ta réponse. Paramètre : `question`.
    - "lire_catalogue_public" : renvoie le texte intégral d'un document
      du catalogue public, identifié par le `fichier_id` obtenu via
      "trouver_catalogue_public". N'appelle cette action QUE si
      l'utilisateur demande explicitement à voir/lire ce document en
      entier -- jamais automatiquement après un "trouver_catalogue_
      public". Paramètre : `fichier_id`.
    - "lister" : liste les documents/liens/notes de la bibliothèque
      personnelle, sans recherche par contenu. Aucun paramètre.
    - "ajouter_lien" : ajoute un lien. Paramètres : `url`, `titre`.
    - "ajouter_texte" : ajoute une note de texte libre. Paramètres :
      `contenu`, `titre`.
    - "ajouter_fichier" : ajoute un fichier (PDF, image, audio ou vidéo).
      Paramètres : `nom_fichier`, `type_mime` (obligatoires, à déduire
      TOI-MÊME du contexte -- ne jamais les demander à l'utilisateur, ce
      sont des détails techniques qu'il n'a pas à connaître : reprends le
      nom donné entre crochets juste après un upload, ex. "[Document
      joint : cours_svt.pdf]", ou l'extension visible à la fin de
      `url_fichier` ; `type_mime` se déduit de cette même extension,
      mapping standard extension -> type MIME). `titre`/`description`
      optionnels. Fournir SOIT `url_fichier` (lien réel d'un fichier déjà
      joint dans CETTE conversation, entre crochets "[Lien réel du
      fichier : ...]" -- à privilégier systématiquement) SOIT
      `contenu_base64` (jamais les deux vides). SI AUCUN FICHIER N'A ÉTÉ
      JOINT dans cette conversation : n'appelle PAS cette action, dis
      simplement à l'utilisateur d'uploader/joindre le fichier (bouton
      trombone). `type_emplacement`/`emplacement_id` optionnels
      ("programme"/"matiere"/"chapitre"/"exercice"/"examen" + son id) :
      classe directement ce document à cet endroit du programme dès
      l'ajout. Limite : 50 Mo.
    - "supprimer" : supprime DÉFINITIVEMENT un document/lien/note.
      Paramètre : `fichier_id`. SENSIBLE : demande toujours confirmation
      à l'utilisateur avant d'être exécuté, quelle que soit la
      formulation de sa demande.
    - "classer" : classe un document existant à un emplacement du
      programme. Paramètres : `fichier_id`, `type_emplacement` ("programme",
      "matiere", "chapitre", "exercice" ou "examen"), `emplacement_id`. Un
      même document peut être classé à plusieurs emplacements (appeler
      plusieurs fois) ; reclasser au même endroit ne crée pas de doublon.
    - "declasser" : retire un document d'un emplacement du programme (le
      document reste dans la bibliothèque, seul ce classement précis
      disparaît). Mêmes paramètres que "classer".
    - "ranger_dossier" : range un fichier dans un dossier. Paramètres :
      `fichier_id`, `dossier_id`. Un fichier peut être rangé dans
      plusieurs dossiers à la fois.
    - "retirer_dossier" : retire un fichier d'un dossier précis (le
      fichier reste dans la bibliothèque et ses autres dossiers
      éventuels). Paramètres : `fichier_id`, `dossier_id`.
    - "lire_entier" : renvoie le texte intégral d'un document PDF/texte
      déjà indexé (obtiens `fichier_id` via "chercher" ou chercher_fichier).
      À utiliser quand les extraits de "chercher" ne suffisent pas. Ne
      fonctionne que pour les documents PDF/texte (images/audio/vidéo non
      vectorisés aujourd'hui -- utilise leur lien pour les afficher).
      Paramètre : `fichier_id`.

    Pour les dossiers eux-mêmes (créer/lister/renommer/supprimer un
    dossier), voir les outils dédiés (section "Dossiers de la
    bibliothèque personnelle" plus bas) -- non consolidés ici.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "chercher":
        # BUG corrigé le 14/08 (constaté en prod : "Rien de pertinent
        # trouvé" alors que la bibliothèque contenait bien des documents
        # indexés) -- user_id vient de ctx (authentifié), jamais d'un
        # paramètre que le modèle pourrait halluciner/inventer.
        try:
            resultats = _chercher_bibliotheque(question, user_id=user_id)
        except Exception:
            return "Erreur : la recherche dans la bibliothèque a échoué, réessaie."
        if not resultats:
            return "Rien de pertinent trouvé dans la bibliothèque pour cette question."
        blocs = []
        for r in resultats:
            bloc = r["contenu"]
            source = _formater_source_bibliotheque(r)
            if source:
                bloc += f"\n{source}"
            blocs.append(bloc)
        return "\n\n---\n\n".join(blocs)

    if action == "chercher_publique":
        try:
            programmes = (
                _supabase_memoire.table("programmes").select("niveau").eq("proprietaire_id", user_id).execute()
            )
            niveaux = list({p["niveau"] for p in (programmes.data or []) if p.get("niveau")})
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (chercher_publique, niveaux user {user_id}) : {e}")
            niveaux = []
        try:
            fichier_ids = _fichiers_des_plugins_publics(niveaux)
            resultats = _chercher_bibliotheque_publique(question, fichier_ids)
        except Exception:
            return "Erreur : la recherche dans les plugins publics a échoué, réessaie."
        if not resultats:
            return "Rien de pertinent trouvé dans les plugins publics pour cette question."
        blocs = []
        for r in resultats:
            bloc = r["contenu"]
            source = _formater_source_bibliotheque(r)
            if source:
                bloc += f"\n{source}"
            blocs.append(bloc)
        return "\n\n---\n\n".join(blocs)

    if action == "trouver_catalogue_public":
        try:
            resultats = _chercher_catalogue_public(question)
        except Exception:
            return "Erreur : la recherche dans le catalogue public a échoué, réessaie."
        if not resultats:
            return "Rien de pertinent trouvé dans le catalogue public pour cette question."
        lignes = []
        for r in resultats:
            ligne = f"- {r['nom']}"
            if r.get("description"):
                ligne += f" — {r['description']}"
            if r.get("url_publique"):
                ligne += f" ({r['url_publique']})"
            lignes.append(ligne)
        return "\n".join(lignes)

    if action == "lire_catalogue_public":
        texte = _lire_document_catalogue_public(fichier_id)
        if texte is None:
            return "Rien à lire pour ce document : soit il n'existe pas, soit son contenu n'a pas pu être vectorisé (vidéo, ou lien externe)."
        return texte

    if action == "lister":
        try:
            fichiers = _lister_fichiers("utilisateur", user_id=user_id, origine="bibliotheque")
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (lister) : {e}")
            return "Erreur : impossible de lister la bibliothèque, réessaie."
        if not fichiers:
            return "Bibliothèque vide pour l'instant."
        lignes = []
        for f in fichiers:
            ligne = (
                f"- {f.get('description') or f.get('nom_fichier')} "
                f"({f.get('type_mime', 'inconnu')}, ajouté le {f.get('created_at', '?')})"
            )
            emplacements = _lister_emplacements_document(f["id"])
            if emplacements:
                ligne += " | classé dans : " + ", ".join(e["libelle"] for e in emplacements)
            ligne += f" [id: {f['id']}]"
            lignes.append(ligne)
        return "\n".join(lignes)

    if action == "ajouter_lien":
        url_val = (url or "").strip()
        if not url_val:
            return "Erreur : url manquante."
        titre_final = (titre or url_val).strip()
        try:
            ligne = _enregistrer_lien(
                url=url_val,
                nom_fichier=titre_final,
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=titre_final,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_lien) : {e}")
            return "Erreur : impossible d'enregistrer ce lien, réessaie."
        try:
            _propager_lien_bibliotheque(user_id, url_val, titre_final, titre_final)
        except Exception as e:
            logging.error(f"ERREUR propagation gerer_document_bibliotheque (ajouter_lien) : {e}")
        return f"Lien ajouté (id {ligne['id']})."

    if action == "ajouter_texte":
        contenu_val = (contenu or "").strip()
        if not contenu_val:
            return "Erreur : contenu vide."
        titre_val = (titre or "").strip()
        nom_fichier_note = f"{titre_val or 'Note'}.txt"
        description_note = titre_val or (contenu_val[:80] + ("…" if len(contenu_val) > 80 else ""))
        try:
            ligne = _enregistrer_fichier(
                contenu=contenu_val.encode("utf-8"),
                nom_fichier=nom_fichier_note,
                type_mime="text/plain",
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=description_note,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_texte) : {e}")
            return "Erreur : impossible d'enregistrer cette note, réessaie."
        try:
            _indexer_texte_bibliotheque(contenu_val, fichier_id=ligne["id"], user_id=user_id)
        except Exception as e:
            logging.error(f"ERREUR vectorisation gerer_document_bibliotheque (ajouter_texte) : {e}")
        try:
            _propager_fichier_bibliotheque(user_id, contenu_val.encode("utf-8"), nom_fichier_note, "text/plain", titre_val or None)
        except Exception as e:
            logging.error(f"ERREUR propagation gerer_document_bibliotheque (ajouter_texte) : {e}")
        return f"Note ajoutée (id {ligne['id']})."

    if action == "ajouter_fichier":
        type_mime_val = (type_mime or "").strip().lower()
        if not type_mime_val:
            return "Erreur : type de fichier manquant."
        url_fichier_val = (url_fichier or "").strip()
        contenu_base64_val = (contenu_base64 or "").strip()
        if not url_fichier_val and not contenu_base64_val:
            return "Erreur : fournis url_fichier (lien réel d'un fichier déjà joint dans la conversation) ou contenu_base64."
        if url_fichier_val:
            try:
                reponse = requests.get(url_fichier_val, timeout=30)
                reponse.raise_for_status()
                contenu_fichier = reponse.content
            except Exception as e:
                logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_fichier, url_fichier={url_fichier_val}) : {e}")
                return "Erreur : impossible de récupérer le fichier à cette URL."
        else:
            try:
                contenu_fichier = base64.b64decode(contenu_base64_val, validate=True)
            except Exception:
                return "Erreur : contenu_base64 invalide (doit être du base64 valide)."
        if len(contenu_fichier) == 0:
            return "Erreur : fichier vide."
        if len(contenu_fichier) > _TAILLE_MAX_OCTETS_BIBLIOTHEQUE:
            return "Erreur : fichier trop lourd (50 Mo max)."
        nom_original = (nom_fichier or "fichier").strip()
        titre_val = (titre or "").strip()
        description_val = (description or "").strip()
        description_finale = (
            f"{titre_val} — {description_val}" if titre_val and description_val
            else (description_val or titre_val or nom_original)
        )
        try:
            ligne = _enregistrer_fichier(
                contenu=contenu_fichier,
                nom_fichier=nom_original,
                type_mime=type_mime_val,
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=description_finale,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_fichier) : {e}")
            return "Erreur : impossible d'enregistrer ce fichier, réessaie."
        if type_mime_val == "application/pdf":
            chemin_temp = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(contenu_fichier)
                    chemin_temp = tmp.name
                _indexer_pdf_bibliotheque(chemin_temp, fichier_id=ligne["id"], user_id=user_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation gerer_document_bibliotheque (ajouter_fichier, fichier_id={ligne['id']}) : {e}")
            finally:
                if chemin_temp:
                    try:
                        os.remove(chemin_temp)
                    except OSError:
                        pass
        elif type_mime_val.startswith("image/"):
            try:
                description_image = _decrire_image_bibliotheque(contenu_fichier, type_mime_val)
                if description_image:
                    _indexer_texte_bibliotheque(description_image, fichier_id=ligne["id"], user_id=user_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation image gerer_document_bibliotheque (ajouter_fichier, fichier_id={ligne['id']}) : {e}")
        elif type_mime_val.startswith("audio/"):
            try:
                segments_audio = _transcrire_audio_bibliotheque(contenu_fichier, nom_original)
                if segments_audio:
                    _indexer_transcription_bibliotheque(segments_audio, fichier_id=ligne["id"], user_id=user_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation audio gerer_document_bibliotheque (ajouter_fichier, fichier_id={ligne['id']}) : {e}")
        try:
            _propager_fichier_bibliotheque(user_id, contenu_fichier, nom_original, type_mime_val, description_finale)
        except Exception as e:
            logging.error(f"ERREUR propagation gerer_document_bibliotheque (ajouter_fichier) : {e}")
        message = f"Fichier ajouté (id {ligne['id']})."
        if type_emplacement and emplacement_id:
            if type_emplacement not in TYPES_EMPLACEMENT_BIBLIOTHEQUE:
                message += f" Attention : type d'emplacement invalide ({type_emplacement}), pas classé dans le programme."
            else:
                resultat = _classer_document(user_id, ligne["id"], type_emplacement, emplacement_id)
                if resultat["ok"]:
                    libelle = _libelle_emplacement(type_emplacement, emplacement_id) or emplacement_id
                    message += f" Classé dans : {libelle}."
                else:
                    message += f" Attention : pas classé dans le programme ({resultat['erreur']})"
        return message

    if action == "supprimer":
        try:
            res = (
                _supabase_memoire.table("fichiers_uploades")
                .select("user_id")
                .eq("id", fichier_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (supprimer, lecture) : {e}")
            return "Erreur : impossible de supprimer ce document, réessaie."
        if not res or not res.data:
            return "Ce document est introuvable."
        if res.data["user_id"] != user_id:
            return "Ce document ne t'appartient pas."
        try:
            _supprimer_fichier(fichier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (supprimer, suppression) : {e}")
            return "Erreur : impossible de supprimer ce document, réessaie."
        return "Document supprimé."

    if action == "classer":
        if type_emplacement not in TYPES_EMPLACEMENT_BIBLIOTHEQUE:
            return f"Erreur : type d'emplacement invalide, utilise l'un de {TYPES_EMPLACEMENT_BIBLIOTHEQUE}."
        resultat = _classer_document(user_id, fichier_id, type_emplacement, emplacement_id)
        if not resultat["ok"]:
            return f"Erreur : {resultat['erreur']}"
        libelle = _libelle_emplacement(type_emplacement, emplacement_id) or emplacement_id
        return f"Document classé dans : {libelle}."

    if action == "declasser":
        resultat = _declasser_document(user_id, fichier_id, type_emplacement, emplacement_id)
        if not resultat["ok"]:
            return f"Erreur : {resultat['erreur']}"
        return "Document retiré de cet emplacement du programme."

    if action == "ranger_dossier":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            res = _supabase.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ranger_dossier, lecture fichier) : {e}")
            return "Erreur : impossible de ranger ce fichier, réessaie."
        if not res or not res.data:
            return "Ce fichier est introuvable."
        if res.data["user_id"] != user_id:
            return "Ce fichier ne t'appartient pas."
        try:
            _ranger_fichier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ranger_dossier) : {e}")
            return "Erreur : impossible de ranger ce fichier, réessaie."
        return "Fichier rangé dans le dossier."

    if action == "retirer_dossier":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            _retirer_fichier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (retirer_dossier) : {e}")
            return "Erreur : impossible de retirer ce fichier, réessaie."
        return "Fichier retiré du dossier."

    if action == "lire_entier":
        texte = _lire_document_bibliotheque_en_entier(fichier_id, user_id=user_id)
        if texte is None:
            return "Rien à lire pour ce fichier : soit il n'existe pas ou ne t'appartient pas, soit ce n'est pas un PDF/texte indexé."
        return texte

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : chercher, chercher_publique, "
        "trouver_catalogue_public, lire_catalogue_public, lister, ajouter_lien, ajouter_texte, "
        "ajouter_fichier, supprimer, classer, declasser, ranger_dossier, retirer_dossier, lire_entier."
    )


# --- Dossiers de la bibliothèque personnelle (22/08, demande Bourama,
# parité avec serveur_mcp_espace.py) : voir core/dossiers_bibliotheque.py
# pour la logique complète. Séparé du classement dans le programme
# ci-dessus. Un fichier peut être dans plusieurs dossiers à la fois.

@mcp_generation.tool()
def gerer_dossier_bibliotheque(
    action: str,
    ctx: Context,
    dossier_id: str = "",
    nom: str = "",
    dossier_parent_id: str = "",
    nouveau_nom: str = "",
) -> str:
    """
    Gère les dossiers de la bibliothèque personnelle de CET utilisateur
    (organisation des documents/liens/notes, distincte du classement
    dans le programme -- voir gerer_document_bibliotheque pour ça) --
    consolidé le 26/08, un seul outil, plusieurs actions (pattern
    "action + paramètres", même logique que gerer_document_bibliotheque).

    `action` doit être l'une de :
    - "lister" : liste tous les dossiers avec leur arborescence (dossier
      parent) et le nombre de fichiers directement rangés dans chacun.
      Aucun paramètre.
    - "consulter" : liste le contenu direct d'un dossier précis (ses
      sous-dossiers et ses fichiers). Ne descend pas récursivement dans
      les sous-dossiers, rappelle avec l'id d'un sous-dossier pour y
      entrer. Paramètre : `dossier_id`.
    - "creer" : crée un dossier. Paramètres : `nom` ; `dossier_parent_id`
      optionnel (id d'un dossier existant pour créer un SOUS-dossier
      dedans, laisse vide pour un dossier à la racine).
    - "renommer" : renomme un dossier existant. Paramètres : `dossier_id`,
      `nouveau_nom`.
    - "supprimer" : supprime DÉFINITIVEMENT un dossier (et ses
      sous-dossiers). Un fichier encore rattaché à au moins un autre
      dossier est conservé (juste détaché de celui-ci) ; un fichier qui
      n'était rattaché à AUCUN autre dossier est supprimé en même temps
      que le dossier. Paramètre : `dossier_id`. SENSIBLE : demande
      toujours confirmation à l'utilisateur avant d'être exécuté, quelle
      que soit la formulation de sa demande.

    Pour ranger/retirer un fichier DANS un dossier, ou pour les documents
    eux-mêmes (chercher/lister/ajouter/supprimer/classer), voir l'outil
    dédié gerer_document_bibliotheque.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "lister":
        try:
            dossiers = _lister_dossiers(user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (lister) : {e}")
            return "Erreur : impossible de lister les dossiers, réessaie."
        if not dossiers:
            return "Aucun dossier pour l'instant."
        par_id = {d["id"]: d for d in dossiers}
        lignes = []
        for d in dossiers:
            parent = par_id.get(d["dossier_parent_id"])
            chemin = f"{parent['nom']} > {d['nom']}" if parent else d["nom"]
            nb_fichiers = len(_lister_fichiers_ids_dossier(d["id"]))
            lignes.append(f"- {chemin} [id: {d['id']}] ({nb_fichiers} fichier(s) direct(s))")
        return "\n".join(lignes)

    if action == "consulter":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            dossiers = _lister_dossiers(user_id)
            sous_dossiers = [d for d in dossiers if d["dossier_parent_id"] == dossier_id]
            fichier_ids = _lister_fichiers_ids_dossier(dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (consulter) : {e}")
            return "Erreur : impossible de consulter ce dossier, réessaie."

        lignes = []
        for sd in sous_dossiers:
            lignes.append(f"- [dossier] {sd['nom']} [id: {sd['id']}]")
        for f_id in fichier_ids:
            try:
                res = _supabase.table("fichiers_uploades").select("nom_fichier, description, type_mime").eq("id", f_id).maybe_single().execute()
            except Exception as e:
                logging.error(f"ERREUR gerer_dossier_bibliotheque (consulter, lecture fichier {f_id}) : {e}")
                continue
            if not res or not res.data:
                continue
            f = res.data
            lignes.append(f"- [fichier] {f.get('description') or f.get('nom_fichier')} ({f.get('type_mime', 'inconnu')}) [id: {f_id}]")
        if not lignes:
            return "Ce dossier est vide."
        return "\n".join(lignes)

    if action == "creer":
        nom_val = (nom or "").strip()
        if not nom_val:
            return "Erreur : nom de dossier manquant."
        parent_id = (dossier_parent_id or "").strip() or None
        if parent_id:
            proprietaire = _proprietaire_dossier(parent_id)
            if proprietaire is None:
                return "Erreur : le dossier parent indiqué est introuvable."
            if proprietaire != user_id:
                return "Erreur : ce dossier parent ne t'appartient pas."
        try:
            dossier = _creer_dossier(user_id, nom_val, parent_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (creer) : {e}")
            return "Erreur : impossible de créer ce dossier, réessaie."
        return f"Dossier « {nom_val} » créé [id: {dossier['id']}]."

    if action == "renommer":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        nouveau_nom_val = (nouveau_nom or "").strip()
        if not nouveau_nom_val:
            return "Erreur : nouveau nom manquant."
        try:
            _renommer_dossier(dossier_id, nouveau_nom_val)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (renommer) : {e}")
            return "Erreur : impossible de renommer ce dossier, réessaie."
        return f"Dossier renommé en « {nouveau_nom_val} »."

    if action == "supprimer":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            _supprimer_dossier(dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (supprimer) : {e}")
            return "Erreur : impossible de supprimer ce dossier, réessaie."
        return "Dossier supprimé."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister, "
        "consulter, creer, renommer, supprimer."
    )


@mcp_generation.tool()
def gerer_memoire_utilisateur(
    action: str,
    ctx: Context,
    champs_json: str = "",
) -> str:
    """
    Gère la mémoire long-terme structurée de CET utilisateur, valable
    d'une conversation à l'autre (préférences, matières suivies,
    difficultés récurrentes, projets en cours, etc.), consolidé le
    26/08, un seul outil, plusieurs actions.

    `action` doit être l'une de :
    - "consulter" : renvoie ce qui est déjà su de cet utilisateur, sous
      forme de JSON (peut être vide si rien n'a encore été noté). À
      utiliser au début d'une conversation si ça peut aider à mieux
      répondre, ou dès qu'un élément de contexte passé serait utile.
      Aucun paramètre.
    - "mettre_a_jour" : note ou met à jour un ou plusieurs éléments, à
      utiliser dès que tu apprends quelque chose d'utile à retenir pour
      les prochaines conversations (préférence, difficulté récurrente,
      projet en cours, etc.). Le schéma est libre : garde les clés déjà
      utilisées si elles collent (ex. "profil_personnel",
      "preferences_pedagogiques", "matieres_ou_sujets",
      "objectifs_et_projets", "points_de_continuite"), ou crée-en de
      nouvelles si aucune ne convient. Paramètre `champs_json` : objet
      JSON, ex. '{"preferences_pedagogiques": {"style_explication":
      "avec des exemples concrets"}}', fusionné avec la mémoire
      existante (les clés de premier niveau fournies remplacent leur
      ancienne valeur, le reste est conservé tel quel). N'écris ici que
      ce qui a une vraie valeur à long terme, pas le contenu d'un seul
      message.
    - "effacer" : efface DÉFINITIVEMENT le résumé long-terme ("oublie
      tout ce que tu sais de moi"). Aucun paramètre. SENSIBLE : demande
      toujours confirmation à l'utilisateur avant d'être exécuté, quelle
      que soit la formulation de sa demande.
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    if action == "consulter":
        try:
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
            logging.error(f"ERREUR gerer_memoire_utilisateur (consulter) : {e}")
            return "Erreur : impossible de consulter la mémoire, réessaie."

    if action == "mettre_a_jour":
        try:
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
            logging.error(f"ERREUR gerer_memoire_utilisateur (mettre_a_jour) : {e}")
            return "Erreur : la mise à jour de la mémoire a échoué, réessaie."

    if action == "effacer":
        try:
            _supabase_memoire.table("conversation_summaries").delete().eq("user_id", user_id).execute()
        except Exception as e:
            logging.error(f"ERREUR gerer_memoire_utilisateur (effacer) : {e}")
            return "Erreur : impossible d'effacer la mémoire, réessaie."
        return "Mémoire effacée."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : consulter, "
        "mettre_a_jour, effacer."
    )


@mcp_generation.tool()
def lister_conversations_historique(ctx: Context) -> str:
    """
    Liste les fils de discussion distincts entre CET utilisateur et
    Clovis (section "Historique"), le plus récemment actif en premier.
    Renvoie pour chacun : conversation_id ("legacy" pour les échanges
    d'avant l'historique par fil), titre (début du premier message),
    dernière activité.
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    agent_id = requete.query_params.get("agent_id")
    if not user_id or not agent_id:
        return "Erreur : impossible d'identifier l'utilisateur ou l'agent."
    try:
        lignes = (
            _supabase_memoire.table("historique_conversations")
            .select("conversation_id, role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .order("created_at")
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR outil lister_conversations_historique : {e}")
        return "Erreur : impossible de charger l'historique, réessaie."

    if not lignes:
        return "Aucune conversation dans l'historique pour l'instant."

    fils: dict = {}
    for ligne in lignes:
        cle = ligne["conversation_id"] or "legacy"
        if cle not in fils:
            fils[cle] = {"premier_message_user": None, "derniere_activite": ligne["created_at"]}
        if ligne["role"] == "user" and fils[cle]["premier_message_user"] is None:
            fils[cle]["premier_message_user"] = ligne["content"]
        fils[cle]["derniere_activite"] = ligne["created_at"]

    resultats = []
    for cle, fil in fils.items():
        if cle == "legacy":
            titre = "Avant l'historique par conversation"
        else:
            titre = (fil["premier_message_user"] or "(sans titre)")[:80]
        resultats.append((fil["derniere_activite"], f"- {titre} | dernière activité : {fil['derniere_activite']} [conversation_id: {cle}]"))
    resultats.sort(reverse=True)
    return "\n".join(l for _, l in resultats)


@mcp_generation.tool()
def lire_conversation_historique(conversation_id: str, ctx: Context) -> str:
    """
    Contenu complet d'un fil de discussion précis entre CET utilisateur
    et Clovis, à partir de son conversation_id (voir
    lister_conversations_historique -- utilise littéralement "legacy"
    pour recharger les échanges d'avant l'historique par fil).
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    agent_id = requete.query_params.get("agent_id")
    if not user_id or not agent_id:
        return "Erreur : impossible d'identifier l'utilisateur ou l'agent."
    try:
        req = (
            _supabase_memoire.table("historique_conversations")
            .select("role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
        )
        if conversation_id == "legacy":
            req = req.is_("conversation_id", "null")
        else:
            req = req.eq("conversation_id", conversation_id)
        lignes = req.order("created_at").execute().data or []
    except Exception as e:
        logging.error(f"ERREUR outil lire_conversation_historique : {e}")
        return "Erreur : impossible de charger cette conversation, réessaie."

    if not lignes:
        return "Cette conversation est introuvable ou vide."

    return "\n".join(f"[{l['role']}] {l['content']}" for l in lignes)


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
def gerer_comportement(
    action: str,
    ctx: Context,
    comportement_id: str = "",
    texte: str = "",
) -> str:
    """
    Gère les instructions personnelles ("skills" dans toute l'interface,
    "comportement" seulement en interne) que CET étudiant a écrites pour
    Clovis, section "Mes comportements" de "Mon espace" -- consolidé le
    26/08, un seul outil, plusieurs actions.

    `action` doit être l'une de :
    - "lister" : liste tous les comportements de cet étudiant (id,
      description courte, emplacement lié le cas échéant -- PAS le texte
      complet, voir "consulter" pour ça). IMPORTANT (terme utilisateur) :
      dans TOUTE l'interface, cette fonctionnalité s'appelle "skill(s)" --
      l'utilisateur ne dira presque jamais "comportement". Utilise cette
      action dès qu'il demande "mes skills", "quels sont mes skills",
      "montre-moi mes skills/mes comportements", etc. -- pas seulement
      quand un skill semble déjà pertinent pour le message en cours (ça,
      c'est géré par la liste de candidats du message système, voir
      "consulter") : "lister" répond à une vraie demande d'énumération.
      Aucun paramètre.
    - "consulter" : lit le skill COMPLET (format Claude, frontmatter +
      instructions) d'un comportement précis, que cet utilisateur l'ait
      écrit lui-même, ou qu'il l'ait reçu d'un autre utilisateur via un
      code (id préfixé "recu:"). Le message système t'a déjà donné une
      courte description de ceux qui semblent pertinents pour ce
      message -- utilise cette action quand l'un d'eux semble
      s'appliquer, AVANT de répondre, pour lire son contenu réel plutôt
      que de deviner à partir de la description seule. Paramètre :
      `comportement_id`.
    - "ajouter" : enregistre une NOUVELLE instruction personnelle, à
      utiliser SEULEMENT quand l'étudiant exprime CLAIREMENT et
      EXPLICITEMENT une préférence ou une règle à retenir pour la suite
      (ex: "explique-moi toujours avec des schémas", "ne me donne jamais
      la réponse directe, guide-moi", "crée-moi un skill qui..."). S'ajoute
      EN PLUS de ses autres comportements, ne les remplace pas.
      N'UTILISE JAMAIS CETTE ACTION SUR UNE SUPPOSITION. Si la demande
      est vague, ambiguë, ou que tu devines seulement ce que l'étudiant
      voudrait retenir sans qu'il l'ait dit clairement, NE CRÉE RIEN --
      demande-lui d'abord de préciser ce qu'il veut que tu retiennes
      exactement. Ne crée jamais un comportement "au cas où", pour
      anticiper un besoin non exprimé, ou à partir d'une remarque en
      passant qui n'était pas une vraie demande de mémorisation. Une
      création hâtive et mal comprise est pire qu'aucune création : elle
      pollue durablement ses instructions et influence toutes ses
      conversations futures avec toi. Paramètre : `texte`.
    - "modifier" : remplace le texte COMPLET d'un comportement existant
      (à partir de son id, vu via "consulter" ou la description courte
      donnée dans le message système). Utilise cette action quand
      l'étudiant veut corriger ou préciser une instruction déjà
      enregistrée -- pas pour en ajouter une nouvelle (voir "ajouter").
      Paramètres : `comportement_id`, `texte`.
    - "supprimer" : supprime DÉFINITIVEMENT un comportement, à partir de
      son id. Paramètre : `comportement_id`. SENSIBLE : demande toujours
      confirmation à l'étudiant avant d'être exécuté, quelle que soit la
      formulation de sa demande.
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    agent_id = requete.query_params.get("agent_id")
    if not user_id or not agent_id:
        return "Erreur : impossible d'identifier l'étudiant ou l'agent."

    if action == "lister":
        try:
            comportements = _lister_comportements(agent_id, user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (lister) : {e}")
            return "Erreur : impossible de lister les comportements, réessaie."
        if not comportements:
            return "Aucun comportement enregistré pour l'instant."
        lignes = []
        for c in comportements:
            ligne = f"- {c['description']}"
            if c.get("lien_type") and c.get("lien_id"):
                libelle = _libelle_emplacement(c["lien_type"], c["lien_id"]) if c["lien_type"] in TYPES_EMPLACEMENT_BIBLIOTHEQUE else None
                ligne += f"\n  lié à : {libelle or (c['lien_type'] + ' ' + c['lien_id'])}"
            ligne += f"\n  [id: {c['id']}]"
            lignes.append(ligne)
        return "\n".join(lignes)

    if action == "consulter":
        try:
            if comportement_id.startswith("recu:"):
                skill_md = _obtenir_comportement_skill_recu(user_id, comportement_id)
            else:
                skill_md = _obtenir_comportement_skill(agent_id, user_id, comportement_id)
            if skill_md is None:
                return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
            return skill_md
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (consulter) : {e}")
            return "Erreur : impossible de consulter ce comportement, réessaie."

    if action == "ajouter":
        try:
            ligne = _ajouter_comportement(agent_id, user_id, texte)
            return f"Comportement enregistré (id {ligne['id']}) : {ligne['description']}"
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (ajouter) : {e}")
            return "Erreur : impossible d'enregistrer ce comportement, réessaie."

    if action == "modifier":
        try:
            ligne = _modifier_comportement(agent_id, user_id, comportement_id, texte)
            if ligne is None:
                return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
            return f"Comportement modifié : {ligne['description']}"
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (modifier) : {e}")
            return "Erreur : impossible de modifier ce comportement, réessaie."

    if action == "supprimer":
        try:
            ok = _supprimer_comportement(agent_id, user_id, comportement_id)
            if not ok:
                return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
            return "Comportement supprimé."
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (supprimer) : {e}")
            return "Erreur : impossible de supprimer ce comportement, réessaie."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister, "
        "consulter, ajouter, modifier, supprimer."
    )


@mcp_generation.tool()
def lister_mes_programmes(ctx: Context) -> str:
    """
    Liste légère (id, niveau, nom) de TOUS les programmes de CET
    utilisateur -- point de départ obligatoire avant tout autre outil
    "programme" s'il n'a pas déjà d'id précis en tête : ils ont tous
    besoin d'un programme_id/matiere_id/chapitre_id en entrée, jamais à
    deviner. Ne contient PAS les matières/chapitres à l'intérieur (voir
    consulter_programme une fois l'id du programme choisi).
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'étudiant."
    try:
        programmes = _lister_mes_programmes_legers(user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lister_mes_programmes : {e}")
        return "Erreur : impossible de lister les programmes, réessaie."
    if not programmes:
        return "Aucun programme enregistré pour l'instant."
    return "\n".join(
        f"- {p['niveau']}" + (f" — {p['nom']}" if p.get("nom") else "") + f" (id: {p['id']})"
        for p in programmes
    )


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
    ou apprend, ça c'est la mémoire, voir gerer_memoire_utilisateur
    action "mettre_a_jour").
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
def gerer_base_connaissance(
    action: str,
    ctx: Context,
    question: str = "",
    nom: str = "",
) -> str:
    """
    Cherche et lit dans la base de connaissances de l'agent (documents et
    instructions de référence préparés à l'avance par l'équipe Clovis SUR
    Clovis et l'application elle-même, PAS les documents personnels de
    l'utilisateur, voir gerer_document_bibliotheque pour ça), consolidé
    le 26/08, un seul outil, plusieurs actions qui fonctionnaient déjà
    ensemble comme un mécanisme à plusieurs étapes.

    `action` doit être l'une de :
    - "chercher" : cherche les passages pertinents pour répondre à
      `question`. À utiliser quand la question touche un sujet précis où
      un contenu de référence a pu être préparé à l'avance, pas
      systématique, seulement si pertinent. Renvoie les extraits trouvés
      ou un message si rien de pertinent. Paramètre : `question`.
    - "lister_articles" : liste les noms de tous les articles
      disponibles. À utiliser avant "lire_article" si le nom exact de
      l'article recherché n'est pas connu. Aucun paramètre.
    - "lire_article" : renvoie le texte COMPLET et EXACT (pas un résumé,
      pas une reformulation, le contenu tel qu'il est stocké, mot pour
      mot) d'un article, identifié par son `nom` exact. À utiliser quand
      la question porte sur l'ensemble d'un article plutôt que sur un
      point précis (ex : "montre-moi l'article Bibliothèque", "affiche
      le fichier tel qu'il est"), en complément de "chercher" qui ne
      renvoie que des passages. Quand tu restitues ce résultat à
      l'utilisateur, recopie-le intégralement et tel quel (verbatim), ne
      le résume pas, ne le reformule pas, ne le raccourcis pas. Si `nom`
      est inconnu, utilise d'abord "chercher" pour identifier le bon
      nom, ou "lister_articles" pour voir les noms disponibles.
      Paramètre : `nom`.
    - "obtenir_fichier" : renvoie le FICHIER original (pas son texte
      recopié) d'un article, sous forme d'un lien vers le fichier tel
      qu'il a été déposé. À utiliser quand tu juges que le fichier
      lui-même aide réellement la réponse (l'utilisateur ne sait
      généralement pas qu'il existe, donc ne le demandera pas
      explicitement), pas systématiquement à chaque question. Pas juste
      lire son contenu (pour ça, "lire_article"). Si `nom` est inconnu,
      utilise "lister_articles" pour voir les noms disponibles.
      Paramètre : `nom`.
    """
    requete = ctx.request_context.request
    agent_id = requete.query_params.get("agent_id")

    if action == "chercher":
        try:
            candidats = _chercher_candidats(question, agent_id=agent_id)
            morceaux = [c["contenu"] for c in candidats.get("prompts", [])] + [
                c["contenu"] for c in candidats.get("documents", [])
            ]
            if not morceaux:
                return "Rien de pertinent trouvé dans la base de connaissances pour cette question."
            return "\n\n---\n\n".join(morceaux)
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (chercher) : {e}")
            return "Erreur : la recherche a échoué, réessaie."

    if action == "lister_articles":
        try:
            res = (
                _supabase_memoire.table("documents")
                .select("nom")
                .eq("agent_id", agent_id)
                .execute()
            )
            noms = sorted({r["nom"] for r in (res.data or [])})
            if not noms:
                return "Aucun article dans la base de connaissances pour l'instant."
            return "\n".join(noms)
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (lister_articles) : {e}")
            return "Erreur : la liste des articles a échoué, réessaie."

    if action == "lire_article":
        try:
            res = (
                _supabase_memoire.table("documents")
                .select("contenu, position")
                .eq("agent_id", agent_id)
                .eq("nom", nom)
                .order("position", desc=False, nullsfirst=False)
                .execute()
            )
            morceaux = res.data or []
            if not morceaux:
                return f"Aucun article nommé '{nom}' trouvé dans la base de connaissances."
            return " ".join(m["contenu"] for m in morceaux)
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (lire_article) : {e}")
            return "Erreur : la lecture de l'article a échoué, réessaie."

    if action == "obtenir_fichier":
        try:
            res = (
                _supabase_memoire.table("documents")
                .select("nom")
                .eq("agent_id", agent_id)
                .eq("nom", nom)
                .limit(1)
                .execute()
            )
            if not res.data:
                return f"Aucun article nommé '{nom}' trouvé dans la base de connaissances."
            url = f"{_SUPABASE_URL}/storage/v1/object/public/documents-agents/{agent_id}/{nom}"
            return f"Fichier : {url}"
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (obtenir_fichier) : {e}")
            return "Erreur : la récupération du fichier a échoué, réessaie."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : chercher, "
        "lister_articles, lire_article, obtenir_fichier."
    )


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
        renvoie un identifiant à donner à consulter_statut_generation
        (type "3d") un peu plus tard. Préviens l'étudiant que ça prend
        un peu de temps.
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
        consulter_statut_generation (type "video") un peu plus tard.
        Préviens l'étudiant que ça prend du temps et qu'il doit
        redemander le statut dans quelques minutes.
        """
        try:
            resultat = _lancer_generation_video(prompt, duree_secondes)
            return (
                f"Génération lancée (id: {resultat['request_id']}). "
                f"Ça prend 1 à 3 minutes, redemande le statut avec cet identifiant un peu plus tard."
            )
        except Exception as e:
            logging.error(f"ERREUR outil generation : {e}")
            return "Erreur : le lancement de la génération vidéo a échoué, réessaie."


# Enregistré conditionnellement, gate par interrupteur dédié (voir
# generation_audio.py : GROQ_API_KEY existe déjà pour le chat, donc ne
# peut pas servir de gate ici, il faut qu'AUDIO_TTS_ACTIF="true" soit
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


if modele_3d_disponible() or signature_disponible():
    @mcp_generation.tool()
    def consulter_statut_generation(type: str, request_id: str) -> str:
        """
        Consulte l'état d'une génération asynchrone déjà lancée
        (modèle 3D, vidéo ou demande de signature), consolidé le 26/08,
        un seul outil, plusieurs types au lieu de 3 outils séparés.
        Même forme pour les trois : un identifiant en entrée, un texte
        de statut en sortie.

        `type` doit être l'un de :
        - "3d" : état d'une génération 3D lancée avec
          lancer_generation_3d. Si terminée, renvoie l'URL publique du
          fichier .glb.
        - "video" : état d'une génération vidéo lancée avec
          lancer_generation_video. Si terminée, renvoie l'URL publique
          de la vidéo. Sinon, indique qu'elle est toujours en cours.
        - "signature" : état d'une demande de signature déjà envoyée
          avec envoyer_pour_signature (en attente, signé, expiré...).

        `request_id` : l'identifiant renvoyé par l'outil de lancement
        correspondant (`request_id` pour "3d"/"video",
        `signature_request_id` pour "signature").
        """
        if type == "3d":
            if not modele_3d_disponible():
                return "Erreur : la génération 3D n'est pas disponible actuellement."
            try:
                resultat = _statut_modele_3d(request_id)
                if resultat["statut"] == "COMPLETED":
                    return f"Modèle 3D prêt : {resultat['url']}"
                return f"Toujours en cours (statut : {resultat['statut']}), redemande un peu plus tard."
            except Exception as e:
                logging.error(f"ERREUR consulter_statut_generation (3d) : {e}")
                return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."

        if type == "video":
            if not video_disponible():
                return "Erreur : la génération vidéo n'est pas disponible actuellement."
            try:
                resultat = _statut_video(request_id)
                if resultat["statut"] == "COMPLETED":
                    return f"Vidéo prête : {resultat['url']}"
                return f"Toujours en cours (statut : {resultat['statut']}), redemande dans une minute."
            except Exception as e:
                logging.error(f"ERREUR consulter_statut_generation (video) : {e}")
                return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."

        if type == "signature":
            if not signature_disponible():
                return "Erreur : la signature électronique n'est pas disponible actuellement."
            try:
                return str(_statut_signature(request_id))
            except Exception as e:
                logging.error(f"ERREUR consulter_statut_generation (signature) : {e}")
                return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."

        return (
            f"Erreur : type '{type}' inconnu. Types valides : 3d, video, signature."
        )


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


# Enregistré conditionnellement, gate par un_canal_push_disponible()
# (voir notifications_push.py) -- élargi le 23/08/2026 (Lot 3 Partie 3
# mobile) : VAPID (navigateur) OU FCM (Android) OU APNs (iOS), le
# rappel part maintenant vers tous les canaux dont dispose
# l'utilisateur, l'outil IA lui-même ne change pas. Outil de ce fichier
# qui a besoin de connaître l'identité de l'appelant (user_id/agent_id) :
# récupérés via ctx.request_context.request.query_params, transmis dans
# l'URL par _url_generation() (registre_outils.py) -- même mécanique
# reprise par envoyer_message ci-dessous. NON TESTÉ EN CONDITIONS
# RÉELLES : si ça échoue au premier essai, vérifier en premier que
# request_context.request est bien accessible dans ce mode
# (stateless_http) -- c'est le point d'incertitude documenté ici.
if un_canal_push_disponible():
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


# Ajouté le 26/08/2026, Bourama : brancher le cerveau sur l'app mobile
# (voir core/actions_appareil_mobile.py). Toujours enregistré (pas de gate
# comme planifier_rappel/un_canal_push_disponible) : même si aucun token
# push n'est enregistré, l'action reste en base et l'app la rattrapera au
# prochain lancement via GET /actions/en-attente (voir
# rattraperActionsEnAttente, plugin PontNatif côté clovis-frontend).
#
# UN SEUL outil générique plutôt qu'un outil par capacité (décision prise
# avec Bourama, 26/08) : colle à ce que core/actions_appareil_mobile.py
# expose déjà (creer_action(type_action, parametres)), zéro outil à
# ajouter/retirer plus tard, juste étendre TYPES_ACTION_MOBILE_VALIDES
# ci-dessous. Contrepartie assumée : pas de schéma strict par type côté
# MCP, d'où la validation stricte ci-dessous et la liste exhaustive dans
# le docstring (seule source de vérité pour le modèle).
TYPES_ACTION_MOBILE_VALIDES = {
    # Dossiers désignés (Lot 2), "dossier_nom"/"nouveau_dossier_nom"
    # ciblent TOUJOURS un nom renvoyé par lister_dossiers_designes_mobile,
    # jamais une URI : l'app résout le nom en URI localement (voir
    # ActionsAppareilExecuteur.kt/.swift, clovis-frontend).
    "dossier_creer_fichier": {"dossier_nom", "nom"},  # "type_mime" optionnel
    "dossier_creer_sous_dossier": {"dossier_nom", "nom"},
    "dossier_renommer": {"dossier_nom", "element_nom", "nouveau_nom"},
    "dossier_supprimer": {"dossier_nom", "element_nom"},
    "dossier_deplacer": {"dossier_nom", "element_nom", "nouveau_dossier_nom"},
    # Accessibilité (Lot 6/7), flavor Android "externe" UNIQUEMENT,
    # échoue proprement sur flavor "play" et sur iOS (pas d'équivalent).
    "accessibilite_cliquer": {"texte_cible"},
    "accessibilite_saisir": {"texte_cible", "valeur"},
}


@mcp_generation.tool()
def gerer_action_mobile(
    action: str,
    ctx: Context,
    type_action: str = "",
    parametres: dict = None,
) -> str:
    """
    Interagit avec l'app Clovis mobile de l'étudiant, consolidé le
    26/08, un seul outil, deux actions.

    `action` doit être l'une de :
    - "lister_dossiers" : liste les noms des dossiers que l'étudiant a
      désignés sur son téléphone (accessibles à l'app Clovis mobile),
      avec la plateforme (android/ios). Utilise TOUJOURS cette action
      avant "executer" pour un type "dossier_*", afin de cibler un nom
      qui existe vraiment, ne devine jamais un nom de dossier. Aucun
      paramètre.
    - "executer" : décide une action à exécuter sur le téléphone de
      l'étudiant. L'action est mise en attente et poussée immédiatement
      au téléphone (ou rattrapée à sa prochaine ouverture s'il est hors
      ligne). LIMITE CONNUE : cette action est asynchrone et son
      résultat (succès/échec) n'est PAS relayé automatiquement dans
      cette conversation pour l'instant, dis à l'étudiant que l'action a
      été envoyée, sans garantir qu'elle a déjà réussi.

      Paramètre `type_action`, EXACTEMENT l'un de :
      - "dossier_creer_fichier" : {"dossier_nom", "nom", "type_mime"?}
      - "dossier_creer_sous_dossier" : {"dossier_nom", "nom"}
      - "dossier_renommer" : {"dossier_nom", "element_nom", "nouveau_nom"}
      - "dossier_supprimer" : {"dossier_nom", "element_nom"}
      - "dossier_deplacer" : {"dossier_nom", "element_nom", "nouveau_dossier_nom"}
      - "accessibilite_cliquer" : {"texte_cible"}
      - "accessibilite_saisir" : {"texte_cible", "valeur"}

      Paramètre `parametres` : objet correspondant au `type_action`
      choisi (voir ci-dessus). Pour un type "dossier_*", "dossier_nom"
      (et "nouveau_dossier_nom" le cas échéant) DOIT être un nom renvoyé
      par l'action "lister_dossiers", appelle-la avant si tu ne connais
      pas déjà la liste à jour. Les actions "accessibilite_*" ne
      fonctionnent que sur la version Android hors Play Store de
      l'étudiant (elles échouent proprement sinon, avec un message clair
      rapporté dans la conversation).
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    if action == "lister_dossiers":
        try:
            dossiers = _lire_dossiers_designes(user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_action_mobile (lister_dossiers) : {e}")
            return "Erreur : impossible de lister les dossiers désignés, réessaie."
        if not dossiers:
            return "Aucun dossier désigné sur le téléphone de l'étudiant pour l'instant."
        return "\n".join(f"- {d['nom']} ({d['plateforme']})" for d in dossiers)

    if action == "executer":
        if type_action not in TYPES_ACTION_MOBILE_VALIDES:
            return (
                f"Erreur : type_action \"{type_action}\" inconnu. Types valides : "
                + ", ".join(sorted(TYPES_ACTION_MOBILE_VALIDES))
            )

        cles_requises = TYPES_ACTION_MOBILE_VALIDES[type_action]
        manquantes = cles_requises - set(parametres or {})
        if manquantes:
            return f"Erreur : paramètres manquants pour \"{type_action}\" : {', '.join(sorted(manquantes))}."

        try:
            _creer_action_mobile(user_id, type_action, parametres)
        except Exception as e:
            logging.error(f"ERREUR gerer_action_mobile (executer, {type_action}) : {e}")
            return "Erreur : impossible de programmer cette action, réessaie."

        return (
            f"Action \"{type_action}\" envoyée au téléphone de l'étudiant. "
            "Le résultat n'est pas encore relayé automatiquement ici : "
            "informe l'étudiant que l'action a été envoyée, sans confirmer "
            "qu'elle a déjà réussi."
        )

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister_dossiers, "
        "executer."
    )