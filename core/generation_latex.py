"""
Génération de fichier LaTeX (.tex) téléchargeable.

Distinct du rendu KaTeX à l'écran (Affichage > Maths, `remark-math` +
`rehype-katex` côté frontend) : celui-là affiche la formule DANS le chat,
celui-ci produit un vrai fichier .tex réutilisable ailleurs (Overleaf,
un éditeur LaTeX local, un devoir à rendre). Les deux sont indépendants
-- une formule peut très bien s'afficher en KaTeX sans jamais être
exportée, et inversement.

Gratuit et local (aucune compilation, pas de clé API requise) : le
fichier généré est le SOURCE .tex, pas un PDF compilé -- compiler du
LaTeX nécessiterait une distribution complète (TeX Live, plusieurs
centaines de Mo) qu'on ne veut pas embarquer sur Railway. Si un PDF
compilé est un jour nécessaire, ce serait un second outil séparé, pas
une extension de celui-ci.

Réutilise le même bucket Supabase "generations" que les autres modules
de génération (dossier "latex/" au lieu de "documents/" ou "code/").
"""

import logging
import re
import uuid

from api.auth import supabase

BUCKET = "generations"

# Préambule minimal mais couvrant les usages maths/chimie attendus pour
# Djiguignè AI : amsmath/amssymb/amsfonts pour les maths avancées,
# mhchem pour la chimie (même extension KaTeX déjà utilisée côté
# affichage, donc la même syntaxe \ce{...} fonctionne des deux côtés).
# Volontairement sobre (pas de logo/charte Djiguignè ici, contrairement
# au CSS du PDF dans generation_documents.py) : un fichier .tex est fait
# pour être repris et modifié tel quel par la personne, pas pour rester
# figé avec un habillage imposé.
_PREAMBULE = r"""\documentclass[12pt]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[french]{babel}
\usepackage{amsmath, amssymb, amsfonts}
\usepackage{mhchem}
\usepackage[margin=2.5cm]{geometry}

\title{%s}
\date{}

\begin{document}
\maketitle

%s

\end{document}
"""

_CARACTERES_SPECIAUX_LATEX = {
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
}


def _echapper_titre(titre: str) -> str:
    """
    Échappe les caractères spéciaux LaTeX dans le titre uniquement --
    jamais dans le corps (contenu_latex), qui est censé être du vrai
    LaTeX écrit par le modèle et ne doit pas être altéré.
    """
    resultat = titre
    for caractere, echappement in _CARACTERES_SPECIAUX_LATEX.items():
        resultat = resultat.replace(caractere, echappement)
    return resultat


def generer_fichier_latex(titre: str, contenu_latex: str) -> str:
    """
    Génère un fichier .tex téléchargeable, l'upload dans Supabase
    Storage, renvoie l'URL publique.

    Si `contenu_latex` contient déjà `\\documentclass`, il est utilisé
    tel quel (le modèle a fourni un document complet, autonomie totale
    laissée) -- sinon il est enveloppé dans le préambule standard
    ci-dessus, pour que le modèle n'ait à écrire que le corps (formules,
    texte) sans repartir de zéro à chaque fois.

    Même contrat d'erreur que les autres modules de génération : les
    exceptions remontent telles quelles, à l'appelant (serveur MCP) de
    les transformer en message utilisateur clair.
    """
    if "\\documentclass" in contenu_latex:
        document_complet = contenu_latex
    else:
        document_complet = _PREAMBULE % (_echapper_titre(titre), contenu_latex)

    nom_fichier_sur = re.sub(r"[^a-zA-Z0-9-_]+", "_", titre).strip("_") or "document"
    chemin_stockage = f"latex/{uuid.uuid4()}-{nom_fichier_sur}.tex"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage,
            document_complet.encode("utf-8"),
            {"content-type": "application/x-tex"},
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload latex {chemin_stockage}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)
