"""
Outils MCP liés au téléphone de l'étudiant (dossiers désignés, exploration
de fichiers) -- Partie 3 (mobile) du chantier programme adaptatif.

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code.
"""

import logging
import base64
from collections import Counter
from anyio import to_thread

from core.actions_appareil_mobile import creer_action as _creer_action_mobile
from core.actions_appareil_mobile import attendre_resultat_action as _attendre_resultat_action_mobile
from core.exploration_dossier_mobile import (
    chercher_par_nom as _chercher_par_nom,
    lister_contenu_dossier as _lister_contenu_dossier,
    ouvrir_sous_dossier as _ouvrir_sous_dossier,
    lire_fichier as _lire_fichier,
    chercher_par_contenu as _chercher_par_contenu,
)
from core.lecture_fichier_mobile import (
    lire_contenu_fichier as _lire_contenu_fichier,
    fichier_trop_volumineux as _fichier_trop_volumineux,
)
from core.dossiers_designes_mobile import (
    lire_dossiers_designes as _lire_dossiers_designes,
    resoudre_appareil_cible as _resoudre_appareil_cible,
)
from core.vectorisation_dossiers_designes import (
    chercher_dossiers_designes as _chercher_dossiers_designes,
    formater_source_dossier_designe as _formater_source_dossier_designe,
)
from core.bibliotheque_fichiers import enregistrer_fichier as _enregistrer_fichier

from core.outils_generation_commun import mcp_generation, Context, _TAILLE_MAX_OCTETS_BIBLIOTHEQUE



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
    #
    # "chemin"/"nouveau_chemin" (01/09/2026, demande Bourama) : listes
    # OPTIONNELLES de noms de sous-dossiers PARENTS (jamais l'élément
    # ciblé lui-même, qui reste "element_nom"/"nom") -- même convention
    # que "chemin" dans explorer_dossier. Absent ou vide = racine du
    # dossier désigné. Profondeur illimitée. "chemin" cible l'emplacement
    # SOURCE (où créer, où trouver l'élément à renommer/supprimer/
    # déplacer) ; "nouveau_chemin" (dossier_deplacer uniquement) cible
    # l'emplacement DESTINATION, à l'intérieur de "nouveau_dossier_nom".
    # Un sous-dossier peut être "element_nom" comme un fichier : les deux
    # sont des cibles valides pour renommer/supprimer/déplacer.
    "dossier_creer_fichier": {"dossier_nom", "nom"},  # "type_mime"/"chemin" optionnels
    "dossier_creer_sous_dossier": {"dossier_nom", "nom"},  # "chemin" optionnel
    "dossier_renommer": {"dossier_nom", "element_nom", "nouveau_nom"},  # "chemin" optionnel
    "dossier_supprimer": {"dossier_nom", "element_nom"},  # "chemin" optionnel
    "dossier_deplacer": {"dossier_nom", "element_nom", "nouveau_dossier_nom"},  # "chemin"/"nouveau_chemin" optionnels
    # Accessibilité (Lot 6/7) retirée le 01/09/2026 (demande Bourama) :
    # l'agent ne peut plus déclencher accessibilite_cliquer/
    # accessibilite_saisir. Le plugin/flavor "externe" reste inchangé côté
    # app mobile (portée de la désactivation limitée à l'agent) -- ce
    # n'est donc plus qu'une capacité dormante côté téléphone, sans aucun
    # moyen de la déclencher tant qu'elle n'est pas réintroduite ici.
}


@mcp_generation.tool()
def gerer_dossier_telephone(
    action: str,
    ctx: Context,
    type_action: str = "",
    parametres: dict = None,
    appareil_nom: str = "",
) -> str:
    """
    Gère les DOSSIERS ET FICHIERS PHYSIQUES du téléphone de l'étudiant
    (renommé le 01/09/2026, ex-gerer_action_mobile -- l'accessibilité a
    été retirée de cet outil, il ne gère plus QUE les dossiers désignés
    sur l'appareil). Un seul outil, deux actions.

    `appareil_nom` (ajouté le 04/09/2026, utile pour "executer"
    seulement) : l'étudiant peut avoir DÉSIGNÉ le même nom de dossier
    sur plusieurs de ses téléphones -- dans ce cas seulement, "lister_dossiers"
    affiche l'appareil entre crochets à côté du nom concerné (ex.
    "- Cours [appareil: iPhone d'Amadou]"). Si "executer" cible un nom
    ambigu, l'erreur renvoyée liste les appareils possibles : rappelle
    alors l'outil avec "appareil_nom" reprenant EXACTEMENT le libellé
    entre crochets. Ne jamais inventer ni deviner ce libellé. Laisser
    vide dès qu'un seul appareil possède ce nom (cas normal).

    NE PAS CONFONDRE avec :
    - gerer_dossier_bibliotheque : gère les dossiers de la bibliothèque
      PERSONNELLE de l'étudiant dans Clovis (organisation de SES
      documents/liens/notes uploadés dans l'app) -- aucun rapport avec
      son téléphone physique.
    - gerer_document_bibliotheque : cherche/lit des documents, y compris
      dans le catalogue PUBLIC partagé -- là non plus aucun rapport avec
      le téléphone de l'étudiant.
    - explorer_dossier : lecture EN DIRECT (app ouverte requise) du
      contenu d'un dossier du téléphone -- utilise cet outil-ci
      uniquement pour AGIR (créer/renommer/supprimer/déplacer), jamais
      pour lire ou lister en détail.

    `action` doit être l'une de :
    - "lister_dossiers" : liste les noms des dossiers que l'étudiant a
      désignés sur son téléphone (accessibles à l'app Clovis mobile).
      Utilise TOUJOURS cette action avant "executer" pour un type
      "dossier_*", afin de cibler un nom qui existe vraiment, ne devine
      jamais un nom de dossier. Chaque ligne renvoyée est le nom EXACT à
      réutiliser comme "dossier_nom" -- tout ce qui suit entre crochets
      "[appareil: ...]", quand présent, est une INDICATION SÉPARÉE, à ne
      JAMAIS coller au nom ni inclure dans "dossier_nom". Aucun
      paramètre.
    - "executer" : décide une action à exécuter sur le téléphone de
      l'étudiant. L'action est mise en attente et poussée immédiatement
      au téléphone. Depuis le 01/09/2026, cet appel ATTEND jusqu'à ~10s
      la confirmation réelle du téléphone avant de répondre : le message
      renvoyé reflète le VRAI résultat (succès/échec) si l'app a
      confirmé à temps, sinon un message explicite "pas encore
      confirmée" (app fermée/hors ligne, l'action reste en attente et
      sera rattrapée plus tard). Attends toujours ce résultat avant de
      lancer une action suivante qui en dépendrait (ex: déplacer un
      fichier juste renommé).

      Paramètre `type_action`, EXACTEMENT l'un de :
      - "dossier_creer_fichier" : {"dossier_nom", "nom", "type_mime"?, "chemin"?}
      - "dossier_creer_sous_dossier" : {"dossier_nom", "nom", "chemin"?}
      - "dossier_renommer" : {"dossier_nom", "element_nom", "nouveau_nom", "chemin"?}
      - "dossier_supprimer" : {"dossier_nom", "element_nom", "chemin"?}
      - "dossier_deplacer" : {"dossier_nom", "element_nom", "nouveau_dossier_nom", "chemin"?, "nouveau_chemin"?}

      Paramètre `parametres` : objet correspondant au `type_action`
      choisi (voir ci-dessus). "dossier_nom" (et "nouveau_dossier_nom"
      le cas échéant) DOIT être un nom renvoyé par l'action
      "lister_dossiers", appelle-la avant si tu ne connais pas déjà la
      liste à jour.

      "chemin" (liste de noms de sous-dossiers, ex. ["Cours", "Maths"]) :
      OPTIONNEL, cible un sous-dossier niché à n'importe quelle
      profondeur SOUS "dossier_nom" -- absent ou vide = racine du
      dossier désigné. Ne contient JAMAIS l'élément visé lui-même
      ("element_nom"/"nom" restent séparés). Un sous-dossier peut lui
      aussi être "element_nom" (renommer/supprimer/déplacer un
      sous-dossier entier fonctionne comme pour un fichier). Utilise
      "explorer_dossier" avant si tu ne connais pas déjà l'arborescence
      exacte, ne devine jamais un chemin. Pour "dossier_deplacer",
      "nouveau_chemin" cible de la même façon l'emplacement niché de
      DESTINATION à l'intérieur de "nouveau_dossier_nom".
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    if action == "lister_dossiers":
        try:
            dossiers = _lire_dossiers_designes(user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_telephone (lister_dossiers) : {e}")
            return "Erreur : impossible de lister les dossiers désignés, réessaie."
        if not dossiers:
            return "Aucun dossier désigné sur le téléphone de l'étudiant pour l'instant."

        # Corrige le 04/09/2026, Bourama : la plateforme n'est plus
        # affichée systematiquement collee au nom (ex. "Download
        # (android)"), qui amenait parfois l'agent a la reprendre comme
        # si elle faisait partie du nom lui-meme, faisant echouer toute
        # action suivante ("Download (android)" introuvable, seul
        # "Download" existe vraiment). L'appareil n'est indique QUE
        # quand deux appareils partagent le meme nom de dossier, entre
        # crochets pour bien le distinguer visuellement du nom.
        comptage = Counter(d["nom"] for d in dossiers)
        lignes = []
        for d in dossiers:
            if comptage[d["nom"]] > 1:
                libelle_appareil = d.get("appareil_nom") or d["plateforme"]
                lignes.append(f"- {d['nom']} [appareil: {libelle_appareil}]")
            else:
                lignes.append(f"- {d['nom']}")
        return "\n".join(lignes)

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

        for cle_chemin in ("chemin", "nouveau_chemin"):
            valeur = (parametres or {}).get(cle_chemin)
            if valeur is not None and (
                not isinstance(valeur, list) or not all(isinstance(s, str) for s in valeur)
            ):
                return f"Erreur : \"{cle_chemin}\" doit être une liste de noms de sous-dossiers."

        # Ajoute le 04/09/2026, Bourama : resout l'appareil PRECIS
        # proprietaire de "dossier_nom" AVANT de creer l'action -- sans
        # ca, avec deux telephones designant un dossier du meme nom,
        # l'action partait en diffusion large et pouvait etre executee
        # (ou echouer) sur le mauvais appareil, voir
        # core/dossiers_designes_mobile.resoudre_appareil_cible.
        dossier_nom_vise = (parametres or {}).get("dossier_nom")
        appareil_id_cible, erreur_resolution = (None, None)
        if dossier_nom_vise:
            appareil_id_cible, erreur_resolution = _resoudre_appareil_cible(
                user_id, dossier_nom_vise, appareil_nom or None
            )
            if erreur_resolution:
                return f"Erreur : {erreur_resolution}"

        try:
            action_id = _creer_action_mobile(user_id, type_action, parametres, appareil_id_cible)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_telephone (executer, {type_action}) : {e}")
            return "Erreur : impossible de programmer cette action, réessaie."

        action_terminee = _attendre_resultat_action_mobile(action_id, user_id)
        if action_terminee is None:
            return (
                f"Action \"{type_action}\" envoyée au téléphone de l'étudiant, mais "
                "pas encore confirmée (app peut-être fermée ou en arrière-plan) : "
                "informe l'étudiant que l'action est en attente, ne dis PAS qu'elle "
                "a réussi, et ne lance PAS une action suivante qui en dépendrait."
            )
        if action_terminee.get("statut") == "echouee":
            return f"Échec de l'action \"{type_action}\" sur le téléphone : {action_terminee.get('resultat') or 'raison inconnue'}."
        return action_terminee.get("resultat") or f"Action \"{type_action}\" exécutée avec succès sur le téléphone."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister_dossiers, "
        "executer."
    )


# Ajouté le 30/08/2026, Bourama : Lot 2, chantier "Exploration de dossier
# en temps réel" (voir 00-commun-exploration-dossier.md et
# 02-outil-exploration.md). Outil SÉPARÉ de gerer_dossier_telephone ci-dessus
# (celui-ci reste dédié au fire-and-forget) : ici la réponse arrive tout
# de suite, dans le même tour de raisonnement, en interrogeant le
# téléphone EN DIRECT via le canal temps réel (core/canal_temps_reel.py,
# Lot 1) -- pas une lecture d'une table miroir en base comme
# "lister_dossiers" ci-dessus.
def _formatter_elements_dossier(elements: list) -> str:
    lignes = []
    for element in elements:
        nom = element.get("nom")
        chemin = element.get("chemin")
        libelle = "/".join(chemin) if chemin else nom
        extrait = element.get("extrait")
        if extrait:
            # Resultat de chercher_par_contenu (Lot 5) : pas de notion de
            # dossier/fichier ici, seulement un extrait du contenu trouve.
            lignes.append(f"- {libelle} : \"...{extrait}...\"")
        elif element.get("estDossier"):
            lignes.append(f"- {libelle} (dossier)")
        else:
            taille = element.get("tailleOctets")
            suffixe = f", {taille} octets" if taille is not None else ""
            lignes.append(f"- {libelle} (fichier{suffixe})")
    return "\n".join(lignes)


@mcp_generation.tool()
async def explorer_dossier(
    action: str,
    ctx: Context,
    dossier_nom: str = "",
    chemin: list[str] = None,
    terme_recherche: str = "",
    appareil_nom: str = "",
) -> str:
    """
    Explore EN DIRECT le contenu d'un dossier désigné par l'étudiant sur
    son téléphone (contrairement à gerer_dossier_telephone, qui est
    asynchrone et fire-and-forget, et sert à AGIR sur les dossiers, pas
    à les lire ou les explorer). NÉCESSITE que l'app Clovis soit ouverte
    sur le téléphone au moment de l'appel, sinon échoue avec un message
    clair à relayer à l'étudiant. NE PAS CONFONDRE non plus avec
    gerer_dossier_bibliotheque/gerer_document_bibliotheque, qui portent
    sur la bibliothèque Clovis (privée ou catalogue public), jamais sur
    le téléphone physique de l'étudiant.

    `action` doit être l'une de :
    - "lister_contenu" : liste le contenu du dossier désigné
      `dossier_nom` à l'instant présent (noms, tailles, type
      fichier/dossier). `dossier_nom` DOIT être un nom renvoyé par
      l'action "lister_dossiers" de l'outil gerer_dossier_telephone --
      appelle-la avant si tu ne connais pas déjà la liste à jour, ne
      devine jamais un nom de dossier.
    - "ouvrir_sous_dossier" : descend dans l'arborescence depuis
      `dossier_nom` en suivant `chemin` (liste ordonnée de noms de
      sous-dossiers vus dans un listing précédent, ex. ["Maths",
      "Chapitre 3"]), et renvoie le contenu du sous-dossier atteint.
      Utilise ceci pour continuer à descendre plutôt que de deviner un
      chemin : chaque nom de `chemin` doit venir d'un listing déjà vu.
    - "chercher_par_nom" : cherche `terme_recherche` (partiel, insensible
      à la casse) dans toute l'arborescence sous `dossier_nom`, sans
      avoir à lister niveau par niveau. Renvoie les éléments trouvés,
      chacun avec son chemin depuis la racine désignée (réutilisable
      ensuite avec "ouvrir_sous_dossier"). Si rien n'est trouvé et que la
      demande de l'étudiant porte sur un contenu ("le cours où on parle
      de...") plutôt que sur un nom précis, enchaîne avec
      "chercher_par_contenu" plutôt que de conclure trop vite qu'il
      n'existe pas.
    - "lire_fichier" : lit vraiment le contenu d'un fichier déjà repéré
      via un listing ou une recherche précédente. `chemin` DOIT être le
      chemin exact vu dans ce listing/cette recherche (dernier élément =
      nom du fichier), ne devine jamais un chemin. Renvoie un texte
      exploitable quel que soit le type de fichier (texte brut lu tel
      quel, texte extrait d'un PDF/Word/Excel, description d'une image,
      transcription d'un audio). Si le fichier est trop volumineux,
      dis clairement à l'étudiant que ce n'est pas encore possible mais
      que ça arrivera plus tard, n'essaie pas de contourner.
    - "donner_fichier" : ENVOIE le fichier lui-même en pièce jointe dans
      le chat (pas juste son contenu/résumé, voir "lire_fichier" pour
      ça), identifié par `chemin` (même convention que "lire_fichier" --
      DOIT venir d'un listing/recherche précédent, ne devine jamais).
      Utilise cette action dès que l'étudiant demande explicitement le
      fichier ("donne-moi ce fichier", "envoie-moi cette photo depuis
      mon téléphone"), ou quand le contexte l'indique clairement. Le
      fichier est transféré depuis le téléphone puis ajouté à la
      bibliothèque personnelle de l'étudiant au passage (comme tout
      fichier qui transite par le chat) : son lien y est visible ensuite
      via gerer_document_bibliotheque (action "lister" ou "chercher"),
      redonnable directement dans ta réponse sans redemander au
      téléphone. Limite de taille : 50 Mo (plus large que "lire_fichier",
      qui lui doit rester lisible par le modèle -- ici le fichier n'est
      pas traité, juste transféré tel quel).
    - "chercher_par_contenu" : cherche `terme_recherche` dans le CONTENU
      des fichiers sous `dossier_nom` (pas dans leur nom, utilise
      "chercher_par_nom" pour ça). Utilise cette action quand l'étudiant
      décrit ce qu'il cherche sans en connaître le nom exact ("le cours
      où on parle des dérivées"), ou juste après un "chercher_par_nom"
      resté sans résultat si ça semble pertinent. Essaie D'ABORD la
      recherche sémantique instantanée dans ce qui a déjà été vectorisé
      en arrière-plan (04/09/2026) -- fonctionne même app fermée, renvoie
      directement un extrait du contenu ET le lien du fichier. Si rien
      n'y correspond (fichier pas encore vectorisé), bascule
      automatiquement sur une lecture EN DIRECT de chaque fichier
      (nécessite l'app ouverte, peut prendre plus de temps si le dossier
      contient beaucoup de fichiers, c'est normal) : renvoie alors les
      fichiers correspondants avec un court extrait autour de la
      correspondance trouvée, réutilisable ensuite avec "lire_fichier"
      (même convention de chemin) pour lire le fichier en entier si
      besoin.

    `appareil_nom` (ajouté le 04/09/2026) : uniquement nécessaire si
    l'étudiant a désigné un dossier du MÊME nom sur plusieurs
    téléphones -- "lister_dossiers" (outil gerer_dossier_telephone)
    l'indique alors entre crochets à côté du nom concerné. Reprends ce
    libellé exactement, ne le devine jamais. Laisse vide sinon.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    actions_valides = {
        "lister_contenu", "ouvrir_sous_dossier", "chercher_par_nom",
        "lire_fichier", "chercher_par_contenu", "donner_fichier",
    }
    if action not in actions_valides:
        return f"Erreur : action '{action}' inconnue. Actions valides : {', '.join(sorted(actions_valides))}."

    if not dossier_nom:
        return "Erreur : paramètre 'dossier_nom' manquant."

    if action == "ouvrir_sous_dossier" and not chemin:
        return "Erreur : paramètre 'chemin' manquant pour l'action 'ouvrir_sous_dossier'."

    if action in ("chercher_par_nom", "chercher_par_contenu") and not terme_recherche:
        return f"Erreur : paramètre 'terme_recherche' manquant pour l'action '{action}'."

    if action == "lire_fichier" and not chemin:
        return "Erreur : paramètre 'chemin' manquant pour l'action 'lire_fichier'."

    if action == "donner_fichier" and not chemin:
        return "Erreur : paramètre 'chemin' manquant pour l'action 'donner_fichier'."

    # Ajoute le 04/09/2026, Bourama : resout l'appareil PRECIS
    # proprietaire de "dossier_nom" AVANT toute question en direct --
    # meme raisonnement que gerer_dossier_telephone (action "executer"),
    # voir core/dossiers_designes_mobile.resoudre_appareil_cible. Pas
    # necessaire pour la branche "vectorisee" de chercher_par_contenu
    # ci-dessous (recherche dans du contenu deja indexe, aucune question
    # posee a un appareil precis) -- resolu seulement si cette branche
    # ne suffit pas.
    # Correctif 05/09/2026, Bourama : _resoudre_appareil_cible fait un
    # appel Supabase SYNCHRONE (bloquant). Appelée directement dans cette
    # fonction async sans offload, elle gelait tout l'event loop asyncio
    # du process pendant sa durée -- plus aucune tâche async ne pouvait
    # avancer, y compris l'envoi des évènements de statut ("outil en
    # cours") vers le frontend, d'où l'impression que l'outil ne
    # s'exécutait même pas. to_thread.run_sync l'exécute dans un thread
    # séparé, sans bloquer le event loop (même pattern que api/main.py).
    appareil_id_cible, erreur_resolution = await to_thread.run_sync(
        _resoudre_appareil_cible, user_id, dossier_nom, appareil_nom or None
    )
    if erreur_resolution and action != "chercher_par_contenu":
        return f"Erreur : {erreur_resolution}"

    try:
        if action == "lister_contenu":
            resultat = await _lister_contenu_dossier(user_id, appareil_id_cible, dossier_nom)
        elif action == "ouvrir_sous_dossier":
            resultat = await _ouvrir_sous_dossier(user_id, appareil_id_cible, dossier_nom, chemin)
        elif action == "chercher_par_nom":
            resultat = await _chercher_par_nom(user_id, appareil_id_cible, dossier_nom, terme_recherche)
        elif action == "chercher_par_contenu":
            # 04/09/2026, demande Bourama : essaie D'ABORD la recherche
            # vectorisée (core/vectorisation_dossiers_designes.py, fusion
            # de l'ancien outil séparé chercher_dossiers_designes ici,
            # même action plutôt que deux outils qui font presque la même
            # chose) -- instantanée, fonctionne même app fermée, lien
            # déjà inclus dans chaque résultat. Ne tombe sur la lecture
            # EN DIRECT (plus lente, app requise) que si rien n'y
            # correspond, ex. fichier pas encore vectorisé.
            # Meme correctif que _resoudre_appareil_cible plus haut :
            # _chercher_dossiers_designes est SYNCHRONE (appel Supabase +
            # appel Gemini embedding), offload sur un thread pour ne pas
            # geler le event loop.
            resultats_bruts = await to_thread.run_sync(
                _chercher_dossiers_designes, terme_recherche, user_id
            )
            resultats_vectorises = [
                r for r in resultats_bruts
                if r.get("dossier_nom") == dossier_nom
            ]
            if resultats_vectorises:
                blocs = []
                for r in resultats_vectorises:
                    bloc = r["contenu"]
                    source = _formater_source_dossier_designe(r)
                    if source:
                        bloc += f"\n{source}"
                    blocs.append(bloc)
                return "\n\n---\n\n".join(blocs)
            # Rien trouve dans le deja-vectorise : bascule sur la
            # lecture en direct, qui a besoin de l'appareil resolu
            # au-dessus (jamais tente si l'ambiguite n'a pas ete levee).
            if erreur_resolution:
                return f"Erreur : {erreur_resolution}"
            resultat = await _chercher_par_contenu(user_id, appareil_id_cible, dossier_nom, terme_recherche)
        else:
            # "lire_fichier" et "donner_fichier" : même récupération
            # brute depuis le téléphone, traitement différent plus bas
            # (extraction de texte vs upload bibliothèque + pièce jointe).
            resultat = await _lire_fichier(user_id, appareil_id_cible, dossier_nom, chemin)
    except Exception as e:
        logging.error(f"ERREUR explorer_dossier ({action}, {dossier_nom}) : {e}")
        return "Erreur : impossible d'explorer ce dossier, réessaie."

    # Message unifié pour TOUTES les actions ci-dessus quand l'app n'est
    # pas ouverte (validé avec Bourama le 30/08/2026, voir
    # 05-recherche-contenu-app-fermee.md) : toujours la même phrase à
    # relayer à l'étudiant, jamais une erreur technique brute.
    if resultat is None:
        return (
            "L'app Clovis n'est pas ouverte sur le téléphone de l'étudiant "
            "en ce moment : dis-lui exactement ceci : \"Ouvre l'app pour "
            "que je regarde.\""
        )

    if "erreur" in resultat:
        return f"Erreur : {resultat['erreur']}"

    if action == "lire_fichier":
        nom_fichier = resultat.get("nom_fichier") or chemin[-1]
        type_mime = resultat.get("type_mime") or ""
        taille_octets = resultat.get("tailleOctets")

        # Point tranché avec Bourama le 30/08/2026 (voir
        # 04-lecture-contenu.md) : pas de lecture pour un fichier trop
        # volumineux pour l'instant, capacité prévue plus tard.
        if _fichier_trop_volumineux(type_mime, taille_octets):
            return (
                f'Le fichier "{nom_fichier}" est trop volumineux pour que je '
                "puisse le lire pour l'instant. Cette capacité arrivera plus "
                "tard."
            )

        contenu_base64 = resultat.get("contenu_base64")
        if not contenu_base64:
            return f'Erreur : contenu de "{nom_fichier}" introuvable dans la réponse du téléphone.'

        lecture = _lire_contenu_fichier(contenu_base64, type_mime, nom_fichier)
        if "erreur" in lecture:
            return f'Erreur de lecture de "{nom_fichier}" : {lecture["erreur"]}'
        return f'Contenu de "{nom_fichier}" :\n\n{lecture["texte"]}'

    if action == "donner_fichier":
        # 04/09/2026, demande Bourama (Partie B, dossier téléphone) :
        # CE cas-ci reste différent du reste de gerer_document_
        # bibliotheque (actions "donner"/"donner_catalogue_public"
        # retirées le même jour, voir leur docstring -- le lien y est
        # déjà connu dès la recherche, l'IA l'écrit directement dans sa
        # réponse). Ici, le fichier n'est lu qu'EN DIRECT sur le
        # téléphone : aucun lien n'existe encore avant ce transfert, donc
        # cette action reste nécessaire pour aller le chercher et le
        # stocker AVANT de pouvoir donner son lien. Contrairement à
        # "lire_fichier" ci-dessus, aucun traitement/extraction : le
        # fichier est stocké tel quel dans la bibliothèque personnelle
        # (comme n'importe quel fichier qui transite par le chat), avec
        # sa VRAIE url_publique, seule façon de le rendre attachable.
        nom_fichier = resultat.get("nom_fichier") or chemin[-1]
        type_mime = resultat.get("type_mime") or "application/octet-stream"
        taille_octets = resultat.get("tailleOctets")

        if taille_octets is not None and taille_octets > _TAILLE_MAX_OCTETS_BIBLIOTHEQUE:
            return f'Le fichier "{nom_fichier}" dépasse 50 Mo, impossible de le transférer depuis le téléphone.'

        contenu_base64 = resultat.get("contenu_base64")
        if not contenu_base64:
            return f'Erreur : contenu de "{nom_fichier}" introuvable dans la réponse du téléphone.'

        try:
            contenu = base64.b64decode(contenu_base64)
        except Exception as e:
            logging.error(f"ERREUR decodage base64 (donner_fichier, {nom_fichier}) : {e}")
            return f'Erreur : contenu de "{nom_fichier}" illisible (erreur de transfert).'

        try:
            ligne = _enregistrer_fichier(
                contenu=contenu,
                nom_fichier=nom_fichier,
                type_mime=type_mime,
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=f"Depuis le téléphone : {nom_fichier}",
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (donner_fichier, {nom_fichier}) : {e}")
            return f'Erreur : impossible de transférer "{nom_fichier}" depuis le téléphone, réessaie.'

        return f"Fichier envoyé : {nom_fichier} -- {ligne['url_publique']}"

    elements = resultat.get("elements") or []
    if not elements:
        if action == "chercher_par_nom":
            return (
                f'Aucun élément trouvé pour "{terme_recherche}" dans '
                f'"{dossier_nom}" par le nom. Tu peux enchaîner avec '
                '"chercher_par_contenu" si la demande de l\'étudiant porte '
                "sur un contenu plutôt que sur un nom précis."
            )
        if action == "chercher_par_contenu":
            return f'Aucun fichier dont le contenu correspond à "{terme_recherche}" dans "{dossier_nom}".'
        return f'Le dossier "{dossier_nom}" est vide.'

    return _formatter_elements_dossier(elements)
