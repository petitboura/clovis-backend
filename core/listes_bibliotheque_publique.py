"""
Listes pays/classe/catégorie de la bibliothèque publique (02/09/2026,
demande Bourama : en plus du filtre par type déjà existant, 3 nouveaux
filtres cochables par le publieur au moment de publier un dossier ou un
fichier -- pays, classe/niveau, catégorie).

Décisions confirmées par Bourama :
- Liste SÉPARÉE de la table `categories` déjà utilisée pour classer les
  IA, pas de réutilisation.
- Champs optionnels à la publication (on peut publier sans rien
  remplir).
- "Autre" : si la valeur tapée n'existe pas encore dans la liste, elle
  y est ajoutée au passage (pour réapparaître en suggestion la
  prochaine fois) -- jamais une liste fermée qui bloquerait la
  publication.

Limite connue (pas demandée, pas traitée pour l'instant) : la
comparaison d'unicité est sensible à la casse/aux espaces au-delà du
strip -- "Mali" et "mali" créeraient deux entrées distinctes.
"""

import logging
import os
import sys

from postgrest.exceptions import APIError

sys.path.append(os.path.join(os.path.dirname(__file__)))
from bibliotheque_fichiers import supabase  # noqa: E402

TABLES = {
    "pays": "bibliotheque_publique_pays",
    "classe": "bibliotheque_publique_classes",
    "categorie": "bibliotheque_publique_categories",
}


def lister_valeurs(champ: str) -> list[str]:
    """Valeurs déjà connues pour un champ ("pays"/"classe"/"categorie"), pour peupler les suggestions du formulaire côté frontend."""
    table = TABLES[champ]
    try:
        res = supabase.table(table).select("nom").order("nom").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture {table}) : {e}")
        return []
    return [ligne["nom"] for ligne in (res.data or [])]


def normaliser_et_enregistrer(champ: str, valeur: str | None) -> str | None:
    """
    Nettoie la valeur envoyée par le formulaire de publication et
    l'ajoute à la liste si elle n'y est pas déjà (cas "Autre") -- ne
    renvoie jamais d'erreur au publieur, un souci d'écriture ici ne
    doit jamais bloquer la publication du fichier/dossier lui-même.
    """
    valeur = (valeur or "").strip()
    if not valeur:
        return None
    table = TABLES[champ]
    try:
        supabase.table(table).insert({"nom": valeur}).execute()
    except APIError as e:
        if getattr(e, "code", None) != "23505":  # 23505 = déjà dans la liste, normal
            logging.error(f"ERREUR ECRITURE {table} (valeur={valeur}) : {e}")
    except Exception as e:
        logging.error(f"ERREUR ECRITURE {table} (valeur={valeur}) : {e}")
    return valeur
