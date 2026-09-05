# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Detection/filtrage du texte pendant qu'il arrive en
# streaming depuis le modele (JSON casse, faux appel d'outil imite en
# texte brut, URL en cours de frappe, etc.), pour ne jamais laisser
# passer ces artefacts tels quels dans la reponse affichee.
import re
from registre_outils import REGISTRE_AFFICHAGE_OUTILS

def _ressemble_a_du_json_casse(texte: str) -> bool:
    """
    Heuristique pour un bug Groq connu et non resolu sur gpt-oss-120b (voir
    community.groq.com/t/670 -- "Reasoning tokens and gibberish output
    appearing in responses despite configuration to hide reasoning") : le
    modele melange parfois des arguments d'appel d'outil (JSON brut) dans
    delta.content (le texte de reponse visible) au lieu de passer par
    delta.tool_calls comme prevu. Plus frequent avec 3+ outils actifs ou une
    conversation longue -- notre cas avec Notion/Wolfram/Tavily. Signale par
    Bourama (24/07) : "souvent il donne dans le chat le json qu'il reçoit".
    Pas un bug corrige par un parametre (arrive meme avec reasoning_format=
    "hidden" d'apres les rapports) -- on ne peut que le detecter et masquer
    le debut suspect plutot que l'afficher tel quel a l'utilisateur.

    IMPORTANT (retour Bourama, 25/07) : ne PAS juste tester "ca commence par
    { ou [" -- si l'utilisateur demande explicitement un vrai JSON ("donne-moi
    un JSON avec..."), sa reponse legitime commence pareil et serait masquee
    a tort. On exige donc en plus la signature precise d'un appel d'outil
    Groq rate (les cles "name" ET "arguments" pres du debut, la structure
    interne que Groq utilise pour le tool calling) -- un vrai JSON demande
    par l'utilisateur a quasiment jamais ces deux cles precises ensemble.
    """
    debut = texte.lstrip()
    if not (debut.startswith("{") or debut.startswith("[")):
        return False
    return '"name"' in debut and '"arguments"' in debut


def _ressemble_a_une_simple_url(contenu: str) -> bool:
    """
    Vrai si le resultat d'un outil n'est (essentiellement) qu'un lien nu,
    comme le renvoient generer_image/generer_document/generer_code/
    generer_site_zip/deployer_site... -- typiquement une courte phrase
    d'accompagnement suivie d'une URL, sans structure JSON. Sert a
    exclure ces resultats de _debut_provient_d_un_resultat_outil : les
    reutiliser dans la reponse est le comportement normal et voulu, pas
    une fuite a masquer.
    """
    c = contenu.strip()
    return ("http://" in c or "https://" in c) and "{" not in c and "\"name\"" not in c


def _debut_provient_d_un_resultat_outil(debut: str, messages_agent) -> bool:
    """
    Deuxieme cas signale par Bourama (25/07), distinct de
    _ressemble_a_du_json_casse : le modele recopie parfois tel quel le
    JSON BRUT renvoye par un outil (GitHub, Notion, Tavily, Wolfram...)
    comme si c'etait sa reponse, au lieu de le resumer en langage naturel.
    Contrairement au bug d'appel d'outil rate, ce JSON n'a pas forcement
    les cles "name"/"arguments" -- sa forme depend entierement de l'outil
    source, donc pas de pattern generique fiable. On compare plutot
    directement au texte des resultats d'outils recus DANS CE TOUR
    (messages_agent, role="tool", toujours groupes juste avant l'appel
    Groq courant -- voir _traiter_appels) : si le debut de la reponse est
    un extrait verbatim d'un de ces resultats, c'est une recopie brute,
    peu importe l'outil ou le format.
    """
    debut = debut.strip()
    if len(debut) < 15:
        return False
    for message in reversed(messages_agent):
        if message.get("role") != "tool":
            break  # les messages "tool" d'un meme tour sont toujours groupes en fin de liste
        contenu = message.get("content")
        # CORRECTION (31/07, signalee par Bourama -- lien image/pdf tronque
        # a l'affichage) : l'ancienne comparaison (`debut[:40] in contenu`)
        # declenchait un faux positif des qu'une URL renvoyee par un outil
        # de generation (image/pdf/code...) etait reutilisee -- normalement
        # -- par le modele dans sa reponse markdown : cette URL apparait
        # par definition dans le contenu de l'outil, meme quand le modele
        # l'integre proprement dans une phrase. Ancrer la comparaison sur
        # le DEBUT du contenu de l'outil ne suffit pas non plus : quand le
        # buffer de streaming est coupe pile au debut de l'URL (cf
        # _position_sure_pour_flush), ce debut coincide quand meme avec le
        # debut du resultat de l'outil. La vraie distinction est donc :
        # un resultat d'outil qui n'est QU'une URL nue (generer_image,
        # generer_document, generer_code...) est fait pour etre reutilise
        # tel quel -- ce n'est jamais une "fuite" -- alors qu'un resultat
        # structure (JSON de GitHub/Notion/Tavily/Wolfram...) recopie
        # verbatim, lui, est bien le bug vise ici. On ignore donc les
        # resultats d'outils qui ne sont qu'un lien.
        if not isinstance(contenu, str):
            continue
        if _ressemble_a_une_simple_url(contenu):
            continue
        if debut[:40] in contenu:
            return True
    return False


_RE_DEBUT_TOOL_CODE = re.compile(r"```\s*tool_code\b", re.IGNORECASE)


def _trouver_debut_tool_code(texte: str):
    """
    Troisieme cas de fuite signale par Bourama (28/07) : au lieu d'un JSON
    casse ou d'une recopie de resultat, le modele ecrit carrement un FAUX
    appel d'outil sous forme de bloc de code cloture ```TOOL_CODE ... ```
    (ex: print(generer_image(prompt='...'))) au lieu d'utiliser le vrai
    mecanisme de tool calling de l'API.

    CORRECTION (29/07, signalee par Bourama) : l'ancienne version
    (_ressemble_a_un_pseudo_appel_outil) ne regardait que le tout DEBUT du
    texte -- si une phrase legitime precedait le faux bloc dans la meme
    fenetre de streaming (ex: "Je lance les operations pour le reste.\n\n
    ```TOOL_CODE"), la verification voyait une phrase normale en premier et
    laissait tout passer, faux bloc inclus. Cette fonction cherche desormais
    le marqueur ```TOOL_CODE N'IMPORTE OU dans le texte et retourne sa
    position (ou None si absent), pour permettre de ne masquer QUE le bloc
    lui-meme -- pas la phrase legitime qui le precede.
    """
    m = _RE_DEBUT_TOOL_CODE.search(texte)
    return m.start() if m else None


_RE_DEBUT_CALL_OUTIL = re.compile(r"\bcall:[A-Za-z_][A-Za-z0-9_]*\{")


def _trouver_debut_call_outil(texte: str):
    """
    Quatrieme cas de fuite signale par Bourama (29/07, captures d'ecran a
    l'appui), distinct du bloc ```TOOL_CODE``` : le modele ecrit un faux
    appel d'outil directement dans le texte visible, sans backticks ni
    print(), sous la forme "call:nom_outil{...json...}", ex :
        call:generer_image{"prompt":"Un chat elegant..."}
        call:tavily_search{"query":"informations sur les chats"}
    Ni _trouver_debut_tool_code (cherche des backticks) ni
    _reponse_suspecte_generique (cherche des cles "name"/"arguments" dans
    un JSON qui commence en debut de fenetre) ne detectaient ce motif --
    confirme en le testant contre les 3 captures d'ecran recues. Cherche
    le marqueur "call:nom_outil{" n'importe ou dans le texte et retourne
    sa position (ou None si absent) ; meme logique de decoupage precis
    que _trouver_debut_tool_code (ne masque que le faux appel, jamais le
    texte legitime autour).
    """
    m = _RE_DEBUT_CALL_OUTIL.search(texte)
    return m.start() if m else None


def _position_fin_bloc_call_outil(bloc_buffer: str):
    """
    A appeler uniquement sur un bloc_buffer qui commence par un motif
    detecte via _trouver_debut_call_outil. Compte les accolades une par
    une (au lieu de s'arreter a la premiere "}" venue) pour gerer un
    argument JSON lui-meme imbrique, ex: call:generer_code{"nom_projet":
    "x","fichiers":{"main.py":"..."}} -- s'arreter a la premiere "}"
    couperait avant la vraie fin.

    Si un autre "call:nom{" enchaine juste apres (espaces/retours a la
    ligne autorises entre les deux, comme les plusieurs print() d'un
    bloc TOOL_CODE), il est absorbe dans le meme bloc a masquer plutot
    que de rouvrir un nouveau passage "reponse" au milieu.

    Retourne la position de fin (exclusive) une fois sur qu'aucun autre
    appel n'enchaine juste apres, ou None si le bloc est encore en cours
    de reception (JSON pas complet, ou fin de fragment ambigue en plein
    milieu d'espaces -- on attend alors la suite du streaming plutot que
    de risquer une coupure trop tot).
    """
    position = 0
    while True:
        m = _RE_DEBUT_CALL_OUTIL.match(bloc_buffer, position)
        if not m:
            return position if position > 0 else None
        profondeur = 0
        fin_accolade = None
        i = m.end() - 1  # position du "{" ouvrant qui vient d'etre matche
        while i < len(bloc_buffer):
            if bloc_buffer[i] == "{":
                profondeur += 1
            elif bloc_buffer[i] == "}":
                profondeur -= 1
                if profondeur == 0:
                    fin_accolade = i + 1
                    break
            i += 1
        if fin_accolade is None:
            return None  # JSON de cet appel pas encore complet
        suite = bloc_buffer[fin_accolade:]
        suite_sans_espaces = suite.lstrip(" \t\r\n")
        if not suite_sans_espaces:
            return None  # ambigu : un autre appel pourrait suivre juste apres, on attend la suite
        if _RE_DEBUT_CALL_OUTIL.match(suite_sans_espaces):
            position = fin_accolade + (len(suite) - len(suite_sans_espaces))
            continue
        return fin_accolade


def _reponse_suspecte_generique(buffer_debut: str, messages_agent) -> bool:
    """Les 2 filets de securite "tout ou rien" contre les bugs Groq connus
    (JSON casse, recopie brute d'un resultat d'outil) -- le 3e cas (faux
    bloc TOOL_CODE) est gere a part via _trouver_debut_tool_code, qui
    permet de ne masquer que le bloc precis plutot que tout le passage."""
    return (
        _ressemble_a_du_json_casse(buffer_debut)
        or _debut_provient_d_un_resultat_outil(buffer_debut, messages_agent)
    )


SEUIL_VERIF_JSON = 60
# Marge de securite (29/07, elargie le 29/07 pour couvrir aussi le motif
# "call:nom_outil{", voir _trouver_debut_call_outil). Le plus long nom
# d'outil enregistre (ex: "generer_document_powerpoint") donne un motif
# "call:generer_document_powerpoint{" d'environ 34 caracteres -- largement
# plus long que "```TOOL_CODE" (~12 caracteres). Si on flush tout le
# buffer des que SEUIL_VERIF_JSON est atteint, on risque de couper un
# motif en deux pile au mauvais moment (ex: le buffer contient juste
# "call:generer_doc" quand le seuil est atteint) -- le debut partirait en
# "reponse" normale et le reste, arrivant dans le fragment suivant, ne
# serait plus jamais reconnu comme un faux appel (le debut manquant est
# deja parti). On garde donc toujours les RESERVE_SUFFIXE derniers
# caracteres du buffer en attente, jamais flushes tant qu'on n'est pas
# sur qu'ils ne sont pas le debut d'un motif.
RESERVE_SUFFIXE = 50


_CARACTERES_FIN_URL = (" ", "\n", "\t", ")", "]", '"', "'")


def _position_sure_pour_flush(buffer: str, position_max: int) -> int:
    """
    Renvoie une position <= position_max a laquelle on peut flusher sans
    risquer de couper une URL en plein milieu (bug signale par Bourama le
    31/07 : lien d'image/pdf tronque et casse a l'affichage cote
    frontend). Cherche la derniere occurrence de "http" avant
    position_max ; si rien entre ce "http" et position_max ne ressemble a
    une fin d'URL (espace, retour a la ligne, guillemet, parenthese ou
    crochet fermant), on considere l'URL encore en cours de formation et
    on recule le point de flush jusqu'a son debut -- elle sera flushee
    d'un seul bloc une fois complete, au prochain passage.
    """
    dernier_http = buffer.rfind("http", 0, position_max)
    if dernier_http == -1:
        return position_max
    segment = buffer[dernier_http:position_max]
    if any(caractere in segment for caractere in _CARACTERES_FIN_URL):
        return position_max  # l'URL semble deja terminee avant position_max
    return dernier_http


def _nouvel_etat_filtre_texte():
    """Etat initial pour _traiter_fragment_texte / _finaliser_fragment_texte
    (voir ces fonctions). Un etat par passage de streaming Groq."""
    return {
        "phase": "avant",   # "avant" (texte normal en cours de verification) ou "dans_bloc" (faux appel en cours)
        "buffer": "",       # texte en attente de decision, phase "avant"
        "bloc_buffer": "",  # texte du faux bloc en cours, phase "dans_bloc"
        "type_bloc": None,  # "tool_code" ou "call_outil" -- decide comment reperer la fin du bloc
        "tool_code_detecte": False,  # devient True des qu'un faux bloc a ete vu (partiel ou complet)
    }


def _traiter_fragment_texte(etat, fragment, messages_agent):
    """
    Traite un nouveau fragment de texte recu du streaming Groq, en isolant
    precisement un eventuel faux appel d'outil -- soit un bloc ```TOOL_CODE
    ... ``` (voir _trouver_debut_tool_code), soit un motif call:nom{...}
    (voir _trouver_debut_call_outil) : tout ce qui est AVANT le faux appel
    est affiche normalement ("reponse"), le faux appel lui-meme est masque
    ("raisonnement"), et tout ce qui vient APRES redevient visible
    normalement -- au lieu d'un comportement "tout ou rien" ou une fois
    suspect, tout le reste du passage restait cache.

    Retourne la liste des evenements a yield. Mute `etat` en place.
    """
    evenements = []

    if etat["phase"] == "dans_bloc":
        etat["bloc_buffer"] += fragment
        if etat["type_bloc"] == "call_outil":
            fin = _position_fin_bloc_call_outil(etat["bloc_buffer"])
        else:
            fin = etat["bloc_buffer"].find("```", 3)  # cherche la fermeture APRES l'ouvrant (3 premiers caracteres)
            fin = None if fin == -1 else fin + 3
        if fin is None:
            return evenements  # bloc toujours en cours, rien a afficher pour l'instant
        evenements.append({"type": "raisonnement", "texte": etat["bloc_buffer"][:fin]})
        reste = etat["bloc_buffer"][fin:]
        etat["phase"] = "avant"
        etat["buffer"] = ""
        etat["bloc_buffer"] = ""
        etat["type_bloc"] = None
        if reste:
            evenements.extend(_traiter_fragment_texte(etat, reste, messages_agent))
        return evenements

    etat["buffer"] += fragment
    position_tool_code = _trouver_debut_tool_code(etat["buffer"])
    position_call_outil = _trouver_debut_call_outil(etat["buffer"])
    positions = [
        (p, t) for p, t in ((position_tool_code, "tool_code"), (position_call_outil, "call_outil")) if p is not None
    ]
    if positions:
        position, type_bloc = min(positions, key=lambda pt: pt[0])
        avant = etat["buffer"][:position]
        if avant:
            evenements.append({"type": "reponse", "texte": avant})
        etat["tool_code_detecte"] = True
        etat["phase"] = "dans_bloc"
        etat["type_bloc"] = type_bloc
        etat["bloc_buffer"] = etat["buffer"][position:]
        etat["buffer"] = ""
        evenements.extend(_traiter_fragment_texte(etat, "", messages_agent))
        return evenements

    if len(etat["buffer"]) >= SEUIL_VERIF_JSON + RESERVE_SUFFIXE:
        # On ne flush que le buffer MOINS la marge de securite finale, pour
        # ne jamais couper un motif en cours de formation (voir le
        # commentaire de RESERVE_SUFFIXE plus haut).
        position_max = len(etat["buffer"]) - RESERVE_SUFFIXE
        # SECURITE SUPPLEMENTAIRE (31/07, signalee par Bourama -- lien
        # image/pdf tronque a l'affichage) : ne jamais flusher en plein
        # milieu d'une URL non terminee (ex. "...supabase.co/storage/v1/
        # object" sans le reste du chemin ni la parenthese fermante du
        # markdown) -- meme si la classification reponse/raisonnement est
        # correcte, couper une URL en deux casse le rendu du lien ou de
        # l'image cote frontend. Si le dernier "http" avant position_max
        # ne semble pas encore termine (aucun espace/saut de ligne/") ]"
        # apres), on recule le point de flush jusqu'au debut de cette URL
        # et on attend la suite du streaming pour la flusher d'un bloc.
        position_flush = _position_sure_pour_flush(etat["buffer"], position_max)
        a_flusher = etat["buffer"][:position_flush]
        etat["buffer"] = etat["buffer"][position_flush:]
        if a_flusher:
            if _reponse_suspecte_generique(a_flusher, messages_agent):
                evenements.append({"type": "raisonnement", "texte": a_flusher})
            else:
                evenements.append({"type": "reponse", "texte": a_flusher})
    return evenements


def _finaliser_fragment_texte(etat, messages_agent):
    """A appeler une fois le flux Groq termine : vide le reliquat de
    `etat`, quelle que soit la phase en cours. Si le flux s'est arrete en
    plein milieu d'un faux appel (TOOL_CODE ou call:outil{...}, fermeture
    jamais recue), le reliquat est tout de meme masque et
    tool_code_detecte reste True."""
    evenements = []
    if etat["phase"] == "dans_bloc":
        if etat["bloc_buffer"]:
            evenements.append({"type": "raisonnement", "texte": etat["bloc_buffer"]})
        etat["tool_code_detecte"] = True
        etat["bloc_buffer"] = ""
        etat["type_bloc"] = None
    elif etat["buffer"]:
        if _reponse_suspecte_generique(etat["buffer"], messages_agent):
            evenements.append({"type": "raisonnement", "texte": etat["buffer"]})
        else:
            evenements.append({"type": "reponse", "texte": etat["buffer"]})
        etat["buffer"] = ""
    return evenements
# Nouvel outil = ajouter une ligne dans REGISTRE_AFFICHAGE_OUTILS
# (core/registre_outils.py), rien à faire ici -- ni dans le frontend
# (voir api/outils_registre.py, expose ce meme registre en JSON).
NOMS_OUTILS_LISIBLES = {nom: entree["label"] for nom, entree in REGISTRE_AFFICHAGE_OUTILS.items()}


