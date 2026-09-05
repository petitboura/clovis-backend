# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Execution effective d'un ou plusieurs appels d'outils MCP
# (y compris en parallele), extraction des sources et des fichiers
# generes pour l'affichage, et detection des outils sensibles necessitant
# une confirmation utilisateur.
import json
import logging
import re
import concurrent.futures
import requests
from mcp_tools import appeler_outil
from registre_outils import OUTILS_SENSIBLES
from profils_agents import _nom_lisible_appel

class _AttenteConfirmation(Exception):
    """
    Levee des qu'un outil sensible (ecriture) est rencontre, AVANT de
    l'executer. `appel` est l'appel en question ; `appels_restants` sont
    les appels du meme lot qui n'ont pas encore ete traites (ils seront
    rejoues a la reprise, dans l'ordre, apres que celui-ci ait ete
    confirme ou annule).
    """
    def __init__(self, appel, appels_restants):
        self.appel = appel
        self.appels_restants = appels_restants


def _executer_un_appel(appel, table_routage):
    try:
        arguments = json.loads(appel["arguments"] or "{}")
    except Exception:
        arguments = {}
    return appeler_outil(appel["name"], arguments, table_routage)


def _sources_depuis_json_generique(resultat_brut):
    """
    Detection GENERIQUE, independante du nom de l'outil : tout outil qui
    renvoie un JSON de la forme {"results": [{"title"/"titre", "url"}, ...]}
    voit ses sources extraites automatiquement -- couvre tavily_* et
    notion-search aujourd'hui (verifie par appel reel, pas suppose), et
    n'importe quel outil FUTUR qui renverrait la meme forme, sans toucher
    a ce fichier (demande explicite de Bourama, session du 2026-07-26 :
    preparer les citations pour n'importe quelle action/outil a venir,
    pas seulement ceux d'aujourd'hui).

    Best-effort : si le JSON ne correspond pas au format attendu (ou
    n'est pas du JSON), renvoie une liste vide plutot que de faire
    planter la reponse -- les sources sont un bonus, jamais un
    prerequis pour repondre.
    """
    try:
        donnees = json.loads(resultat_brut)
    except (json.JSONDecodeError, TypeError):
        return []

    resultats = donnees.get("results") if isinstance(donnees, dict) else None
    if not isinstance(resultats, list):
        return []

    sources = []
    for r in resultats:
        if isinstance(r, dict) and r.get("url"):
            sources.append({"titre": r.get("title") or r["url"], "url": r["url"]})
    return sources


def _sources_github_depuis_arguments(appel):
    """
    Cas particulier : notre outil GitHub local (core/serveur_mcp_github.py,
    gerer_depot_github, consolidé le 26/08) renvoie du TEXTE brut
    (arborescence ou contenu de fichier), jamais du JSON, la detection
    generique ci-dessus ne peut donc rien y trouver. La source se deduit
    plutot des ARGUMENTS de l'appel (repo/chemin), exactement comme le
    fait l'outil lui-meme pour construire ses requetes API. Si `branche`
    n'a pas ete precisee par le modele, on refait le meme appel
    `default_branch` que l'outil (action "explorer" ou "lire_fichier")
    plutot que de deviner "main", un mauvais lien casse (ex: depot dont
    la branche par defaut est "master") serait pire qu'une source
    absente.
    """
    try:
        arguments = json.loads(appel["arguments"] or "{}")
    except Exception:
        return []

    repo = (arguments.get("repo") or "").strip()
    if not repo:
        return []

    branche = (arguments.get("branche") or "").strip()
    if not branche:
        try:
            info = requests.get(f"https://api.github.com/repos/{repo}", timeout=5)
            branche = info.json().get("default_branch", "main") if info.status_code == 200 else "main"
        except Exception:
            branche = "main"

    if arguments.get("action") == "lire_fichier":
        chemin = (arguments.get("chemin") or "").strip()
        if not chemin:
            return []
        return [{"titre": chemin.split("/")[-1], "url": f"https://github.com/{repo}/blob/{branche}/{chemin}"}]

    # action "explorer"
    chemin_depart = (arguments.get("chemin_depart") or "").strip()
    url = f"https://github.com/{repo}/tree/{branche}/{chemin_depart}".rstrip("/")
    return [{"titre": repo, "url": url}]


def _resultat_pour_affichage(resultat_brut, max_chars=3000):
    """
    Tronque le resultat brut d'un outil pour l'evenement SSE
    "outil_resultat" (2026-07-26, demande Bourama : afficher ce qui a ete
    execute/le resultat pour CHAQUE outil, dans une section dediee avec
    l'icone de l'outil, distincte du raisonnement libre du modele -- voir
    OutilResultatBulle.tsx). Purement un affront de securite d'affichage
    (un depot GitHub explore ou un JSON de recherche peuvent faire
    plusieurs dizaines de Ko) : le contenu COMPLET reste envoye au modele
    via messages_agent, cette troncature ne concerne QUE ce qui est
    montre a la personne.
    """
    if not isinstance(resultat_brut, str):
        resultat_brut = str(resultat_brut)
    if len(resultat_brut) <= max_chars:
        return resultat_brut
    return resultat_brut[:max_chars] + f"\n... (tronqué, {len(resultat_brut)} caractères au total)"


_RE_SOURCE_BIBLIOTHEQUE_LIGNE = re.compile(r"^\(Source : (.+), (https?://\S+), ([^,()]*)\)$")
_RE_PAGE_BIBLIOTHEQUE = re.compile(r"^(.*), page (\d+)(?:-\d+)?$")
_RE_TIMESTAMP_BIBLIOTHEQUE = re.compile(r"^(.*), à (\d{2}):(\d{2})$")


def _sources_bibliotheque_depuis_texte(resultat_brut):
    """
    consulter_bibliotheque (aujourd'hui exposé via gerer_document_
    bibliotheque, action chercher) renvoie du texte
    formaté par bloc -- "{extrait}\\n(Source : {nom}[, page N[-M]|, à
    MM:SS], {url}, {type_mime})", blocs séparés par "\\n\\n---\\n\\n"
    (voir core/bibliotheque_rag.py:formater_source_bibliotheque). Pas de
    JSON ici, donc parsing texte dédié, sur le même principe que le cas
    GitHub ci-dessus.

    Renvoie des sources enrichies de `extrait` (le paragraphe exact
    utilisé), `url_extrait` (URL positionnée -- fragment #page= pour un
    PDF, #t=<secondes> pour un audio ; identique à `url` quand le chunk
    n'a pas de position, càd image/note/lien), `type_mime` (26/08,
    demande Bourama : "tout reste en popup interne" -- le frontend choisit
    le bon visionneur EN APP à partir de ça, sans deviner par extension
    d'URL) et, pour les deux popups ET la citation inline (26/08, retour
    Bourama : "juste des chiffres, on y comprend rien" -- il faut le nom
    du fichier + "page X"/le timestamp affichés en clair, pas un numéro
    nu) :
    - `reperage` : "page N[-M]" ou "à MM:SS", texte prêt à afficher, None
      sinon
    - `position_type`/`position_valeur` : ("page", N) ou ("timestamp",
      secondes), pour que le frontend sache quel visionneur EN APP ouvrir
      (voir VisionneurPositionGlobal.tsx -- on ne compte plus sur le
      fragment d'URL, ignoré par la plupart des lecteurs PDF/audio
      externes une fois le lien ouvert hors de l'app)
    """
    if not isinstance(resultat_brut, str) or "(Source : " not in resultat_brut:
        return []

    sources = []
    for bloc in resultat_brut.split("\n\n---\n\n"):
        lignes = bloc.strip().splitlines()
        if not lignes:
            continue
        m = _RE_SOURCE_BIBLIOTHEQUE_LIGNE.match(lignes[-1].strip())
        if not m:
            continue
        nom_et_reperage, url, type_mime = m.group(1), m.group(2), m.group(3)
        extrait = "\n".join(lignes[:-1]).strip()

        m_page = _RE_PAGE_BIBLIOTHEQUE.match(nom_et_reperage)
        m_ts = _RE_TIMESTAMP_BIBLIOTHEQUE.match(nom_et_reperage)
        reperage = position_type = position_valeur = None
        if m_page:
            nom, page = m_page.group(1), m_page.group(2)
            url_extrait = f"{url}#page={page}"
            reperage = f"page {page}"
            position_type, position_valeur = "page", int(page)
        elif m_ts:
            nom, mm, ss = m_ts.group(1), int(m_ts.group(2)), int(m_ts.group(3))
            url_extrait = f"{url}#t={mm * 60 + ss}"
            reperage = f"à {mm:02d}:{ss:02d}"
            position_type, position_valeur = "timestamp", mm * 60 + ss
        else:
            nom, url_extrait = nom_et_reperage, url

        sources.append({
            "titre": nom,
            "url": url,
            "extrait": extrait,
            "url_extrait": url_extrait,
            "reperage": reperage,
            "position_type": position_type,
            "position_valeur": position_valeur,
            "type_mime": type_mime or None,
        })

    return sources


def _extraire_sources(appel, resultat_brut):
    """
    Construit les sources ({"titre", "url"}) d'un appel d'outil pour
    l'evenement SSE "sources" (citations affichees sous la reponse, voir
    ChatIA.tsx/SourcesBulle.tsx). Trois strategies, dans l'ordre :

    1. Generique par forme de JSON (_sources_depuis_json_generique) --
       future-proof, aucune liste d'outils a maintenir.
    2. Cas particuliers a resultat texte brut, ou la source se deduit des
       arguments de l'appel plutot que du resultat (GitHub aujourd'hui ;
       tout futur outil du meme genre s'ajoute ici au besoin).
    3. Bibliotheque perso/publique (texte brut aussi, mais la source se
       deduit du RESULTAT lui-meme, pas des arguments -- voir
       _sources_bibliotheque_depuis_texte, seul cas avec extrait +
       url_extrait a ce jour).

    Best-effort partout : jamais d'exception qui remonte jusqu'a la
    reponse -- les sources sont un bonus.
    """
    sources = _sources_depuis_json_generique(resultat_brut)
    if sources:
        return sources

    if appel["name"] == "gerer_depot_github":
        try:
            action = json.loads(appel["arguments"] or "{}").get("action")
        except Exception:
            action = None
        if action in ("explorer", "lire_fichier"):
            return _sources_github_depuis_arguments(appel)

    if appel["name"] == "gerer_document_bibliotheque":
        return _sources_bibliotheque_depuis_texte(resultat_brut)

    return []


# Mêmes extensions que EXTENSIONS_FICHIER dans FichierChip.tsx (frontend)
# -- si une extension est ajoutée d'un côté, l'ajouter aussi de l'autre.
EXTENSIONS_FICHIER_GENERE = (
    "pdf", "docx", "doc", "xlsx", "xls", "csv", "pptx", "ppt", "zip", "json",
    "xml", "png", "jpg", "jpeg", "webp", "glb", "tex", "md",
    # 04/09/2026, demande Bourama ("donner"/"donner_catalogue_public" de
    # gerer_document_bibliotheque) : un fichier audio/vidéo de la
    # bibliothèque doit pouvoir sortir en pièce jointe comme n'importe
    # quel autre type -- absents avant car aucun outil ne renvoyait de
    # lien audio/vidéo brut par ce chemin (le cas markdown ![]() dans le
    # texte du modèle passe par LecteurMedia.tsx, pas ici). Ne PAS
    # ajouter côté FichierChip.tsx (EXTENSIONS_FICHIER, frontend) : cette
    # liste-là sert uniquement au rendu markdown, où LecteurMedia.tsx
    # intercepte déjà l'audio/vidéo avant -- le bloc "Fichier(s) généré(s)"
    # de BulleMessage.tsx n'a lui aucune interception, il utilise
    # FichierChip.tsx directement quelle que soit l'extension (repli sur
    # icône générique + libellé "Fichier" pour une extension absente du
    # dict frontend, déjà le cas aujourd'hui pour mp3/mp4).
    "mp3", "wav", "m4a", "ogg", "mp4", "webm",
)
REGEX_FICHIER_GENERE = re.compile(
    r"https?://[^\s<>\"'\)\]]+\.(?:" + "|".join(EXTENSIONS_FICHIER_GENERE) + r")\b",
    re.IGNORECASE,
)


def _extraire_fichiers_generes(resultat_brut):
    """
    Detecte, dans le resultat brut d'un outil, tout lien vers un fichier
    genere -- generique par FORME d'URL (extension connue), pas par nom
    d'outil : aucune liste d'outils de generation a maintenir ici, un
    futur outil de generation est couvert automatiquement du moment que
    son URL se termine par une extension listee ci-dessus (2026-07-28,
    demande Bourama : le lien ne doit plus dependre de la fidelite du
    modele a le recopier correctement dans sa propre reponse -- voir
    evenement SSE "fichiers_generes" emis par _traiter_appels ci-dessous,
    et son rendu cote frontend qui reutilise FichierChip.tsx tel quel).
    Best-effort : jamais d'exception, renvoie [] si rien trouve.
    """
    if not isinstance(resultat_brut, str):
        return []
    vus = set()
    fichiers = []
    for match in REGEX_FICHIER_GENERE.finditer(resultat_brut):
        url = match.group(0)
        if url in vus:
            continue
        vus.add(url)
        fichiers.append({"url": url, "nom": url.rsplit("/", 1)[-1]})
    return fichiers


def _est_outil_sensible(appel):
    """
    True si cet appel doit déclencher la confirmation utilisateur
    (OUTILS_SENSIBLES). Gère deux formats dans OUTILS_SENSIBLES :
    - un nom d'outil seul ("supprimer_programme"), sensible quel que
      soit l'appel.
    - un composite "nom_outil:action" ("gerer_document_bibliotheque:supprimer"),
      sensible seulement si l'argument `action` de CET appel vaut
      exactement cette valeur. Nécessaire depuis la consolidation du
      26/08 (plusieurs outils fusionnés en un seul avec un paramètre
      `action`, dont certaines actions sont sensibles et d'autres non).
    """
    if appel["name"] in OUTILS_SENSIBLES:
        return True
    try:
        arguments = json.loads(appel["arguments"] or "{}")
    except Exception:
        arguments = {}
    action = arguments.get("action")
    return bool(action) and f"{appel['name']}:{action}" in OUTILS_SENSIBLES


def _traiter_appels(appels, messages_agent, table_routage, compteur_sources=None):
    """
    Execute une liste d'appels d'outils, en ajoutant le resultat de chacun
    a messages_agent au fur et a mesure. Des qu'un outil sensible
    (OUTILS_SENSIBLES) est rencontre, s'arrete AVANT de l'executer et leve
    _AttenteConfirmation avec les appels restants (lui inclus).

    Les appels "surs" qui precedent ce premier outil sensible (le cas le
    plus frequent : aucun outil sensible du tout dans le lot) sont
    executes EN PARALLELE plutot qu'un par un, pour ne pas payer en
    latence la somme des temps de reponse de chaque outil alors qu'ils
    sont independants les uns des autres (ex: deux recherches web
    simultanees). On ne parallelise jamais un outil sensible ni ce qui le
    suit : la garantie "on s'arrete avant de l'executer" doit rester
    valable meme dans le lot.

    `compteur_sources` (26/08, citations inline bibliotheque) : boite
    mutable [n] partagee entre TOUS les appels de _traiter_appels pour un
    meme tour (voir _agent_groq, qui la declare une seule fois et la
    passe aux deux appels internes) -- permet de numeroter les sources de
    CHAQUE appel a la suite des precedentes, dans le meme ordre que
    l'evenement SSE "sources" est emis (donc le meme ordre que le
    frontend utilise pour sa propre numerotation globale, voir
    BulleMessage.tsx:sourcesAplaties). Fraiche (commence a 0) si non
    fournie.
    """
    if compteur_sources is None:
        compteur_sources = [0]
    index_sensible = next(
        (i for i, appel in enumerate(appels) if _est_outil_sensible(appel)),
        None,
    )
    appels_surs = appels if index_sensible is None else appels[:index_sensible]

    if appels_surs:
        for appel in appels_surs:
            yield {"type": "statut", "texte": f"{_nom_lisible_appel(appel)}..."}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(appels_surs)) as executor:
            futures = {
                executor.submit(_executer_un_appel, appel, table_routage): appel
                for appel in appels_surs
            }
            for future in concurrent.futures.as_completed(futures):
                appel = futures[future]
                try:
                    resultat = future.result()
                except Exception as e:
                    # CORRECTIF 2026-07-30 (audit UX) : avant, une exception
                    # levee par un outil (ex: generation_video.py/generation_3d.py
                    # si fal.ai change son format de reponse, envoyer_pour_signature
                    # si un signataire est mal forme, etc.) remontait telle
                    # quelle jusqu'a la cascade de secours dans chat(), qui la
                    # traitait comme une panne du modele Groq lui-meme -> bascule
                    # sur un modele de secours SANS AUCUN outil, sans jamais dire
                    # a la personne que sa generation avait echoue. En plus,
                    # messages_agent se retrouvait avec un tool_call sans reponse
                    # correspondante (puisqu'on n'atteignait jamais l'append plus
                    # bas), ce qui faisait aussi echouer les modeles de secours
                    # suivants (API tool-calling stricte sur ce point).
                    #
                    # Desormais : un outil qui echoue est un RESULTAT normal (visible
                    # dans le fil, explique au modele dans le meme tour), jamais une
                    # exception qui remonte. Le modele peut donc reagir dans sa
                    # propre reponse ("la generation a echoue, veux-tu reessayer ?")
                    # au lieu de changer de personnalite en silence.
                    logging.error(f"ERREUR OUTIL ({appel['name']}) : {e}")
                    resultat = f"Erreur : {_nom_lisible_appel(appel)} a échoué ({e})."
                    yield {"type": "statut_termine", "texte": f"{_nom_lisible_appel(appel)} a échoué"}
                    yield {
                        "type": "outil_resultat",
                        "nom_outil": appel["name"],
                        "nom_lisible": _nom_lisible_appel(appel),
                        "resultat": resultat,
                    }
                    messages_agent.append({
                        "role": "tool",
                        "tool_call_id": appel["id"],
                        "content": resultat,
                    })
                    continue
                yield {"type": "statut_termine", "texte": f"{_nom_lisible_appel(appel)} effectuée"}
                # Généralisé (26/07, demande Bourama) : pour N'IMPORTE QUEL
                # outil, présent ou futur -- pas de liste à maintenir, voir
                # docstring de _resultat_pour_affichage.
                yield {
                    "type": "outil_resultat",
                    "nom_outil": appel["name"],
                    "nom_lisible": _nom_lisible_appel(appel),
                    "resultat": _resultat_pour_affichage(resultat),
                }
                # Garanti indépendamment de ce que le modèle écrira ensuite
                # dans sa propre réponse -- voir _extraire_fichiers_generes
                # et le bloc "LIENS" de la page Notion Clovis (le modèle est
                # instruit de ne plus réécrire ce lien lui-même, pour éviter
                # le doublon).
                #
                # CORRECTIF 2026-08-27 (signalé par Bourama) : les sources
                # bibliotheque sont calculees AVANT desormais, pour pouvoir
                # exclure leurs URLs de _extraire_fichiers_generes. Sans ca,
                # un document de bibliotheque cite en source (ex: un PDF)
                # se faisait detecter UNE DEUXIEME FOIS par la regex
                # generique (n'importe quelle URL en .pdf/.docx/etc, pensee
                # pour les fichiers CREES par un outil de generation) et
                # s'affichait en double dans "Fichier genere" : mal nomme
                # (fin brute de l'URL de stockage au lieu du vrai nom du
                # document, deja connu par _extraire_sources), et avec un
                # repli qui pouvait faire quitter l'appli (voir
                # telechargerFichier/FichierChip.tsx cote frontend) alors
                # que la source elle-meme (SourcesBulle.tsx) est deja
                # cliquable et correctement nommee.
                sources = _extraire_sources(appel, resultat)
                urls_deja_sourcees = {s["url"] for s in sources}
                fichiers_generes = [
                    f for f in _extraire_fichiers_generes(resultat)
                    if f["url"] not in urls_deja_sourcees
                ]
                if fichiers_generes:
                    yield {
                        "type": "fichiers_generes",
                        "nom_outil": appel["name"],
                        "fichiers": fichiers_generes,
                    }
                contenu_pour_modele = resultat
                if sources:
                    debut_numero = compteur_sources[0] + 1
                    compteur_sources[0] += len(sources)
                    # Chaque source porte desormais son propre `numero`
                    # (2026-09-02, demande Bourama : le frontend ne doit
                    # plus RETROUVER une source par sa position dans un
                    # tableau -- source de bugs de resolution -- mais la
                    # recevoir avec son numero deja attache, cle fiable
                    # meme si le frontend deduplique/reordonne ensuite).
                    for n, s in zip(range(debut_numero, compteur_sources[0] + 1), sources):
                        s["numero"] = n
                    yield {"type": "sources", "sources": sources}
                    # 2026-09-02 (demande Bourama) : citation inline [[n]]
                    # au fil du texte desactivee -- on ne dit plus au
                    # modele d'inserer de marqueur. La liste de sources en
                    # bas (evenement SSE "sources" ci-dessus, avec le
                    # `numero` deja attache a chaque source) reste
                    # inchangee et continue de s'afficher normalement.
                    # Vaut pour TOUT outil sourcé (bibliotheque,
                    # tavily_*/notion-search via _sources_depuis_json_generique,
                    # gerer_depot_github via _sources_github_depuis_arguments,
                    # et tout futur outil sourcé sans changement ici).
                messages_agent.append({
                    "role": "tool",
                    "tool_call_id": appel["id"],
                    "content": contenu_pour_modele,
                })

    if index_sensible is not None:
        raise _AttenteConfirmation(appels[index_sensible], appels[index_sensible + 1:])


