"""
Outils MCP de génération de documents et de code (Word, Excel, PowerPoint,
LaTeX, code, calcul symbolique, export de données, site statique).

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code.
"""

import logging

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
from core.calcul_symbolique import calculer_symbolique as _calculer_symbolique, ErreurCalculSymbolique
from core.generation_site import (
    deployer_site as _deployer_site,
    site_deploiement_disponible,
)

from core.outils_generation_commun import mcp_generation, Context, _sauvegarder_generation_bibliotheque



@mcp_generation.tool()
def generer_document(titre: str, contenu_markdown: str, ctx: Context) -> str:
    """
    Génère un document PDF à partir d'un titre et d'un contenu au format
    markdown (titres, listes, tableaux, blocs de code supportés).
    Renvoie l'URL publique du PDF généré, prête à être partagée à
    l'étudiant.
    """
    try:
        url = generer_pdf_depuis_markdown(titre, contenu_markdown)
        _sauvegarder_generation_bibliotheque(ctx, url, f"{titre}.pdf", "application/pdf")
        return url
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
def generer_document_word(titre: str, contenu_markdown: str, ctx: Context) -> str:
    """
    Génère un document Word (.docx) à partir d'un titre et d'un contenu
    markdown (titres #/##/### et paragraphes supportés, pas de mise en
    forme avancée). Renvoie l'URL publique du fichier, et si possible un
    aperçu PDF prêt à afficher directement dans le chat.
    """
    try:
        resultat = _generer_docx(titre, contenu_markdown)
        _sauvegarder_generation_bibliotheque(
            ctx, resultat["url"], f"{titre}.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        return _formater_resultat_document(resultat)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du document Word a échoué, réessaie."


@mcp_generation.tool()
def generer_document_excel(titre: str, en_tetes: list, lignes: list, ctx: Context) -> str:
    """
    Génère un classeur Excel (.xlsx) à une feuille. `en_tetes` : liste
    de noms de colonnes, ex. ["Nom", "Note"]. `lignes` : liste de
    listes de valeurs, une sous-liste par ligne, ex.
    [["Awa", 15], ["Ibrahim", 12]]. Renvoie l'URL publique du fichier,
    et si possible un aperçu PDF prêt à afficher directement dans le
    chat.
    """
    try:
        resultat = _generer_xlsx(titre, en_tetes, lignes)
        _sauvegarder_generation_bibliotheque(
            ctx, resultat["url"], f"{titre}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        return _formater_resultat_document(resultat)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du classeur Excel a échoué, réessaie."


@mcp_generation.tool()
def generer_document_powerpoint(titre: str, diapositives: list, ctx: Context) -> str:
    """
    Génère une présentation PowerPoint (.pptx). `diapositives` : liste
    de dicts {"titre": ..., "contenu": ...}, une diapositive titre+texte
    par élément (en plus d'une diapositive de titre générée
    automatiquement à partir de `titre`). Renvoie l'URL publique du
    fichier, et si possible un aperçu PDF prêt à afficher directement
    dans le chat.
    """
    try:
        resultat = _generer_pptx(titre, diapositives)
        _sauvegarder_generation_bibliotheque(
            ctx, resultat["url"], f"{titre}.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        return _formater_resultat_document(resultat)
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération de la présentation PowerPoint a échoué, réessaie."


@mcp_generation.tool()
def generer_code(nom_projet: str, fichiers: dict, ctx: Context) -> str:
    """
    Génère un fichier de code téléchargeable à partir d'un ou plusieurs
    fichiers. `fichiers` est un dictionnaire {chemin: contenu}, ex.
    {"main.py": "print('hello')"}. Un seul fichier -> renvoie directement
    ce fichier (pas de zip). Plusieurs fichiers -> archive .zip. Renvoie
    l'URL publique du fichier ou de l'archive.
    """
    try:
        url = generer_zip_depuis_fichiers(nom_projet, fichiers)
        # Nom réel déduit de l'URL (single file -> son propre nom, sinon
        # -> archive .zip du projet) plutôt que deviné ici.
        nom_reel = url.rsplit("/", 1)[-1].split("?", 1)[0]
        type_mime = "application/zip" if len(fichiers) > 1 else "text/plain"
        _sauvegarder_generation_bibliotheque(ctx, url, nom_reel, type_mime)
        return url
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du fichier a échoué, réessaie."


@mcp_generation.tool()
def generer_document_latex(titre: str, contenu_latex: str, ctx: Context) -> str:
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
        url = _generer_fichier_latex(titre, contenu_latex)
        _sauvegarder_generation_bibliotheque(ctx, url, f"{titre}.tex", "application/x-tex")
        return f"Fichier LaTeX généré : {url}"
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
def generer_site_zip(nom_projet: str, fichiers: dict, ctx: Context) -> str:
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
        url = generer_zip_depuis_fichiers(nom_projet, fichiers)
        nom_reel = url.rsplit("/", 1)[-1].split("?", 1)[0]
        type_mime = "application/zip" if len(fichiers) > 1 else "text/plain"
        _sauvegarder_generation_bibliotheque(ctx, url, nom_reel, type_mime)
        return url
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du site (zip) a échoué, réessaie."



@mcp_generation.tool()
def generer_bundle(nom_projet: str, elements: list, ctx: Context) -> str:
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
        url = _generer_bundle(nom_projet, elements)
        _sauvegarder_generation_bibliotheque(ctx, url, f"{nom_projet}.zip", "application/zip")
        return url
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : la génération du bundle a échoué, réessaie."


@mcp_generation.tool()
def exporter_donnees(nom: str, donnees: dict, format: str = "json", ctx: Context = None) -> str:
    """
    Exporte des données structurées (un dictionnaire, potentiellement
    imbriqué) vers un fichier JSON ou XML téléchargeable. `format` doit
    valoir "json" ou "xml". Renvoie l'URL publique du fichier généré.
    """
    try:
        url = _exporter_donnees(nom, donnees, format)
        _sauvegarder_generation_bibliotheque(
            ctx, url, f"{nom}.{format}", "application/json" if format == "json" else "application/xml"
        )
        return url
    except Exception as e:
        logging.error(f"ERREUR outil generation : {e}")
        return "Erreur : l'export des données a échoué, réessaie."



# Enregistré conditionnellement, gate par VERCEL_API_TOKEN (voir
# generation_site.py). generer_site_zip (juste au-dessus, non
# conditionnel) reste toujours disponible pour le cas "code seul" :
# seul ce second outil, le déploiement en ligne, dépend de la clé.
# 02/09/2026, demande Bourama (auto-sauvegarde bibliothèque de tout ce
# que l'IA génère) : volontairement NON branché ici -- un déploiement
# Vercel est un site en ligne, pas un fichier téléchargeable, il n'y a
# rien de concret à ranger dans la bibliothèque (contrairement à
# generer_site_zip juste au-dessus, qui lui produit une vraie archive).
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
