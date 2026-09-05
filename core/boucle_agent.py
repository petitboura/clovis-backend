# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Le coeur de la boucle agent (_agent_groq) : orchestre le
# streaming de la reponse, le declenchement/traitement des appels
# d'outils, la gestion des confirmations et la detection des appels
# repetes en boucle.
import json
import logging
from mcp_tools import parametres_outils
from constantes_agent import GROQ_PRIMARY, MODELES_AVEC_REASONING_EFFORT, DELAI_MAX_PAR_APPEL
from execution_outils import _AttenteConfirmation, _traiter_appels
from routage_outils import _ecrire_outils_retenus, _separer_appels_garder_outils, NOM_OUTIL_GARDER_OUTILS
from filtre_texte_streaming import _finaliser_fragment_texte, _nouvel_etat_filtre_texte, _traiter_fragment_texte
from profils_agents import _nom_lisible_appel

def _evenement_confirmation(attente, messages_agent, outils_mcp, table_routage, modele=GROQ_PRIMARY, reasoning_effort=None, agent_nom=None):
    appel = attente.appel
    try:
        arguments_dict = json.loads(appel["arguments"] or "{}")
    except Exception:
        arguments_dict = {}
    return {
        "type": "confirmation_requise",
        "nom_outil": appel["name"],
        "nom_lisible": _nom_lisible_appel(appel),
        # Message centré sur l'AGENT (2026-07-23) : "Nucleos va faire X",
        # pas une description technique de l'outil -- valable pour
        # n'importe quelle action sensible, pas seulement GitHub. Le
        # frontend peut afficher ce message directement, ou continuer à
        # composer le sien à partir de nom_lisible s'il préfère.
        "message": f"{agent_nom or 'Cet agent'} veut faire ceci : {_nom_lisible_appel(appel)}.",
        "agent_nom": agent_nom,
        "arguments": arguments_dict,
        "etat_reprise": {
            "messages_agent": messages_agent,
            "outils_mcp": outils_mcp,
            "table_routage": table_routage,
            "appel": appel,
            "appels_restants": attente.appels_restants,
            "modele": modele,
            "reasoning_effort": reasoning_effort,
            "agent_nom": agent_nom,
        },
    }


def _evenement_reprise_agent(type_evenement, messages_agent, outils_mcp, table_routage, modele, reasoning_effort, agent_nom):
    """
    Meme principe que _evenement_confirmation, pour les deux cas ou la
    boucle _agent_groq s'arrete SANS appel en attente : plafond_absolu
    d'etapes atteint ("limite_outils_atteinte", bouton Continuer) ou
    repetition detectee ("repetition_detectee", bouton Reessayer). Contrairement
    a la reprise apres confirmation, la reprise ici rappelle directement
    _agent_groq avec un budget neuf, sans rejouer d'appel -- voir chat().
    """
    return {
        "type": type_evenement,
        "etat_reprise": {
            "messages_agent": messages_agent,
            "outils_mcp": outils_mcp,
            "table_routage": table_routage,
            "modele": modele,
            "reasoning_effort": reasoning_effort,
            "agent_nom": agent_nom,
        },
    }


def _detecter_appel_repete(historique_appels, nouveaux_appels, tolerance):
    """
    Parcourt `nouveaux_appels` (lot du tour courant) a la suite de
    `historique_appels` (liste de tuples (nom, arguments) deja executes ce
    tour, dans l'ordre) et retourne le premier appel qui ferait atteindre
    `tolerance` repetitions IDENTIQUES CONSECUTIVES (meme nom, memes
    arguments), ou None si aucune repetition de ce type n'apparait dans ce
    lot. Ne modifie pas `historique_appels` -- a l'appelant de l'etendre
    une fois la decision prise (executer ou arreter).
    """
    dernier = historique_appels[-1] if historique_appels else None
    compteur = 0
    if dernier is not None:
        for cle in reversed(historique_appels):
            if cle == dernier:
                compteur += 1
            else:
                break
    for appel in nouveaux_appels:
        cle = (appel["name"], appel["arguments"])
        if cle == dernier:
            compteur += 1
        else:
            dernier = cle
            compteur = 1
        if compteur >= tolerance:
            return appel
    return None


def _generer_conclusion_forcee(client_groq, messages_agent, outils_mcp, modele, kwargs_reasoning, timeout):
    """
    Force une reponse texte finale a partir de messages_agent tel quel
    (utilise pour les deux fins de boucle de _agent_groq : plafond_absolu
    atteint et repetition detectee -- un message systeme explicatif est
    ajoute a messages_agent par l'appelant AVANT ce generateur, voir plus
    bas). Factorise ce qui etait duplique dans l'ancien bloc "MAX_ETAPES_
    OUTILS epuise".
    """
    completion = client_groq.chat.completions.create(
        model=modele,
        messages=messages_agent,
        max_completion_tokens=None,
        tools=outils_mcp if outils_mcp else None,
        stream=True,
        timeout=timeout,
        **kwargs_reasoning,
    )
    etat_filtre = _nouvel_etat_filtre_texte()
    for chunk in completion:
        delta = chunk.choices[0].delta
        raisonnement = getattr(delta, "reasoning", None)
        if raisonnement:
            yield {"type": "raisonnement", "texte": raisonnement}
        token = delta.content or ""
        if token:
            for evenement in _traiter_fragment_texte(etat_filtre, token, messages_agent):
                yield evenement
    for evenement in _finaliser_fragment_texte(etat_filtre, messages_agent):
        yield evenement
    if etat_filtre["tool_code_detecte"]:
        logging.error(f"Faux appel d'outil (bloc TOOL_CODE) détecté sur {modele} (conclusion forcée) -- abandon.")
        yield {
            "type": "reponse",
            "texte": "Désolé, je n'ai pas réussi à exécuter l'action demandée. Peux-tu réessayer ou reformuler ta demande ?",
        }


def _agent_groq(client_groq, messages_agent, outils_mcp, table_routage,
                 appels_en_cours_a_finir=None, modele=GROQ_PRIMARY, reasoning_effort=None, agent_nom=None,
                 rattrapage_tool_code_restant=1, conversation_id=None):
    """
    Boucle d'agent generique sur le modele Groq utilise (par defaut
    GROQ_PRIMARY, mais peut recevoir n'importe quel modele Groq qui sait
    faire du tool calling -> permet de reutiliser cette meme boucle pour
    les modeles de secours de GROQ_FALLBACKS, avec les outils MCP branches
    dessus aussi, plutot que de les perdre des que GROQ_PRIMARY sature son
    quota TPM.

    `reasoning_effort`, si fourni (ex: "none"), est transmis tel quel a
    l'appel Groq : certains modeles de secours (ex: qwen3) font du
    raisonnement par defaut, ce qui peut etre desactive pour rester rapide.

    Genere des evenements "statut"/"reponse"/"confirmation_requise".
    S'arrete (sans exception) des qu'une reponse finale a ete produite OU
    qu'une confirmation est necessaire.

    `appels_en_cours_a_finir`, si fourni, est traite AVANT le prochain
    appel a Groq : c'est le cas lors d'une reprise apres confirmation, ou
    il faut d'abord finir le lot d'outils du tour precedent (executer les
    appels restants, ou re-demander confirmation si l'un d'eux est aussi
    sensible) avant de redemander une reponse au modele.

    `rattrapage_tool_code_restant` (29/07, demande Bourama) : quand le
    modele ecrit un faux appel d'outil (bloc ```TOOL_CODE, voir
    _trouver_debut_tool_code) au lieu d'utiliser le vrai mecanisme de tool
    calling, l'ancien comportement se contentait de masquer le texte a
    l'utilisateur SANS jamais executer l'action demandee -- meme quand la
    detection fonctionnait, le vrai probleme (rien n'est execute) restait
    entier. Desormais, une detection de ce cas declenche UNE tentative de
    rattrapage automatique : un message correctif est injecte et le modele
    est relance avec ce budget decremente a 0, pour eviter toute boucle. Si
    le meme bug se reproduit malgre le rattrapage, un message d'erreur clair
    est affiche a l'utilisateur plutot que de le laisser sans reponse.

    `conversation_id` (2026-09-04) : uniquement utilise pour persister les
    appels a l'outil interne garder_outils (voir
    _separer_appels_garder_outils plus bas) -- None accepte, dans ce cas
    garder_outils reste inoffensif (rien n'est ecrit, pas d'erreur).
    """
    kwargs_reasoning = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    # Compteur de sources partagé sur tout le tour (26/08, citations
    # inline bibliotheque) -- une seule boîte, passée aux deux appels de
    # _traiter_appels ci-dessous, pour que la numérotation ne reparte
    # jamais à 1 entre "finir les appels en attente d'une confirmation"
    # et "le prochain lot d'appels du modèle". Voir _traiter_appels.
    compteur_sources = [0]
    # reasoning_format="parsed" separe le raisonnement (delta.reasoning) du
    # texte de reponse final (delta.content). IMPORTANT : la doc Groq
    # (console.groq.com/docs/reasoning) precise que ce parametre n'est PAS
    # supporte par gpt-oss-20b/120b (eux exposent deja le raisonnement par
    # defaut dans le champ "reasoning") -- on ne l'envoie donc qu'aux
    # modeles qui le supportent reellement (qwen3), pour eviter un
    # comportement indefini cote API sur GROQ_PRIMARY (gpt-oss-120b).
    if modele in MODELES_AVEC_REASONING_EFFORT and "gpt-oss" not in modele and reasoning_effort != "none":
        kwargs_reasoning["reasoning_format"] = "parsed"

    if appels_en_cours_a_finir:
        try:
            for event in _traiter_appels(appels_en_cours_a_finir, messages_agent, table_routage, compteur_sources):
                yield event
        except _AttenteConfirmation as attente:
            yield _evenement_confirmation(attente, messages_agent, outils_mcp, table_routage, modele, reasoning_effort, agent_nom)
            return

    # Budget dynamique d'aller-retours "outil" (02/09/2026, remplace
    # MAX_ETAPES_OUTILS fixe -- voir sa note plus haut) : demarre a
    # budget_depart, s'etend de palier_extension a chaque epuisement TANT
    # QU'aucune repetition n'est detectee (progression jugee reelle),
    # jusqu'a plafond_absolu qui reste la vraie limite infranchissable.
    _params_outils = parametres_outils()
    budget_courant = _params_outils["budget_depart"]
    palier_extension = _params_outils["palier_extension"]
    plafond_absolu = _params_outils["plafond_absolu"]
    tolerance_repetition = _params_outils["tolerance_repetition"]
    # (nom, arguments) de chaque appel deja execute ce tour, dans l'ordre
    # -- sert uniquement a _detecter_appel_repete, jamais vide entre deux
    # tours (nouvelle liste a chaque appel de _agent_groq).
    historique_appels_tour = []
    limite_atteinte = False
    etape = 0
    while True:
        if etape >= budget_courant:
            if budget_courant >= plafond_absolu:
                limite_atteinte = True
                break
            budget_courant = min(budget_courant + palier_extension, plafond_absolu)
        etape += 1
        # Forçage tool_choice="required" RETIRÉ (2026-09-05, décision
        # explicite de Bourama, chantier "timeline chronologique") : il
        # avait été introduit le 2026-07-28 pour garantir qu'un outil
        # sélectionné (bouton Outils ou suggestion du routeur) soit
        # réellement appelé, mais il empêchait aussi toute prose du modèle
        # avant son tout premier appel d'outil du tour (reserve_tokens
        # minimale ci-dessous, aucune place pour du texte). Bourama veut
        # désormais que le modèle puisse réfléchir/commenter à voix haute
        # avant d'appeler un outil. La garantie d'appel réel n'est plus
        # portée par l'API mais par le prompt (voir <outils_actifs> dans
        # _construire_system_prompt, reformulé le 05/09 en conséquence,
        # strict sur l'obligation d'appeler l'outil dès qu'il est
        # pertinent) -- à surveiller si le bug du 28/07 (outil sélectionné
        # jamais appelé) reapparaissait malgré ce prompt renforcé.
        kwargs_tool_choice = {}

        # Reserve de tokens de sortie (2026-07-30, correction demandee par
        # Bourama) : jusqu'ici 8192 fixe pour TOUS les appels, principal
        # comme fallbacks. Or Groq compare cette reserve demandee (pas
        # l'usage reel) a la limite TPM du modele AVANT meme de generer
        # quoi que ce soit -- plusieurs modeles de la cascade plafonnent
        # autour de 8000 TPM cote gratuit, donc demander 8192 fait echouer
        # l'appel des le depart, meme sur un premier message tout simple
        # (ex: "genere-moi une image"), constate par Bourama le 30/07.
        # Le cas "outil force, reserve minimale" (2026-07-28) a disparu en
        # meme temps que tool_choice="required" ci-dessus (05/09) : plus
        # d'appel purement structure sans prose, donc plus de raison de
        # reduire la reserve pour ce cas.
        if modele == GROQ_PRIMARY:
            reserve_tokens = 8192
        else:
            reserve_tokens = 4096

        completion = client_groq.chat.completions.create(
            model=modele,
            messages=messages_agent,
            max_completion_tokens=reserve_tokens,
            tools=outils_mcp if outils_mcp else None,
            stream=True,
            timeout=DELAI_MAX_PAR_APPEL,
            **kwargs_reasoning,
            **kwargs_tool_choice,
        )

        reponse_directe = False
        appels_en_cours = {}  # index -> {"id", "name", "arguments"}
        # Filet de securite contre les bugs Groq connus (JSON casse, recopie
        # brute d'un resultat d'outil, faux bloc ```TOOL_CODE) : voir
        # _traiter_fragment_texte / _finaliser_fragment_texte plus haut.
        #
        # IMPORTANT (bug trouve le 26/07/2026, signale par Bourama) : cette
        # verification portait AVANT seulement sur les 60 tout premiers
        # caracteres du flux, une seule fois -- une fois la reponse jugee
        # "normale" au debut, plus RIEN ne revenait verifier le reste du
        # flux. Desormais on re-verifie en continu pendant TOUTE la duree
        # du flux, pas juste au debut.
        #
        # CORRECTION (29/07, signalee par Bourama) : pour le cas specifique
        # du faux bloc TOOL_CODE, on ne masque plus que le bloc lui-meme
        # (bornes precises, voir _traiter_fragment_texte) -- le texte avant
        # ET apres le bloc reste visible, au lieu de tout basculer en
        # "raisonnement" des la detection et jusqu'a la fin du passage.
        etat_filtre = _nouvel_etat_filtre_texte()
        dernier_finish_reason = None
        dernier_usage = None

        for chunk in completion:
            # Diagnostic du bug de troncature (27/07) : le dernier chunk
            # du flux porte finish_reason ("stop" = fin normale, "length" =
            # coupé faute de budget de tokens) et parfois x_groq.usage
            # (tokens consommés) -- on les garde pour les logger une fois
            # le flux terminé, plutôt que de deviner la cause à l'aveugle.
            if chunk.choices and chunk.choices[0].finish_reason:
                dernier_finish_reason = chunk.choices[0].finish_reason
            if getattr(chunk, "x_groq", None) and getattr(chunk.x_groq, "usage", None):
                dernier_usage = chunk.x_groq.usage

            delta = chunk.choices[0].delta

            raisonnement = getattr(delta, "reasoning", None)
            if raisonnement:
                yield {"type": "raisonnement", "texte": raisonnement}

            if delta.content:
                reponse_directe = True
                for evenement in _traiter_fragment_texte(etat_filtre, delta.content, messages_agent):
                    yield evenement

            if delta.tool_calls:
                for fragment in delta.tool_calls:
                    etat = appels_en_cours.setdefault(
                        fragment.index, {"id": None, "name": "", "arguments": ""}
                    )
                    if fragment.id:
                        etat["id"] = fragment.id
                    if fragment.function:
                        if fragment.function.name:
                            etat["name"] += fragment.function.name
                        if fragment.function.arguments:
                            etat["arguments"] += fragment.function.arguments

        if dernier_finish_reason == "length":
            logging.error(
                f"TRONCATURE (finish_reason=length) sur {modele} -- usage : {dernier_usage}"
            )
        elif dernier_finish_reason and dernier_finish_reason != "stop":
            logging.info(f"Fin de flux {modele} avec finish_reason={dernier_finish_reason} (usage : {dernier_usage})")

        for evenement in _finaliser_fragment_texte(etat_filtre, messages_agent):
            yield evenement

        if etat_filtre["tool_code_detecte"] and not appels_en_cours:
            # Faux appel d'outil détecté (bloc ```TOOL_CODE) et aucun vrai
            # tool_calls reçu ce tour-ci : rien n'a été réellement exécuté.
            # Voir la docstring de _agent_groq pour le mécanisme de
            # rattrapage.
            if rattrapage_tool_code_restant > 0 and outils_mcp:
                logging.warning(
                    f"Faux appel d'outil (bloc TOOL_CODE) détecté sur {modele} -- rattrapage automatique déclenché."
                )
                messages_agent.append({
                    "role": "system",
                    "content": (
                        "Tu viens d'écrire un faux appel d'outil sous forme de bloc de "
                        "code (```TOOL_CODE...```) au lieu d'utiliser le vrai mécanisme "
                        "d'appel d'outil de l'API. Cela n'exécute rien du tout. Si tu "
                        "veux exécuter un outil, utilise IMPÉRATIVEMENT le vrai "
                        "mécanisme d'appel d'outil (tool_calls), jamais de bloc de "
                        "code. N'écris plus jamais de bloc ```TOOL_CODE```."
                    ),
                })
                yield from _agent_groq(
                    client_groq, messages_agent, outils_mcp, table_routage,
                    modele=modele, reasoning_effort=reasoning_effort, agent_nom=agent_nom,
                    rattrapage_tool_code_restant=rattrapage_tool_code_restant - 1,
                    conversation_id=conversation_id,
                )
                return
            else:
                logging.error(
                    f"Faux appel d'outil (bloc TOOL_CODE) détecté à nouveau sur {modele} après rattrapage (ou sans outil dispo) -- abandon."
                )
                yield {
                    "type": "reponse",
                    "texte": "Désolé, je n'ai pas réussi à exécuter l'action demandée. Peux-tu réessayer ou reformuler ta demande ?",
                }
                return

        if reponse_directe and not appels_en_cours:
            # Cas normal : reponse texte pure, aucun outil appele -- on
            # peut s'arreter la.
            logging.info(f"Réponse via GROQ (sans outil, streaming): {modele}")
            return

        if not appels_en_cours:
            return  # ni contenu ni outil (rare) : rien a faire de plus

        # BUG CORRIGE (2026-07-26, trouve par Bourama) : avant ce fix, la
        # simple presence de texte (reponse_directe=True) faisait sortir
        # la fonction ICI, AVANT d'atteindre le code qui execute
        # appels_en_cours plus bas -- un appel d'outil recu dans le meme
        # passage qu'un peu de texte (ex: un modele qui dit "D'accord, je
        # m'en occupe..." en meme temps qu'il appelle l'outil) etait donc
        # silencieusement perdu : jamais execute, aucune erreur visible,
        # l'IA repondait juste comme si de rien n'etait. Desormais, si
        # appels_en_cours n'est pas vide, on continue vers le traitement
        # des appels ci-dessous, meme si du texte a deja ete stream et
        # affiche.

        appels = [appels_en_cours[i] for i in sorted(appels_en_cours)]

        # Outil interne garder_outils (2026-09-04, demande Bourama) :
        # sorti du lot AVANT tout le reste -- jamais compte dans le
        # budget/la detection de repetition, jamais route vers un vrai
        # serveur MCP. Ecrit immediatement en base (ecrase le tour
        # precedent) des qu'il apparait dans ce lot ; les appels normaux
        # restants repartent seuls dans le circuit habituel juste apres.
        appels_normaux, noms_a_garder_ce_lot = _separer_appels_garder_outils(appels)
        if len(appels_normaux) != len(appels):
            _ecrire_outils_retenus(conversation_id, noms_a_garder_ce_lot)

        # Detection de repetition (02/09/2026, demande Bourama) : si l'un
        # de ces appels ferait atteindre tolerance_repetition fois
        # d'affilee EXACTEMENT le meme appel (nom + arguments), on
        # s'arrete AVANT de l'executer -- signe d'une vraie boucle, pas
        # d'un usage legitime. On ne l'ajoute pas a messages_agent
        # (aucun tool_calls sans tool_result correspondant). Porte
        # uniquement sur appels_normaux (garder_outils exclu).
        appel_repete = _detecter_appel_repete(historique_appels_tour, appels_normaux, tolerance_repetition)
        historique_appels_tour.extend((a["name"], a["arguments"]) for a in appels_normaux)
        if appel_repete:
            logging.warning(
                f"Répétition détectée sur {modele} -- même appel ({appel_repete['name']}) "
                f"refait {tolerance_repetition}x d'affilée, arrêt avant exécution."
            )
            messages_agent.append({
                "role": "system",
                "content": (
                    f"Tu as répété {tolerance_repetition} fois d'affilée exactement la "
                    f"même action ({_nom_lisible_appel(appel_repete)}) sans y arriver. "
                    "Rédige maintenant ta réponse en expliquant clairement ce blocage à "
                    "l'utilisateur, avec ce que tu as quand même réussi à faire jusque-là. "
                    "Ne retente pas cette même action telle quelle."
                ),
            })
            for event in _generer_conclusion_forcee(client_groq, messages_agent, outils_mcp, modele, kwargs_reasoning, DELAI_MAX_PAR_APPEL):
                yield event
            yield _evenement_reprise_agent("repetition_detectee", messages_agent, outils_mcp, table_routage, modele, reasoning_effort, agent_nom)
            return

        messages_agent.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": appel["id"],
                    "type": "function",
                    "function": {"name": appel["name"], "arguments": appel["arguments"]},
                }
                for appel in appels
            ],
        })

        # Reponses synthetiques pour les appels garder_outils de ce lot --
        # jamais via _traiter_appels (aucun evenement outil_resultat,
        # totalement invisible pour l'utilisateur, voir NOM_OUTIL_GARDER_OUTILS).
        for appel in appels:
            if appel["name"] == NOM_OUTIL_GARDER_OUTILS:
                messages_agent.append({
                    "role": "tool",
                    "tool_call_id": appel["id"],
                    "content": (
                        f"Outils gardés pour ton prochain message : {', '.join(noms_a_garder_ce_lot)}."
                        if noms_a_garder_ce_lot else
                        "Aucun outil gardé pour le prochain message."
                    ),
                })

        if appels_normaux:
            try:
                for event in _traiter_appels(appels_normaux, messages_agent, table_routage, compteur_sources):
                    yield event
            except _AttenteConfirmation as attente:
                yield _evenement_confirmation(attente, messages_agent, outils_mcp, table_routage, modele, reasoning_effort, agent_nom)
                return

        # (2026-07-30, demande Bourama : round-trip standard apres
        # execution des outils, SANS EXCEPTION -- y compris pour les
        # outils de generation/action (ancien OUTILS_AUTONOMES, retire le
        # 15/08, voir registre_outils.py). On ne s'arrete plus ici : la
        # boucle `for` continue naturellement vers un nouvel appel Groq
        # avec les resultats d'outils ajoutes a messages_agent, pour que
        # le modele formule sa reponse finale a partir d'eux -- le
        # fonctionnement standard du tool calling.
        #
        # La reponse du modele n'est PLUS filtree/masquee cote backend
        # (choix explicite de Bourama, 2026-07-30) : si le modele
        # recopie/casse un lien, tant pis, sa reponse s'affiche telle
        # quelle. Le vrai resultat fiable de l'outil (URL correcte
        # comprise) reste disponible independamment, via les evenements
        # outil_resultat/fichiers_generes deja emis par _traiter_appels
        # -- voir OutilResultatBulle.tsx/FichierChip.tsx cote frontend
        # pour leur affichage en menu repliable en bas de la reponse.


    # plafond_absolu atteint sans conclusion (02/09/2026, remplace
    # l'ancien forcage silencieux) : le modele redige quand meme une
    # vraie reponse a partir de ce qu'il a deja obtenu, en expliquant
    # lui-meme qu'il a atteint sa limite -- puis un evenement
    # limite_outils_atteinte porte l'etat complet pour le bouton
    # "Continuer" cote frontend (voir _evenement_reprise_agent).
    messages_agent.append({
        "role": "system",
        "content": (
            f"Tu as atteint la limite de {plafond_absolu} actions pour cette "
            "réponse. Rédige maintenant ta réponse finale à partir de ce que tu "
            "as déjà obtenu, en indiquant clairement à l'utilisateur que tu as "
            "atteint cette limite et qu'il peut te demander de continuer pour "
            "poursuivre le travail."
        ),
    })
    for event in _generer_conclusion_forcee(client_groq, messages_agent, outils_mcp, modele, kwargs_reasoning, DELAI_MAX_PAR_APPEL):
        yield event
    yield _evenement_reprise_agent("limite_outils_atteinte", messages_agent, outils_mcp, table_routage, modele, reasoning_effort, agent_nom)
    logging.info(f"Réponse via GROQ (avec outil, plafond_absolu={plafond_absolu} atteint): {modele}")


def _capturer_reponse(generateur, accumulateur, meta=None):
    """
    Relaie tous les evenements d'un generateur tel quel, en accumulant au
    passage le texte des evenements "reponse" dans `accumulateur` (une
    liste, mutee en place). Permet de reconstruire la reponse finale
    complete une fois le generateur epuise, pour la persister en memoire,
    sans dupliquer cette logique a chaque point de sortie de chat().

    `meta` (optionnel, dict mute en place) : si fourni, capture aussi les
    outils executes (nom_outil/nom_lisible/resultat) et leurs sources,
    pour persistance dans historique_conversations.meta -- ces evenements
    sont sinon uniquement diffuses en direct (SSE) et perdus a la
    reouverture d'une conversation. Structure identique a ce qu'attend
    MessageAffiche.outilsResultats cote frontend (voir BulleMessage.tsx) :
    une entree "sources" est toujours rattachee au DERNIER outil ajoute,
    jamais a un tableau global -- le backend emet toujours outil_resultat
    puis sources pour un meme appel, sans rien entre les deux (voir
    _traiter_appels).
    """
    for event in generateur:
        if event["type"] == "reponse":
            accumulateur.append(event["texte"])
        elif meta is not None and event["type"] == "outil_resultat":
            meta.setdefault("outils", []).append({
                "nomOutil": event["nom_outil"],
                "nomLisible": event["nom_lisible"],
                "resultat": event["resultat"],
            })
        elif meta is not None and event["type"] == "sources" and meta.get("outils"):
            meta["outils"][-1]["sources"] = event["sources"]
        yield event


