# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Decide quels outils MCP sont pertinents pour une question
# (routeur LLM rapide) et gere la memoire des outils "gardes" ouverts sur
# une conversation.
import json
import logging
from groq import Groq
from constantes_agent import get_secret, supabase, MODELE_ROUTEUR_OUTILS, MODELE_ROUTEUR_OUTILS_REPLI, DELAI_MAX_PAR_APPEL
from mcp_tools import lister_outils_autorises_pour_agent

def _resume_description_outil(description, max_caracteres=200):
    """
    Version courte d'une description d'outil, pour le catalogue envoyé au
    routeur (_router_outils) uniquement -- jamais pour l'exécution réelle
    (lister_tous_les_outils envoie toujours la description complète au
    modèle principal, qui lui en a besoin pour bien remplir les
    paramètres). Ajouté le 15/08 (Bourama : le routeur -- un petit modèle
    8B -- dépassait systématiquement son budget de tokens avec 50+ outils
    à décrire en entier, donc échouait à CHAQUE message -> aucune
    suggestion automatique possible, quelle que soit la question).

    Coupe à la première phrase complète (le "à quoi ça sert" est
    quasiment toujours dedans, le reste des docstrings détaille des cas
    limites/formats dont un simple tri n'a pas besoin) -- garde donc le
    sens plutôt que de tronquer au milieu d'un mot. Si la première phrase
    dépasse quand même max_caracteres, coupe au dernier espace avant la
    limite plutôt qu'en plein milieu d'un mot.
    """
    description = description.strip()
    fin_phrase = description.find(". ")
    if 0 < fin_phrase <= max_caracteres:
        return description[: fin_phrase + 1]
    if len(description) <= max_caracteres:
        return description
    coupe = description[:max_caracteres].rsplit(" ", 1)[0]
    return coupe + "..."


NOM_OUTIL_GARDER_OUTILS = "garder_outils"


def _lire_outils_retenus(conversation_id):
    """
    Outils que le grand modele avait explicitement decide de garder au
    tour precedent pour cette conversation (voir _outil_garder_outils).
    Liste vide si rien n'est retenu, si conversation_id est absent, ou en
    cas d'erreur Supabase -- fail-safe strict, ne doit jamais bloquer la
    reponse normale (meme logique que _router_outils).
    """
    if not conversation_id:
        return []
    try:
        res = (
            supabase.table("outils_retenus_conversation")
            .select("outils")
            .eq("conversation_id", conversation_id)
            .maybe_single()
            .execute()
        )
        # maybe_single().execute() peut renvoyer None directement (pas un
        # objet avec .data) quand 0 ligne correspond, selon la version de
        # postgrest-py -- d'ou le crash 'NoneType' object has no attribute
        # 'data' observe en logs. On tolere les deux comportements.
        return ((res.data if res else None) or {}).get("outils") or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture outils_retenus_conversation) : {e}")
        return []


def _ecrire_outils_retenus(conversation_id, outils):
    """
    Ecrase (jamais cumule) la liste d'outils gardes pour cette
    conversation. Appele a la fin de chaque tour ou garder_outils a ete
    utilise (voir _agent_groq) -- y compris avec une liste vide si le
    modele ne l'a pas rappele ce tour-ci, pour que l'effet retombe
    naturellement au tour d'apres. Toute erreur est juste loguee.
    """
    if not conversation_id:
        return
    try:
        supabase.table("outils_retenus_conversation").upsert({
            "conversation_id": conversation_id,
            "outils": list(dict.fromkeys(outils or [])),
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (ecriture outils_retenus_conversation) : {e}")


def _outil_garder_outils(noms_disponibles):
    """
    Outil interne (2026-09-04, demande Bourama) : jamais un vrai outil
    MCP, jamais route via table_routage, jamais compte dans le budget
    d'aller-retours ni dans la detection de repetition (voir
    _separer_appels_garder_outils dans _agent_groq). Permet au grand
    modele de garder un ou plusieurs des outils qui lui sont proposes ce
    tour-ci disponibles pour SON PROCHAIN message dans cette conversation,
    sans attendre une nouvelle suggestion du petit routeur automatique
    (_router_outils) ni un clic de confirmation cote frontend.

    N'a d'effet que sur le tour suivant : si le modele veut le garder
    encore apres, il doit rappeler cet outil a ce moment-la (voir
    _ecrire_outils_retenus, ecrase a chaque tour).
    """
    return {
        "type": "function",
        "function": {
            "name": NOM_OUTIL_GARDER_OUTILS,
            "description": (
                "Garde un ou plusieurs des outils qui te sont proposes "
                "dans CE message disponibles pour TON PROCHAIN message "
                "dans cette conversation, sans attendre une nouvelle "
                "suggestion automatique. A utiliser quand tu juges qu'un "
                "outil que tu viens de voir (que tu l'aies utilise ou "
                "non ce tour-ci) sera probablement encore utile pour la "
                "question suivante de l'utilisateur. Sans effet sur ta "
                "reponse actuelle. Si tu veux le garder encore apres le "
                "prochain message, rappelle cet outil a nouveau a ce "
                "moment-la -- rien n'est conserve automatiquement plus "
                "d'un message a l'avance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "outils": {
                        "type": "array",
                        "items": {"type": "string", "enum": noms_disponibles},
                        "description": "Noms exacts (parmi les outils disponibles ce tour-ci) a garder pour le prochain message.",
                    }
                },
                "required": ["outils"],
            },
        },
    }


def _separer_appels_garder_outils(appels):
    """
    Sort les appels a l'outil interne garder_outils (voir
    _outil_garder_outils) du reste des appels normaux -- jamais envoyes a
    table_routage/_traiter_appels, jamais comptes dans le budget ni la
    detection de repetition. Renvoie (appels_normaux,
    noms_outils_a_garder), noms_outils_a_garder etant l'union (dans
    l'ordre) des arguments "outils" de tous les appels garder_outils de
    ce lot -- rare qu'il y en ait plus d'un dans le meme lot, mais couvert.
    """
    appels_normaux = []
    noms_a_garder = []
    for appel in appels:
        if appel["name"] == NOM_OUTIL_GARDER_OUTILS:
            try:
                arguments = json.loads(appel["arguments"] or "{}")
                noms_a_garder.extend(arguments.get("outils") or [])
            except Exception as e:
                logging.error(f"ERREUR arguments garder_outils illisibles : {e}")
        else:
            appels_normaux.append(appel)
    return appels_normaux, list(dict.fromkeys(noms_a_garder))


NOM_OUTIL_DEMANDER_OUTILS = "demander_outils"


def _outil_demander_outils():
    """
    Outil interne (chantier "demander_outils", 05/09/2026, demande
    Bourama) : comme _outil_garder_outils juste au-dessus, jamais un vrai
    outil MCP, jamais route via table_routage, jamais compte dans le
    budget d'aller-retours ni dans la detection de repetition (voir
    _separer_appels_demander_outils juste en dessous). Permet au grand
    modele de demander, EN PLEIN MILIEU de sa reponse en cours, un outil
    qui existe reellement dans le catalogue de Clovis mais qui ne fait
    pas partie de ce qui lui a ete propose ce tour-ci (ni outils forces
    de contexte, ni gardes du tour precedent, ni suggeres par le routeur
    automatique -- voir _outils_deja_en_main juste en dessous).

    Contrairement a _outil_garder_outils, PAS de liste fermee en enum :
    le modele ne connait pas les noms exacts des outils qu'il n'a pas, il
    decrit son besoin en langage libre. C'est l'etape 3 (branchement,
    dans _agent_groq) qui compare ensuite ce texte par recherche
    mots-cles (voir recherche_outils.rechercher_outils_pertinents) au
    reste du catalogue complet deja recupere ailleurs (voir
    mcp_tools.lister_outils_autorises_pour_agent) -- cette fonction-ci ne
    fait que decrire l'outil, aucune recherche.
    """
    return {
        "type": "function",
        "function": {
            "name": NOM_OUTIL_DEMANDER_OUTILS,
            "description": (
                "Demande un outil dont tu as besoin MAINTENANT pour "
                "continuer ta reponse en cours, mais qui ne fait pas "
                "partie des outils qui te sont proposes ce tour-ci. "
                "Decris en une phrase claire ce que tu cherches a faire "
                "(jamais un nom d'outil que tu devinerais). Si un outil "
                "correspondant existe dans le catalogue de Clovis, il "
                "t'est ajoute immediatement et tu peux l'appeler dans la "
                "foulee, sans attendre le prochain message de "
                "l'utilisateur. Si rien ne correspond, on te le dit "
                "clairement -- adapte-toi alors plutot que de rester "
                "bloque en silence. A utiliser seulement quand tu es "
                "reellement bloque par l'absence d'un outil, pas "
                "systematiquement au debut de chaque tache. "
                "PRIORITE (12/09/2026, demande Bourama) : decris d'abord "
                "l'outil precis dont tu as besoin pour la tache demandee "
                "(ex: envoyer un message, gerer un document, un skill, "
                "chercher sur le web). N'utilise gerer_base_connaissance "
                "qu'en dernier recours, apres au maximum 2 recherches "
                "d'outils precis infructueuses -- sauf si la question "
                "porte clairement sur Clovis ou l'application elle-meme "
                "(fonctionnement, bug, fonctionnalite), auquel cas "
                "demande directement gerer_base_connaissance des la "
                "premiere recherche."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "besoin": {
                        "type": "string",
                        "description": (
                            "Description libre, en une phrase, de ce que "
                            "tu cherches a faire (ex : \"un outil pour "
                            "envoyer un email\"), pas un nom d'outil."
                        ),
                    }
                },
                "required": ["besoin"],
            },
        },
    }


def _separer_appels_demander_outils(appels):
    """
    Sort les appels a l'outil interne demander_outils (voir
    _outil_demander_outils) du reste des appels normaux -- meme principe
    que _separer_appels_garder_outils : jamais envoyes a
    table_routage/_traiter_appels, jamais comptes dans le budget ni la
    detection de repetition.

    REVISION (etape 3, meme jour) : contrairement a la premiere version
    (poussee avec l'etape 2), ne renvoie plus une simple liste de textes
    "besoin" -- chaque appel demander_outils du lot peut chercher quelque
    chose de DIFFERENT et doit recevoir sa propre reponse individuelle
    (voir _agent_groq), pas une reponse generique partagee comme
    garder_outils. Renvoie donc (appels_normaux, demandes), demandes
    etant la liste (dans l'ordre, un element par appel demander_outils du
    lot -- rare qu'il y en ait plus d'un, mais couvert) de dicts
    {"id": tool_call_id, "besoin": texte_ou_chaine_vide}. Un besoin
    vide/illisible reste dans la liste (jamais ignore silencieusement
    cette fois) : l'API Groq exige un message "tool" pour CHAQUE
    tool_call_id emis par l'assistant, sinon le prochain appel a Groq
    echoue -- charge donc a l'appelant de repondre "je n'ai pas compris"
    plutot que rien.
    """
    appels_normaux = []
    demandes = []
    for appel in appels:
        if appel["name"] == NOM_OUTIL_DEMANDER_OUTILS:
            try:
                arguments = json.loads(appel["arguments"] or "{}")
                besoin = (arguments.get("besoin") or "").strip()
            except Exception as e:
                logging.error(f"ERREUR arguments demander_outils illisibles : {e}")
                besoin = ""
            demandes.append({"id": appel["id"], "besoin": besoin})
        else:
            appels_normaux.append(appel)
    return appels_normaux, demandes


def _rafraichir_enum_garder_outils(outils_mcp):
    """
    Etape 4 du chantier "demander_outils" (06/09/2026, demande Bourama) :
    verifie que garder_outils reconnaisse bien les outils ajoutes en
    direct par demander_outils.

    Probleme sans cette fonction : l'enum de garder_outils (voir
    _outil_garder_outils, parametres.outils.items.enum) est fige au
    moment ou main.py construit outils_mcp UNE SEULE FOIS, AVANT que le
    tour ne commence -- si demander_outils agrandit outils_mcp en cours
    de route (voir le branchement dans _agent_groq), l'entree
    garder_outils deja presente dans cette meme liste continue de
    pointer vers l'ANCIEN enum, sans les noms tout juste ajoutes. Le
    modele ne pourrait alors pas les "garder" pour son prochain message
    meme s'il vient de les utiliser avec succes ce tour-ci.

    Reconstruit donc l'entree garder_outils de outils_mcp avec un enum a
    jour (tous les noms reels actuellement dans outils_mcp, garder_outils
    et demander_outils eux-memes exclus -- ce sont des outils internes
    remis en place a chaque tour qualifiant par main.py, jamais quelque
    chose que le modele devrait "garder" via ce mecanisme).

    A appeler juste apres tout ajout reussi a outils_mcp par
    demander_outils, AVANT le prochain appel a Groq (voir _agent_groq).
    Si garder_outils n'est pas present dans outils_mcp (ne devrait pas
    arriver ici, puisque demander_outils lui-meme n'est ajoute par
    _preparer_demander_outils que si outils_mcp etait deja non vide au
    debut du tour -- mais reste fail-safe : renvoie outils_mcp inchange).
    """
    if not any(o["function"]["name"] == NOM_OUTIL_GARDER_OUTILS for o in outils_mcp):
        return outils_mcp
    noms_reels = [
        o["function"]["name"] for o in outils_mcp
        if o["function"]["name"] not in (NOM_OUTIL_GARDER_OUTILS, NOM_OUTIL_DEMANDER_OUTILS)
    ]
    return [
        _outil_garder_outils(noms_reels) if o["function"]["name"] == NOM_OUTIL_GARDER_OUTILS else o
        for o in outils_mcp
    ]


def _outils_deja_en_main(outils_mcp):
    """
    Ensemble des noms d'outils que le modele a deja disponibles ce
    tour-ci -- outils forces de contexte, gardes du tour precedent et
    suggeres par le routeur sont deja tous fusionnes en amont dans
    outils_mcp au moment ou main.py le construit (voir _fusionner_outils
    dans main.py), donc un simple passage sur outils_mcp suffit ici, pas
    besoin de recalculer la fusion. Si un appel demander_outils precedent
    dans ce meme tour a deja ajoute des outils a outils_mcp, ils sont
    aussi dans cet ensemble -- pas de risque de les redemander deux fois.

    Ne fait aucune recherche elle-meme (voir etape 3) : sert seulement a
    savoir quoi exclure du catalogue complet avant de chercher dedans.
    """
    return {o["function"]["name"] for o in (outils_mcp or [])}


def _preparer_demander_outils(outils_mcp, catalogue_complet, table_routage_complet):
    """
    A appeler UNE SEULE FOIS dans main.py, au meme point de convergence
    que _outil_garder_outils (une fois outils_mcp definitivement etabli
    pour ce tour, apres garder_outils lui-meme). Ajoute demander_outils a
    outils_mcp SYSTEMATIQUEMENT, meme si outils_mcp est vide au depart
    (decision explicite de Bourama, 10/09/2026 : demander_outils doit
    toujours etre propose, quel que soit l'etat du tour -- ne suit plus
    la regle de garder_outils).

    Modifie le 11/09/2026 (demande Bourama, chasse aux doublons de
    travail par message) : catalogue_complet/table_routage_complet sont
    maintenant fournis par l'appelant, deja recuperes une seule fois plus
    haut dans main.py (via lister_outils_autorises_pour_agent, cache 24h
    par serveur) pour construire outils_mcp -- au lieu d'etre redemandes
    ici par un second appel a la meme fonction. Le catalogue lui-meme ne
    change pas (toujours celui deja en cache), seul le nombre de fois ou
    on va le chercher passe de deux a une fois par message. Pour un
    utilisateur connecte a un service necessitant son compte (Notion,
    Google Drive), evite aussi de revalider son acces deux fois de suite.

    Renvoie outils_mcp, avec demander_outils toujours ajoute.
    """
    return (outils_mcp or []) + [_outil_demander_outils()]


def _catalogue_pour_demander_outils(user_id, agent_id, outils_mcp, conversation_id=None):
    """
    Variante de _preparer_demander_outils pour les deux chemins de
    reprise de main.py (apres confirmation, ou apres limite/repetition) :
    outils_mcp y est restaure tel quel depuis l'etat de reprise -- il a
    deja ete etabli (et demander_outils deja ajoute si applicable) sur le
    tour precedent, donc RIEN a rajouter ici, seulement rafraichir
    catalogue_complet/table_routage_complet (transitoires, jamais
    persistes dans l'etat de reprise) si demander_outils fait bien partie
    de ce qui est deja propose -- sinon (None, None), rien recupere pour
    rien.
    """
    if not outils_mcp or not any(o["function"]["name"] == NOM_OUTIL_DEMANDER_OUTILS for o in outils_mcp):
        return None, None
    return lister_outils_autorises_pour_agent(get_secret, user_id, agent_id, conversation_id)


def _router_outils(message_utilisateur, outils_disponibles, historique=None):
    """
    Bouton Outils, couche de suggestion automatique (2026-07-28, demande
    Bourama). Coexiste avec la sélection manuelle (BarreDeSaisie.tsx) sans
    la remplacer : les deux retombent sur le même mécanisme final
    (outil_force -> lister_tous_les_outils -> system prompt).

    Premier appel LLM séparé, rapide (pas le modèle qui répond à
    l'utilisateur), qui juge lesquels des outils RÉELLEMENT autorisés
    pour cet agent (outils_disponibles, déjà filtré par
    lister_tous_les_outils AVANT le filtre outil_force -- jamais le
    catalogue brut du registre) seraient pertinents pour la question. Ne
    répond jamais à la question lui-même, ne décide rien à la place de
    l'utilisateur : le résultat sert juste à proposer des boutons côté
    frontend (voir chat(), événement SSE "outils_suggeres"), que
    l'utilisateur clique ou ignore.

    historique (2026-07-31, demande Bourama, correction "routeur suggère
    mal") : quelques derniers échanges de la conversation, même format que
    messages_base (liste de {"role", "content"}), pour que le routeur juge
    avec le contexte de la discussion et pas seulement la dernière phrase
    isolée (ex: "et pour le fichier PDF ?" ne veut rien dire sans savoir de
    quel fichier on parlait). Optionnel et borné aux 4 derniers messages
    pour ne pas alourdir cet appel censé rester rapide et bon marché.

    Renvoie une liste de noms d'outils (sous-ensemble de
    outils_disponibles, éventuellement vide). Fail-safe strict : toute
    erreur ou réponse mal formée renvoie une liste vide plutôt que de
    bloquer la réponse normale -- ce routeur ne doit jamais empêcher
    l'utilisateur d'obtenir une réponse.
    """
    if not outils_disponibles or not message_utilisateur:
        return []

    noms_valides = {o["function"]["name"] for o in outils_disponibles}
    catalogue = "\n".join(
        f"- {o['function']['name']} : {_resume_description_outil(o['function']['description'])}"
        for o in outils_disponibles
    )

    contexte = ""
    if historique:
        derniers = historique[-4:]
        lignes = "\n".join(
            f"{m.get('role', '?')} : {m.get('content', '')}" for m in derniers
        )
        contexte = f"Derniers échanges de la conversation (contexte) :\n{lignes}\n\n"

    prompt_routeur = (
        "Tu es un routeur d'outils : tu ne réponds JAMAIS à la question "
        "toi-même, tu décides seulement quels outils (parmi la liste "
        "ci-dessous) seraient utiles pour y répondre. Si aucun outil "
        "n'est pertinent (question générale, conversation normale, "
        "salutation...), renvoie une liste vide.\n\n"
        "IMPORTANT : diagramme, graphique/chart, carte/localisation, "
        "figure géométrique et mini-outil interactif (widget) NE SONT "
        "JAMAIS des outils de cette liste -- ce sont des blocs que le "
        "modèle principal écrit lui-même directement dans sa réponse, "
        "affichés nativement par l'interface. Une demande de ce type "
        "(\"fais-moi un diagramme de...\", \"montre-moi une carte de...\", "
        "\"trace un graphique de...\") ne justifie donc JAMAIS de "
        "suggestion, même si un outil de la liste semble vaguement "
        "proche -- réponds liste vide dans ce cas.\n\n"
        # CORRECTIF 2026-07-31 (signalé par Bourama, test réel : le
        # routeur suggérait une recherche web pour "1+1") : un petit
        # modèle rapide (voir MODELE_ROUTEUR_OUTILS) a besoin d'exemples
        # concrets, pas seulement d'une règle abstraite -- "évite les
        # outils inutiles" ne suffit pas à empêcher un réflexe "au cas
        # où" sur une question triviale. Les exemples ci-dessous couvrent
        # explicitement calcul simple, connaissance générale stable et
        # salutation/conversation normale.
        # UNIFICATION 2026-08-29 (signalé par Bourama : "il mélange le
        # privé et le public avec la base de connaissance de Clovis" --
        # même si chaque monde de documents avait déjà reçu sa propre
        # règle au fil des bugs (15/08 bibliothèque perso, 18/08 base de
        # connaissance, 28/08 catalogue public/plugins publics), ces
        # règles étaient ajoutées une par une, séparément, jamais les
        # unes à côté des autres -- le petit modèle 8B/20B n'avait donc
        # jamais vu les 5 mondes (web compris, qui n'avait AUCUNE règle
        # jusqu'ici) posés côte à côte avec une seule frontière claire
        # entre chaque paire. Bloc réécrit en un seul morceau, structuré
        # comme UNE SEULE décision de tri à 5 branches plutôt que 4
        # règles indépendantes ajoutées au fil de l'eau.
        "Il existe QUATRE mondes de documents/information totalement "
        "différents. Pour CHAQUE question, commence par identifier à "
        "quel monde elle appartient AVANT de choisir un outil -- ne te "
        "fie JAMAIS à un mot-clé ('bibliothèque', 'public', 'catalogue', "
        "'Clovis'...), base-toi sur l'intention réelle :\n\n"
        "1) MES DOCUMENTS À MOI -- cours, exercice, fichier que "
        "L'ÉTUDIANT LUI-MÊME a uploadé dans SA bibliothèque personnelle. "
        "-> gerer_document_bibliotheque (action \"chercher\"). "
        "Exemples : \"explique-moi le chapitre 3\", \"résume mon cours "
        "sur les intégrales\", \"qu'est-ce que dit mon document sur la "
        "photosynthèse ?\", \"aide-moi avec l'exercice 4\".\n\n"
        "2) CLOVIS / L'APPLICATION ELLE-MÊME -- comment fonctionne "
        "Clovis, ses fonctionnalités, un bug, une question sur "
        "l'application, même vaguement. L'utilisateur ne sait pas que "
        "cette base de connaissances existe, ne connaît aucun nom "
        "d'outil, et ne dira jamais \"cherche dans la base de "
        "connaissance\" -- mets-toi à sa place. "
        "-> gerer_base_connaissance. "
        "Exemples : \"comment fonctionne le partage de code sur "
        "Clovis ?\", \"est-ce que tu peux générer un PDF ?\", \"c'est "
        "quoi la bibliothèque dans l'appli ?\", \"comment je crée un "
        "programme ?\", \"ça bug chez moi, tu peux m'aider ?\", \"c'est "
        "quoi Clovis ?\", \"je comprends pas comment marche cette "
        "fonctionnalité\".\n\n"
        "3) CATALOGUE PUBLIC -- LOCALISER un document dans la section "
        "\"Bibliothèque publique\", ouverte à tout le monde, PAS "
        "l'étudiant qui l'a uploadé, PAS Clovis lui-même. "
        "-> gerer_document_bibliotheque (action "
        "\"trouver_catalogue_public\"). "
        "Exemples : \"trouve-moi un document sur la thermodynamique "
        "dans la bibliothèque publique\", \"y a-t-il un cours sur la "
        "Révolution française dans le catalogue public ?\". "
        # AJOUT 09/09/2026 (demande Bourama : dossiers/filtres + gestion
        # d'une entrée du catalogue public, jusque-là invisibles au
        # LLM) : deux outils supplémentaires du même monde 3, à
        # suggérer EN PLUS de gerer_document_bibliotheque dès que la
        # demande porte sur un DOSSIER du catalogue public ou sur son
        # ÉCRITURE plutôt que sa simple localisation.
        "gerer_dossier_catalogue_public : NAVIGUER dans les dossiers "
        "du catalogue public (lister, ouvrir un dossier précis), ou "
        "AGIR sur eux (créer, renommer, supprimer, y ranger/retirer un "
        "document). Exemples : \"quels sont les dossiers de la "
        "bibliothèque publique ?\", \"ouvre le dossier Chimie du "
        "catalogue public\", \"crée un dossier public pour le "
        "bac\", \"range ce document publié dans le dossier X\". "
        "gerer_entree_catalogue_public : PUBLIER un nouveau document/"
        "lien/note dans le catalogue public, MODIFIER ou SUPPRIMER une "
        "entrée déjà publiée, ou la COPIER vers sa bibliothèque "
        "personnelle. Exemples : \"publie ce document dans la "
        "bibliothèque publique\", \"ajoute ce lien au catalogue "
        "public\", \"change la description de ce document publié\", "
        "\"retire ce document du catalogue public\", \"copie ce "
        "document de la bibliothèque publique dans ma bibliothèque\", "
        "\"télécharge ce document public chez moi\".\n\n"
        "4) WEB -- tout ce qui n'est NI un document de l'étudiant, NI "
        "Clovis/l'application, NI le catalogue public : actualité, "
        "information générale externe, sujet "
        "sans rapport avec Clovis ou les documents de l'étudiant. "
        "-> tavily_search. "
        "Exemples : \"quelle est la capitale du Japon ?\", \"donne-moi "
        "les dernières nouvelles sur X\", \"c'est quoi la photosynthèse "
        "?\" (question générale, PAS \"MON cours sur la photosynthèse\" "
        "qui est le monde 1). Ne suggère PAS tavily_search pour une "
        "connaissance générale stable que tu connais déjà sans "
        "recherche (ex: \"1+1\", \"capitale de la France\") -- même "
        "règle que plus haut, une info ne devient pas une recherche web "
        "juste parce qu'elle est \"externe\" à Clovis.\n\n"
        "Piège fréquent à éviter : une question généraliste et une "
        "question sur LES documents personnels de l'étudiant peuvent se "
        "ressembler en surface (\"c'est quoi la mitose ?\" = web ou "
        "connaissance générale, \"c'est quoi dans MON cours sur la "
        "mitose ?\" = monde 1) -- le mot \"mon\"/\"ma\" ou une référence "
        "implicite à un cours déjà uploadé est le signal, pas le sujet "
        "lui-même.\n\n"
        "SI TU HÉSITES entre plusieurs mondes, élargis au lieu de "
        "choisir au hasard : hésitation entre perso et publique -> "
        "suggère les 2 ; hésitation plus large (type \"cherche-moi...\") "
        "-> suggère perso + publique + web ; hésitation totale -> "
        "suggère les 4 (perso, publique, web, base de connaissance).\n\n"
        # AJOUT 2026-08-22 (demande Bourama : "les skills ont été
        # corrigés visuellement, mais intérieurement non, il faut que
        # le LLM soit au courant") : lister_comportements/
        # consulter_comportement (devenus gerer_comportement, actions
        # "lister"/"consulter", consolidation du 26/08) existaient déjà
        # dans le catalogue, mais rien n'apprenait au routeur que
        # "skill(s)" -- le SEUL terme utilisé partout dans l'interface --
        # désigne cette fonctionnalité ("comportement" reste un nom
        # interne, jamais vu par l'utilisateur). Sans cette règle, une
        # question comme "quels sont mes skills ?" ne matchait rien : le
        # routeur ne proposait pas cet outil, et le grand modèle
        # répondait à côté (confusion avec "compétences personnelles").
        "IMPORTANT : gerer_comportement (action \"lister\") DOIT être "
        "suggéré dès que l'utilisateur demande à voir/lister ses "
        "\"skills\" (le SEUL mot utilisé dans toute l'interface Clovis "
        "pour cette fonctionnalité -- \"comportement\" est un nom interne, "
        "ignore-le pour reconnaître l'intention). Exemples qui DOIVENT "
        "suggérer cet outil : \"quels sont mes skills ?\", \"montre-moi "
        "mes skills\", \"liste mes skills/comportements\", \"j'ai combien "
        "de skills ?\". Ne confonds JAMAIS ça avec une question sur les "
        "compétences, talents ou aptitudes personnelles de l'utilisateur "
        "(\"qu'est-ce que je sais bien faire ?\") -- aucun rapport, ne "
        "suggère rien dans ce cas.\n\n"
        # AJOUT 09/09/2026 (demande Bourama : "pareil avec les skills "
        # publique" -- avant cet ajout, le catalogue public de skills
        # ("Catalogue" de l'interface) était invisible du LLM). NE PAS
        # confondre avec gerer_comportement ci-dessus (skills PERSONNELS,
        # jamais publiés) : même piège que le monde bibliothèque
        # personnelle/catalogue public plus haut, transposé aux skills.
        "IMPORTANT : ne confonds JAMAIS \"mes skills\" (personnels, "
        "gerer_comportement) avec le CATALOGUE PUBLIC de skills (visible "
        "par tout le monde -> gerer_comportement_public). Exemples "
        "catalogue public : \"cherche un skill sur la prise de notes "
        "dans le catalogue\", \"quels sont les skills les plus utilisés "
        "?\", \"active ce skill du catalogue\", \"publie ce skill dans "
        "le catalogue public\", \"retire mon skill du catalogue "
        "public\". SI TU HÉSITES entre les deux, suggère les deux "
        "outils.\n\n"
        # AJOUT 2026-09-01 (demande Bourama, suite au renommage de
        # gerer_action_mobile en gerer_dossier_telephone) : le mot
        # "dossier" désigne DEUX choses totalement différentes dans le
        # catalogue, un piège classique pour un petit modèle qui ne
        # regarde que le mot-clé. Même logique que le bloc "quatre
        # mondes" plus haut : en cas de doute, élargir plutôt que
        # choisir au hasard OU ne rien suggérer -- l'erreur qui coûte le
        # plus cher ici est de rester silencieux, pas de suggérer un
        # outil de trop.
        "IMPORTANT : ne confonds JAMAIS un dossier de la BIBLIOTHÈQUE "
        "Clovis PERSONNELLE (documents/liens/notes privés de "
        "l'étudiant -> gerer_dossier_bibliotheque), un dossier du "
        "CATALOGUE PUBLIC (visible par tout le monde, statut "
        "contribution_libre/privee -> gerer_dossier_catalogue_public, "
        "AJOUT 09/09/2026), et un dossier PHYSIQUE sur le TÉLÉPHONE de "
        "l'étudiant (fichiers réels de son appareil, aucun rapport avec "
        "la bibliothèque Clovis -> gerer_dossier_telephone + "
        "explorer_dossier). Exemples bibliothèque personnelle : "
        "\"crée-moi un dossier pour mes cours de maths\", \"range ce "
        "document dans un nouveau dossier\", \"supprime mon dossier "
        "Chimie\" (dans l'app, sans mention de partage/de tout le "
        "monde). Exemples catalogue public : \"crée un dossier public "
        "pour le bac\", \"quels sont les dossiers de la bibliothèque "
        "publique ?\", \"range ce document publié dans le dossier X\", "
        "\"est-ce que d'autres personnes peuvent ajouter des documents "
        "à ce dossier public ?\". Exemples téléphone : "
        "\"crée un dossier Téléchargements sur mon "
        "téléphone\", \"renomme le dossier Photos sur mon tel\", "
        "\"regarde mon dossier sur mon téléphone et dis-moi ce qu'il y "
        "a dedans\", \"qu'est-ce qu'il y a dans mon dossier Cours sur "
        "mon téléphone ?\", \"envoie-moi cette photo depuis mon "
        "téléphone\", \"donne-moi ce fichier qui est dans mon dossier "
        "Cours sur mon tel\". RÈGLE STRICTE : gerer_dossier_telephone et "
        "explorer_dossier forment UNE SEULE paire, jamais suggérés "
        "séparément -- dès que le monde téléphone est identifié, "
        "suggère TOUJOURS les DEUX ensemble (explorer_dossier a besoin "
        "des noms listés par gerer_dossier_telephone pour fonctionner). "
        "SI TU HÉSITES entre bibliothèque personnelle, catalogue public "
        "et téléphone, suggère les outils des mondes en doute.\n\n"
        # AJOUT 2026-09-04 (bug remonté par Bourama : le nouveau
        # comportement "donner le fichier en pièce jointe" -- actions
        # "donner"/"donner_catalogue_public" de gerer_document_bibliotheque,
        # voir [[clovis-fichiers-pieces-jointes]] -- ne se déclenchait
        # jamais). Cause trouvée : AUCUN exemple ci-dessus ne ressemble à
        # une demande de fichier lui-même (tous parlent d'expliquer/
        # résumer/localiser un contenu), donc le petit routeur ne
        # suggérait gerer_document_bibliotheque pour aucune de ces
        # formulations -- outil absent du tour, donc littéralement
        # impossible à appeler pour le grand modèle, quelle que soit sa
        # docstring (le routeur ne voit qu'un résumé de 200 caractères
        # par outil, jamais la liste de ses actions).
        "IMPORTANT : dès que l'utilisateur veut RECEVOIR le fichier "
        "lui-même (pas juste son contenu/résumé/explication), suggère "
        "TOUJOURS gerer_document_bibliotheque, que le fichier soit dans "
        "SA bibliothèque personnelle (monde 1), dans le catalogue "
        "public (monde 3), qu'il ait déjà été ENVOYÉ par l'utilisateur "
        "PLUS TÔT DANS CETTE CONVERSATION, OU qu'il ait déjà été GÉNÉRÉ "
        "PAR TOI plus tôt dans cette même conversation (image, document, "
        "audio, vidéo...) -- un fichier que tu génères est automatiquement "
        "enregistré dans la bibliothèque personnelle de l'utilisateur, "
        "donc retrouvable ensuite. Si le fichier demandé est physiquement "
        "SUR LE TÉLÉPHONE de l'étudiant plutôt que dans sa bibliothèque "
        "Clovis, suggère plutôt la paire gerer_dossier_telephone + "
        "explorer_dossier (voir règle du monde téléphone plus haut) --"
        " explorer_dossier a une action dédiée \"donner_fichier\" pour "
        "ce cas précis. Signal de reconnaissance : "
        "\"donne-moi\", \"envoie-moi\", \"renvoie-moi\", \"redonne-moi\", "
        "\"j'aimerais avoir/récupérer le fichier\", \"mets-le en pièce "
        "jointe\", \"attache ce document\" -- appliqué à un fichier, pas à "
        "une information. Exemples qui DOIVENT suggérer cet outil : "
        "\"donne-moi le PDF sur les intégrales\", \"envoie-moi ce document "
        "de la bibliothèque publique\", \"renvoie-moi le fichier que je "
        "viens de t'envoyer\", \"redonne-moi l'image que tu as générée tout "
        "à l'heure\", \"renvoie-moi le document que tu m'as fait plus tôt\". "
        "Ne confonds pas avec une demande de contenu/résumé (\"explique-moi "
        "ce PDF\", \"résume ce document\") qui reste couverte par les règles "
        "des mondes 1/3 ci-dessus, sans rapport avec cet ajout.\n\n"
        f"Outils disponibles :\n{catalogue}\n\n"
        f"{contexte}"
        f"Question de l'utilisateur : {message_utilisateur}\n\n"
        "Réponds UNIQUEMENT avec un objet JSON de la forme "
        '{"outils": ["nom_outil_1", "nom_outil_2"]} (noms EXACTEMENT '
        "comme listés ci-dessus, liste vide si rien n'est pertinent)."
    )

    def _appeler_et_parser(model, avec_reasoning_effort, avec_outils_desactives):
        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
        kwargs = dict(
            model=model,
            messages=[{"role": "user", "content": prompt_routeur}],
            response_format={"type": "json_object"},
            max_completion_tokens=500,
            timeout=DELAI_MAX_PAR_APPEL,
        )
        if avec_reasoning_effort:
            # 18/08 : MODELE_ROUTEUR_OUTILS (openai/gpt-oss-20b) est un
            # modele de raisonnement -- sans reasoning_effort explicite, il
            # tourne par defaut en "medium" (doc Groq), et ce raisonnement
            # est compte DANS max_completion_tokens. Avec l'ancienne valeur
            # (200), le modele epuisait son budget en raisonnement avant
            # meme d'ecrire le JSON final -> erreur "max completion tokens
            # reached before generating a valid document", quelle que soit
            # la taille du catalogue envoye (c'est un budget de SORTIE, pas
            # d'entree -- reduire le catalogue n'y changeait donc rien).
            # reasoning_effort="low" limite ce raisonnement, et 500 (au
            # lieu de 200) laisse une marge de securite pour le JSON final.
            kwargs["reasoning_effort"] = "low"
        if avec_outils_desactives:
            # Voir MODELE_ROUTEUR_OUTILS_REPLI dans constantes_agent.py :
            # compound-mini est agentique, il faut lui interdire d'appeler
            # ses outils integres pour ce simple routage (json_object est
            # sinon incompatible avec l'appel d'outils cote Groq).
            kwargs["compound_custom"] = {"tools": {"enabled_tools": []}}

        completion = client_groq.chat.completions.create(**kwargs)
        brut = completion.choices[0].message.content.strip()
        suggestion = json.loads(brut)
        return [n for n in suggestion.get("outils", []) if n in noms_valides]

    try:
        outils_suggeres = _appeler_et_parser(
            MODELE_ROUTEUR_OUTILS, avec_reasoning_effort=False, avec_outils_desactives=True
        )
        logging.info(f"Routeur d'outils -> suggérés : {outils_suggeres or '(aucun)'}")
        return outils_suggeres
    except Exception as e_principal:
        logging.warning(f"Routeur d'outils ({MODELE_ROUTEUR_OUTILS}) en echec, repli sur {MODELE_ROUTEUR_OUTILS_REPLI} : {e_principal}")
        try:
            outils_suggeres = _appeler_et_parser(
                MODELE_ROUTEUR_OUTILS_REPLI, avec_reasoning_effort=True, avec_outils_desactives=False
            )
            logging.info(f"Routeur d'outils (repli) -> suggérés : {outils_suggeres or '(aucun)'}")
            return outils_suggeres
        except Exception as e_repli:
            logging.error(f"ERREUR routeur outils (principal + repli) : {e_principal} / {e_repli}")
            return []


