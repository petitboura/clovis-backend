"""
Calcul symbolique via SymPy -- pas de cle API, pas de service externe :
une dependance Python locale (voir requirements.txt), donc toujours
disponible des que la librairie est installee, comme generer_image.
Complementaire de wolfram (registre_outils.py) : SymPy fait le calcul
formel pur (simplifier/resoudre/deriver/integrer/developper/factoriser/
limite), jamais de connaissance factuelle du monde reel (constantes
physiques, chimie, donnees geographiques...), qui reste le role de
wolfram.

Un seul outil MCP (calculer_symbolique, voir serveur_mcp_generation.py)
plutot que 7 outils separes : toutes ces operations partagent le meme
coeur (parsing d'une expression, meme structure d'erreur), et le modele
choisit l'operation via le parametre `operation` -- evite de faire
gonfler le catalogue d'outils pour des variantes d'un meme geste.
"""

import logging

import sympy
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
    convert_xor,
)

_TRANSFORMATIONS = standard_transformations + (
    implicit_multiplication_application,
    convert_xor,
)

OPERATIONS_SUPPORTEES = {
    "simplifier",
    "developper",
    "factoriser",
    "deriver",
    "integrer",
    "resoudre",
    "limite",
}


class ErreurCalculSymbolique(Exception):
    """Erreur utilisateur (expression invalide, operation inconnue...),
    distincte d'une erreur technique -- pour renvoyer un message clair
    au lieu d'un plantage brut."""


def _parser(expression_str, variable_str):
    """
    Parse une expression donnee en notation naturelle par un etudiant
    (ex: "2x^2 + 3x - 5", "sin(x)*cos(x)") plutot que la syntaxe stricte
    Python attendue par sympy nativement (2*x**2). `convert_xor` accepte
    "^" comme puissance, `implicit_multiplication_application` accepte
    "2x" comme "2*x".
    """
    try:
        variable = sympy.symbols(variable_str)
        expression = parse_expr(expression_str, transformations=_TRANSFORMATIONS)
        return expression, variable
    except Exception as e:
        raise ErreurCalculSymbolique(
            f"Expression illisible : \"{expression_str}\" ({e})"
        )


def _borne(valeur_str):
    """Convertit une borne d'integrale/limite -- accepte 'oo'/'-oo' pour
    l'infini (notation sympy), sinon une expression numerique classique."""
    if valeur_str is None:
        return None
    if valeur_str.strip() in ("oo", "+oo", "inf", "+inf"):
        return sympy.oo
    if valeur_str.strip() in ("-oo", "-inf"):
        return -sympy.oo
    return parse_expr(valeur_str, transformations=_TRANSFORMATIONS)


def _formater(resultat):
    """
    Renvoie a la fois la forme LaTeX (rendue en KaTeX dans le chat, voir
    core/generation_latex.py et le front) et une version texte brute en
    repli. Le modele recoit les deux et choisit comment les integrer a
    sa reponse.
    """
    return {
        "latex": sympy.latex(resultat),
        "texte": str(resultat),
    }


def calculer_symbolique(
    operation,
    expression,
    variable="x",
    ordre=1,
    borne_inf=None,
    borne_sup=None,
    point=None,
):
    """
    Coeur du calcul, appele par l'outil MCP (voir
    serveur_mcp_generation.py). Leve ErreurCalculSymbolique pour toute
    entree invalide -- l'appelant se charge de la convertir en message
    utilisateur.
    """
    if operation not in OPERATIONS_SUPPORTEES:
        raise ErreurCalculSymbolique(
            f"Operation \"{operation}\" inconnue (attendu : "
            f"{', '.join(sorted(OPERATIONS_SUPPORTEES))})."
        )

    if operation == "resoudre":
        # Equation complete ("2x + 3 = 7") ou expression seule supposee
        # egale a zero ("2x + 3").
        if "=" in expression:
            gauche_str, droite_str = expression.split("=", 1)
            gauche, variable_sym = _parser(gauche_str, variable)
            droite, _ = _parser(droite_str, variable)
            equation = sympy.Eq(gauche, droite)
        else:
            expr, variable_sym = _parser(expression, variable)
            equation = sympy.Eq(expr, 0)
        try:
            solutions = sympy.solve(equation, variable_sym)
        except Exception as e:
            raise ErreurCalculSymbolique(f"Resolution impossible : {e}")
        if not solutions:
            return {"latex": "\\text{Aucune solution}", "texte": "Aucune solution"}
        return _formater(solutions)

    expr, variable_sym = _parser(expression, variable)

    if operation == "simplifier":
        return _formater(sympy.simplify(expr))

    if operation == "developper":
        return _formater(sympy.expand(expr))

    if operation == "factoriser":
        return _formater(sympy.factor(expr))

    if operation == "deriver":
        return _formater(sympy.diff(expr, variable_sym, ordre))

    if operation == "integrer":
        if borne_inf is not None and borne_sup is not None:
            a = _borne(borne_inf)
            b = _borne(borne_sup)
            try:
                resultat = sympy.integrate(expr, (variable_sym, a, b))
            except Exception as e:
                raise ErreurCalculSymbolique(f"Integrale impossible a calculer : {e}")
            return _formater(resultat)
        try:
            resultat = sympy.integrate(expr, variable_sym)
        except Exception as e:
            raise ErreurCalculSymbolique(f"Integrale impossible a calculer : {e}")
        # Primitive : + C implicite, precise en texte pour eviter toute
        # ambiguite (une primitive n'est definie qu'a une constante pres).
        formate = _formater(resultat)
        formate["latex"] += " + C"
        formate["texte"] += " + C"
        return formate

    if operation == "limite":
        if point is None:
            raise ErreurCalculSymbolique(
                "L'operation \"limite\" necessite le parametre `point`."
            )
        point_val = _borne(point)
        try:
            resultat = sympy.limit(expr, variable_sym, point_val)
        except Exception as e:
            raise ErreurCalculSymbolique(f"Limite impossible a calculer : {e}")
        return _formater(resultat)

    # Ne devrait jamais etre atteint (garde en tete de fonction), mais
    # explicite plutot qu'un retour implicite None.
    raise ErreurCalculSymbolique(f"Operation \"{operation}\" non implementee.")
