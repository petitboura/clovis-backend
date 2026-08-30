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
from core.lecture_fichier_mobile import lire_contenu_fichier, fichier_trop_volumineux


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


async def ouvrir_sous_dossier(
    user_id: str, dossier_nom: str, chemin: list[str], on_statut=None
) -> dict[str, Any] | None:
    """
    Cree le 30/08/2026, Bourama : Lot 3 (voir 03-navigation-recherche-nom.md).

    Descend depuis le dossier designe `dossier_nom` en suivant `chemin`
    (liste de noms de sous-dossiers a traverser depuis la racine
    designee, ex. ["Maths", "Chapitre 3"]) et renvoie le contenu du
    sous-dossier atteint -- meme format que lister_contenu_dossier.
    Renvoie {"erreur": "..."} si un des noms du chemin n'existe pas
    (chemin errone ou dossier deplace/supprime entre-temps).
    """
    return await poser_question_appareil(
        user_id,
        {"action": "ouvrir_sous_dossier", "dossier_nom": dossier_nom, "chemin": chemin},
        on_statut=on_statut,
    )


async def chercher_par_nom(
    user_id: str, dossier_nom: str, terme_recherche: str, on_statut=None
) -> dict[str, Any] | None:
    """
    Cree le 30/08/2026, Bourama : Lot 3 (voir 03-navigation-recherche-nom.md).

    Parcourt toute l'arborescence a partir du dossier designe
    `dossier_nom` a la recherche d'elements dont le nom contient
    `terme_recherche` (partiel, insensible a la casse -- meme logique
    que chercherNoeud pour l'accessibilite, cote app). Renvoie
    {"elements": [{"nom": ..., "estDossier": bool, "tailleOctets":
    int|None, "chemin": [...]}]} : "chemin" donne la position de chaque
    resultat depuis la racine designee (utilisable ensuite avec
    ouvrir_sous_dossier). Liste vide si rien ne correspond, ce n'est
    PAS une erreur : l'agent peut alors enchainer avec
    chercher_par_contenu (Lot 5) si une recherche dans le nom seul ne
    suffit pas.
    """
    return await poser_question_appareil(
        user_id,
        {"action": "chercher_par_nom", "dossier_nom": dossier_nom, "terme_recherche": terme_recherche},
        on_statut=on_statut,
    )


async def lire_fichier(
    user_id: str, dossier_nom: str, chemin: list[str], on_statut=None
) -> dict[str, Any] | None:
    """
    Cree le 30/08/2026, Bourama : Lot 4 (voir 04-lecture-contenu.md).

    Demande en direct au telephone le contenu brut du fichier situe a
    `chemin` depuis la racine designee `dossier_nom` (liste de noms,
    DERNIER element = nom du fichier, meme convention que le "chemin"
    deja renvoye par chercher_par_nom pour un element trouve). Le
    traitement du contenu recu selon le type de fichier vit dans
    core/lecture_fichier_mobile.py, pas ici : ce module ne fait que
    demander et transmettre, comme les autres fonctions ci-dessus.

    Renvoie :
    - None si l'app n'est pas ouverte sur le telephone ;
    - {"contenu_base64": ..., "type_mime": ..., "nom_fichier": ...,
      "tailleOctets": int|None} si la demande a abouti (le telephone a
      trouve et lu le fichier) ;
    - {"erreur": "..."} si l'app a repondu mais n'a pas trouve ce
      fichier a ce chemin (chemin errone, fichier deplace/supprime
      entre-temps).
    """
    return await poser_question_appareil(
        user_id,
        {"action": "lire_fichier", "dossier_nom": dossier_nom, "chemin": chemin},
        on_statut=on_statut,
    )


async def lister_tous_fichiers(user_id: str, dossier_nom: str, on_statut=None) -> dict[str, Any] | None:
    """
    Cree le 30/08/2026, Bourama : Lot 5 (voir 05-recherche-contenu-app-fermee.md).

    Comme chercher_par_nom, mais SANS filtre sur le nom : parcourt toute
    l'arborescence a partir du dossier designe `dossier_nom` et renvoie
    TOUS les fichiers trouves (jamais les dossiers eux-memes, seulement
    des elements lisibles), chacun avec son chemin depuis la racine
    designee. Reserve a l'usage interne de chercher_par_contenu
    ci-dessous, qui a besoin de la liste complete avant de lire chaque
    fichier un par un : pas expose directement comme action de l'outil
    agent (voir core/serveur_mcp_generation.py::explorer_dossier).
    """
    return await poser_question_appareil(
        user_id,
        {"action": "lister_tous_fichiers", "dossier_nom": dossier_nom},
        on_statut=on_statut,
    )


async def chercher_par_contenu(
    user_id: str, dossier_nom: str, terme_recherche: str, on_statut=None
) -> dict[str, Any] | None:
    """
    Cree le 30/08/2026, Bourama : Lot 5 (voir 05-recherche-contenu-app-fermee.md).

    Cherche `terme_recherche` (insensible a la casse) dans le CONTENU des
    fichiers de l'arborescence sous `dossier_nom` (pas dans leur nom,
    voir chercher_par_nom pour ca). Liste d'abord tous les fichiers
    (lister_tous_fichiers), puis lit chacun un par un via lire_fichier
    (Lot 4) et core.lecture_fichier_mobile pour verifier son contenu.
    Peut donc prendre du temps si le dossier contient beaucoup de
    fichiers : pas de limite de nombre de fichiers pour l'instant (voir
    05-recherche-contenu-app-fermee.md, "point a verifier", a revoir
    seulement si un vrai probleme de lenteur apparait en conditions
    reelles, jamais decide a l'avance sans donnee reelle).

    Un fichier trop volumineux pour etre lu (fichier_trop_volumineux) ou
    dont la lecture echoue est simplement ignore et la recherche continue
    sur les fichiers suivants : un seul fichier illisible ne doit jamais
    faire echouer toute la recherche.

    Renvoie :
    - None IMMEDIATEMENT si l'app se ferme a n'importe quel moment de la
      recherche (au listing initial ou pendant la lecture d'un fichier),
      jamais de resultat partiel presente comme s'il etait complet ;
    - {"elements": [{"nom": ..., "chemin": [...], "extrait": "..."}]} :
      un element par fichier dont le contenu contient `terme_recherche`,
      "extrait" est un court passage du contenu autour de la premiere
      occurrence trouvee (pour que l'agent puisse verifier et citer sans
      relire tout le fichier). Liste vide si rien ne correspond, ce n'est
      PAS une erreur ;
    - {"erreur": "..."} si le listing initial du dossier echoue (dossier
      designe introuvable).
    """
    listing = await lister_tous_fichiers(user_id, dossier_nom, on_statut=on_statut)
    if listing is None:
        return None
    if "erreur" in listing:
        return listing

    terme = terme_recherche.strip().lower()
    resultats = []

    for element in listing.get("elements") or []:
        chemin_element = element.get("chemin") or [element.get("nom")]

        lecture_brute = await lire_fichier(user_id, dossier_nom, chemin_element, on_statut=on_statut)
        if lecture_brute is None:
            return None
        if "erreur" in lecture_brute:
            continue

        # Meme point tranche avec Bourama qu'au Lot 4 (voir
        # 04-lecture-contenu.md) : un fichier trop volumineux n'est pas
        # lu. Ici, en recherche multi-fichiers, on l'ignore simplement
        # plutot que d'interrompre toute la recherche pour le signaler.
        if fichier_trop_volumineux(lecture_brute.get("type_mime") or "", lecture_brute.get("tailleOctets")):
            continue

        contenu_base64 = lecture_brute.get("contenu_base64")
        if not contenu_base64:
            continue

        nom_fichier = lecture_brute.get("nom_fichier") or chemin_element[-1]
        type_mime = lecture_brute.get("type_mime") or ""
        lecture = lire_contenu_fichier(contenu_base64, type_mime, nom_fichier)
        if "erreur" in lecture:
            continue

        texte = lecture["texte"]
        position = texte.lower().find(terme)
        if position == -1:
            continue

        debut = max(0, position - 80)
        fin = min(len(texte), position + len(terme) + 80)
        extrait = texte[debut:fin].strip()

        resultats.append({"nom": nom_fichier, "chemin": chemin_element, "extrait": extrait})

    return {"elements": resultats}
