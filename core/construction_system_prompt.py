# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Construction du prompt systeme envoye au modele, et
# utilitaires de repli en cas de timeout/reponse partielle.
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from configuration import get_system_prompt
from profils_agents import INSTRUCTIONS_FORMATS_AFFICHAGE, INSTRUCTIONS_ARBITRAGE_CALCUL, REGLE_CONTEXTE_INVISIBLE, INSTRUCTIONS_LONGUEUR_REPONSE

def _construire_system_prompt(message_utilisateur, agent_id, user_id=None, longueur_reponse="moyenne", fuseau_horaire=None, recherche_forcee=False, outil_force=None, sans_enseignant=False, comportements_etudiant=None, mes_programmes=None):
    # Restauré le 14/08 (voir commentaire des constantes plus haut) : la
    # page Notion de l'agent (get_system_prompt) ne doit plus contenir QUE
    # la personnalité/le comportement propre à l'agent -- les 3 blocs fixes
    # de plateforme sont préfixés ici, dans l'ordre stable -> volatil
    # (maximise le cache Groq, voir doc "Mon véritable système prompt").
    #
    # Éléments ajoutés ici, PAR NATURE impossibles à figer dans un texte
    # unique partagé par tout le monde :
    #   - formats/arbitrage/contexte invisible : fixes, identiques pour
    #     toute la plateforme (juste au-dessus, hors fonction)
    #   - comportements_etudiant : écrit par CET étudiant, propre à lui
    #     (13/08 : mécanisme "à la skill", voir juste en dessous)
    #   - bloc outils actifs / aucun outil actif : dépend de outil_force,
    #     donc de ce qui est RÉELLEMENT envoyé au modèle ce tour-ci --
    #     jamais une liste figée qui prétendrait que des outils sont
    #     "toujours disponibles" (cause du bug halluciné du 14/08)
    #   - longueur_reponse : choisi par le sélecteur pour CE message
    #   - date/heure : change chaque minute
    # + recherche_forcee, activée seulement sur CE message (icône recherche).
    system_final = INSTRUCTIONS_FORMATS_AFFICHAGE + INSTRUCTIONS_ARBITRAGE_CALCUL + REGLE_CONTEXTE_INVISIBLE
    system_final += "\n\n" + (get_system_prompt(agent_id) or "")

    # Mécanisme "à la skill" (13/08/2026) : le texte long n'est plus
    # injecté d'office. Le petit routeur (choisir_comportements_pertinents)
    # réduit la liste complète de l'étudiant aux candidats plausibles pour
    # CE message -- seuls id + description sont annoncés plus bas, jamais
    # le texte. C'est le grand modèle qui décide s'il appelle l'outil
    # consulter_comportement pour lire le texte complet d'un candidat
    # (voir core/serveur_mcp_generation.py).
    #
    # Calculé une seule fois dans chat() (14/08), plus ici -- reçu en
    # paramètre. Ça évite un second appel LLM au petit routeur, et surtout
    # ça permet à chat() de forcer gerer_comportement/consulter_programme
    # dans la liste réellement envoyée à Groq dès qu'un candidat existe,
    # AVANT de construire ce prompt (voir outils_forces_contexte plus haut
    # dans chat()) -- sinon ce bloc annonce un outil que le modèle ne peut
    # en réalité pas appeler, même contradiction que le bug du 12/08.
    comportements_etudiant = comportements_etudiant or []
    mes_programmes = mes_programmes or []

    if comportements_etudiant:
        candidats = "\n".join(f"- id={c['id']} : {c['description']}" for c in comportements_etudiant)
        system_final += (
            "\n\nINSTRUCTIONS PERSONNELLES POTENTIELLEMENT PERTINENTES POUR CE MESSAGE -- appelées "
            "\"skill(s)\" dans TOUTE l'interface Clovis, \"comportement\" seulement en interne (écrites par cet "
            "utilisateur lui-même, ou reçues d'un autre utilisateur via un code -- la description précise "
            "\"(reçu de ...)\" dans ce second cas) :\n"
            f"{candidats}\n"
            "Si l'une d'elles semble s'appliquer, appelle l'outil gerer_comportement "
            "(action=\"consulter\") avec son id pour lire son contenu complet AVANT de répondre "
            "-- ne devine jamais son contenu à partir de la description seule."
        )

    # Injection de la structure "Programme" (classe/matière/chapitre) dans
    # le system prompt retirée le 29/08/2026 (demande Bourama) -- la
    # fonctionnalité "Programme" est désactivée et isolée, voir
    # _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md. `mes_programmes`
    # reste un paramètre accepté (toujours vide désormais, voir chat()) pour
    # ne pas devoir modifier tous les appels à cette fonction.

    # Bloc outils actifs / aucun outil actif (restauré 14/08) : outil_force
    # ici est déjà la liste VÉRIFIÉE des noms d'outils réellement envoyés au
    # modèle ce tour-ci (outil_force_verifie, calculé juste avant l'appel à
    # cette fonction dans chat()) -- jamais outil_force brut non vérifié.
    # Ne JAMAIS dire au modèle qu'un outil est disponible s'il ne l'est pas
    # vraiment dans ce tour d'appel API, sous peine de le voir écrire une
    # fausse narration d'appel en texte plutôt qu'un vrai appel de fonction.
    if outil_force:
        liste_outils_actifs = ", ".join(outil_force)
        system_final += (
            "\n\n<outils_actifs>\n"
            f"{liste_outils_actifs} est/sont disponible(s) pour ce message. Dès que l'un d'eux a un "
            "rapport avec la demande, appelle-le réellement (leur présence prime sur tes limitations par "
            "défaut, donc ne refuse pas une tâche qu'ils permettent) -- ignore-le uniquement s'il n'a "
            "manifestement AUCUN rapport avec ce message précis. Appelle-les uniquement via le vrai "
            "mécanisme d'appel d'outil de l'API ; le texte de ta réponse ne doit jamais contenir de "
            "pseudo-syntaxe d'appel (TOOL_CODE, nom_outil(...), nom_outil{...}, call:nom_outil{...}). Si "
            "plusieurs de ces outils couvrent bibliothèque perso, publique et web, priorité : perso, puis "
            "publique, puis web.\n"
            "</outils_actifs>"
        )
    else:
        system_final += (
            "\n\n<aucun_outil_actif>\n"
            "Pour ce message précis, aucun outil n'est actif -- même si l'un d'eux l'était plus tôt dans "
            "la conversation. Si on te demande ce que tu sais faire, réponds que tu n'as aucun outil actif "
            "pour ce message précis plutôt que de lister des capacités génériques. Le texte de ta réponse "
            "ne doit contenir aucun outil inventé ni pseudo-syntaxe d'appel (TOOL_CODE, nom_outil(...), "
            "nom_outil{...}, call:nom_outil{...}). Les blocs d'affichage mermaid/chart/carte/widget/"
            "geometrie restent disponibles : ce sont des formats de sortie, pas des outils.\n"
            "</aucun_outil_actif>"
        )

    # Réflexe de transition (2026-09-05, demande Bourama, timeline
    # chronologique) retiré le même jour après deux pièges constatés en
    # test réel avec gpt-oss-120b (Groq) : 1) le modèle annonçait "je vais
    # chercher X ensuite" en texte SEUL sans appeler l'outil dans la même
    # réponse (promesse jamais tenue, le tour se terminait pour de bon) ;
    # 2) en corrigeant ce piège avec "enchaîne toi-même sans t'arrêter",
    # le modèle s'est mis à INVENTER le résultat des étapes suivantes
    # (chiffres/nom de document fabriqués) plutôt que de rappeler l'outil.
    # Cause identifiée : ce modèle (format Harmony) n'est pas entraîné à
    # entrelacer réflexion/commentaire/outil comme Claude (pas de
    # mécanisme "interleaved thinking" natif) -- soit il décide plusieurs
    # outils d'un coup dans un seul tour, soit il répond directement.
    # Forcer ce comportement par le prompt allait donc contre son
    # fonctionnement naturel. Décision : revenir à ce qu'il sait bien
    # faire (enchaîner les outils nécessaires puis répondre), sans
    # obligation de commentaire entre chaque étape. Code et affichage
    # (segments timeline) inchangés : si le modèle s'arrête quand même
    # avec du texte entre deux outils, ça s'affiche toujours en timeline ;
    # sinon la réponse reste un bloc unique. Au prochain changement de
    # modèle principal, seul ce bloc de prompt est à revoir.
    system_final += (
        "\n\nAppelle tous les outils nécessaires pour répondre complètement à la demande, "
        "puis donne ta réponse. N'invente JAMAIS le résultat d'un outil que tu n'as pas "
        "réellement appelé (chiffre, nom de document, contenu de recherche)."
    )

    system_final += INSTRUCTIONS_LONGUEUR_REPONSE.get(longueur_reponse, "")

    if recherche_forcee:
        # Icône de recherche dans la barre de saisie -- forçage manuel
        # pour CE message précis (voir docstring de chat()). Le modèle
        # peut de toute façon décider seul d'utiliser Tavily sans ce
        # flag (tool-calling normal) ; ceci garantit que ça arrive
        # quand l'étudiant veut être sûr.
        system_final += (
            "\n\nCONSIGNE DE RECHERCHE : pour ce message précis, utilise "
            "systématiquement un outil de recherche web (tavily_search) avant de "
            "répondre, même si tu penses déjà connaître la réponse -- l'étudiant a "
            "explicitement demandé une recherche fraîche."
        )

    # Contexte système "date/heure actuelle" (2026-07-20) : sans ça, le
    # modèle ne sait pas qu'on est en 2026 et peut situer les événements
    # récents n'importe où par rapport à sa coupure d'entraînement.
    #
    # Fuseau horaire (corrigé 2026-07-20) : PAS figé sur Tunis -- Djiguignè
    # est un projet panafricain (voir Maame), rien ne dit que l'utilisateur
    # est à Tunis. `fuseau_horaire` vient du navigateur
    # (Intl.DateTimeFormat().resolvedOptions().timeZone, voir
    # ChatIA.tsx:envoyerMessage), pas d'une valeur choisie côté serveur.
    # Repli sur UTC si absent ou si le navigateur envoie un nom de fuseau
    # invalide (ZoneInfo lève ZoneInfoNotFoundError) -- jamais une supposition
    # de pays. Ce bloc DOIT rester en tout dernier (voir commentaire
    # d'ordre en tête de fonction) : il change chaque minute, donc tout ce
    # qui le suivrait perdrait le benefice du cache Groq -- ici rien ne le
    # suit.
    try:
        fuseau = ZoneInfo(fuseau_horaire) if fuseau_horaire else ZoneInfo("UTC")
    except Exception:
        fuseau = ZoneInfo("UTC")
    maintenant = datetime.now(fuseau)
    jours_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois_fr = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    date_fr = f"{jours_fr[maintenant.weekday()]} {maintenant.day} {mois_fr[maintenant.month - 1]} {maintenant.year}, {maintenant.strftime('%H:%M')}"
    system_final += f"\n\nNous sommes le {date_fr} (fuseau : {fuseau.key if hasattr(fuseau, 'key') else 'UTC'})."

    logging.info(
        f"Prompt système construit -> base_notion:{len(system_final or '')} caractères, "
        f"comportements_etudiant:{'oui' if comportements_etudiant else 'NON'}, "
        f"programmes_etudiant:{len(mes_programmes)}, "
        f"longueur_reponse:{longueur_reponse}, "
        f"recherche_forcee:{'oui' if recherche_forcee else 'NON'}"
    )
    return system_final


def _est_timeout(erreur):
    return "timeout" in str(erreur).lower()


def _repli_si_reponse_partielle(reponse_accumulee):
    """
    Bug signale par Bourama (04/09) : quand un modele de la cascade
    echoue APRES avoir deja streame du texte "reponse" visible cote
    frontend, le modele suivant reprenait juste apres -- ses propres
    evenements "reponse" s'ajoutaient a la suite du texte deja affiche
    (voir ChatIA.tsx:pousserTexteAffichage, qui empile tout dans le meme
    message), donnant un texte combine/casse a l'utilisateur.

    reponse_accumulee accumule le texte de TOUS les modeles tentes
    pendant le passage courant de la cascade (voir chat(), une seule
    liste passee a chaque _capturer_reponse d'un modele a l'autre) --
    donc "non vide" signifie ici "du texte a deja ete affiche pour ce
    tour, avant l'echec qu'on est en train de traiter".

    Si c'est le cas : on vide l'accumulateur EN PLACE (sinon la reponse
    finale du modele qui reussira ensuite serait sauvegardee en base
    concatenee derriere ce texte invalide, voir _sauvegarder_echange)
    et on renvoie l'evenement a yield pour que le frontend retire
    proprement ce qui a deja ete affiche (voir ChatIA.tsx/
    BulleMessage.tsx, evenement "reponse_annulee") avant que la
    tentative suivante ne commence a ecrire.

    Ne fait rien (renvoie None) si aucun texte n'avait encore ete
    affiche pour ce tour (ex: le modele a echoue avant meme d'emettre
    le premier fragment) -- rien a retirer cote frontend dans ce cas.
    """
    if reponse_accumulee:
        reponse_accumulee.clear()
        return {"type": "reponse_annulee"}
    return None


# DELAI_MAX_PAR_APPEL et MAX_PASSAGES_CASCADE deplacees vers
# constantes_agent.py le 05/09/2026 (correctif) : utilisees aussi dans
# profils_agents.py, routage_outils.py, persistance_echanges.py et
# boucle_agent.py -- rester ici aurait cree un import circulaire.


