"""
Cree le 30/08/2026, Bourama : Lot 2 Partie 3 (app mobile), chantier
"Exploration de dossier en temps reel" (voir 00-commun-exploration-dossier.md
et 02-outil-exploration.md a la racine du depot).

Logique "dossier" du canal temps reel : core/canal_temps_reel.py (Lot 1)
reste generique, sans aucune notion de dossier dedans -- c'est ici que
vit la premiere vraie capacite d'exploration.

A NE PAS CONFONDRE avec :
- core/actions_appareil_mobile.py : fire-and-forget (creer/renommer/
  deplacer/supprimer), resultat differe, hors de la conversation en cours.
- core/dossiers_designes_mobile.py : miroir en base des NOMS de dossiers
  designes seulement, jamais leur contenu reel.
Ici, la demande part EN DIRECT vers le telephone via
core/canal_temps_reel.poser_question_appareil et la reponse arrive dans
le meme tour de raisonnement de l'agent.
"""

from typing import Any

from core.canal_temps_reel import poser_question_appareil


async def lister_contenu_dossier(user_id: str, dossier_nom: str, on_statut=None) -> dict[str, Any] | None:
    """
    Demande en direct au telephone de l'etudiant le contenu du dossier
    designe `dossier_nom`.

    Renvoie :
    - None si l'app n'est pas ouverte sur le telephone (voir
      poser_question_appareil) ;
    - {"elements": [{"nom": ..., "estDossier": bool, "tailleOctets":
      int|None}, ...]} si la demande a abouti (l'app repond via
      DossiersPlugin.listerContenu, voir clovis-frontend) ;
    - {"erreur": "..."} si l'app a repondu mais n'a pas trouve ce
      dossier designe (ex: retire par l'etudiant depuis, ou nom d'une
      autre plateforme que celle actuellement connectee).
    """
    return await poser_question_appareil(
        user_id,
        {"action": "lister_contenu", "dossier_nom": dossier_nom},
        on_statut=on_statut,
    )
