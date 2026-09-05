import json
import logging
import base64
import concurrent.futures
from groq import Groq
from google import genai
from google.genai import types
from comportements_etudiants import (
    lister_comportements as lister_comportements_etudiant,
    choisir_comportements_pertinents,
    separer_comportements_par_niveau,
    lister_comportements_chapitres_pour_matiere,
)
# Fonctionnalité "Programme" désactivée et isolée le 29/08/2026 (demande
# Bourama) -- voir _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md.
# Anciens imports (ne jamais réactiver) :
#   from programme_llm import lister_mes_programmes_legers
#   from codes_partage import lister_programmes_recus_legers
from codes_partage import lister_comportements_recus
from mcp_tools import lister_tous_les_outils, lister_outils_autorises_pour_agent, appeler_outil
from fournisseurs_llm import generer_reponse_premium

# 05/09/2026 (demande Bourama) : main.py decoupe en plusieurs fichiers pour
# ne plus avoir un seul fichier de presque 4000 lignes -- voir
# constantes_agent.py, moderation_message.py, filtre_texte_streaming.py,
# lecture_urls_externes.py, profils_agents.py, routage_outils.py,
# construction_system_prompt.py, persistance_echanges.py, execution_outils.py
# et boucle_agent.py. Il ne reste ici que la fonction publique chat() et les
# imports qui la font tenir ensemble -- aucun changement de comportement.
from constantes_agent import (
    AGENT_ID_PAR_DEFAUT, GOOGLE_MODEL, GROQ_FALLBACKS, GROQ_PRIMARY,
    MESSAGE_CONTENU_BLOQUE, MESSAGE_ERREUR, MODELES_AVEC_REASONING_EFFORT,
    MODELES_QUALITE_REDUITE, MODERATION_ENTREE_ACTIVE, get_secret, supabase,
    MAX_PASSAGES_CASCADE,
)
from moderation_message import _verifier_message_utilisateur
from filtre_texte_streaming import _ressemble_a_du_json_casse  # réexporté pour core/proactivite.py (05/09/2026)
from lecture_urls_externes import _construire_parts_gemini, _enrichir_message_avec_urls, _telecharger_image
from profils_agents import _mettre_a_jour_profil_utilisateur_si_besoin, _nom_agent, _nom_lisible, _nom_lisible_appel
from routage_outils import _ecrire_outils_retenus, _lire_outils_retenus, _outil_garder_outils, _router_outils
from construction_system_prompt import _construire_system_prompt, _est_timeout, _repli_si_reponse_partielle
from persistance_echanges import _sauvegarder_echange, _mettre_a_jour_resume_si_besoin
from execution_outils import _resultat_pour_affichage
from boucle_agent import _agent_groq, _capturer_reponse

logging.basicConfig(level=logging.INFO)

# (13/08) Commentaire ajoute pour forcer Railway a rebuild depuis ce commit --
# le deploiement precedent avait reutilise une image en cache identique a
# celle d'avant le fix du NameError resume_memoire dans
# _construire_system_prompt (voir commit d565000), donc le bug restait
# present en prod malgre le fix deja present sur main.


def chat(message_utilisateur=None, historique=None, user_id=None, reprise=None, agent_id=None, conversation_id=None, longueur_reponse="moyenne", image_url=None, localisation=None, fuseau_horaire=None, images_base64=None, recherche_forcee=False, outil_force=None, ignorer_suggestion_outils=False, modele_force=None, sans_enseignant=False, natif=False):
    """
    Generateur d'evenements. Chaque element produit est un dictionnaire :
    - {"type": "statut", "texte": "..."}         -> un outil MCP est en cours d'utilisation
    - {"type": "statut_termine", "texte": "..."} -> cet outil a fini (ou a ete annule)
    - {"type": "outil_resultat", "nom_outil": "...", "nom_lisible": "...", "resultat": "..."}
      -> ce que CET outil a concretement execute/retourne (tronque a 3000 caracteres pour
      l'affichage, voir _resultat_pour_affichage -- le contenu complet reste envoye au
      modele separement). Generalise a tout outil, present ou futur (26/07) : distinct du
      raisonnement libre du modele, qui lui peut paraphraser/melanger ce contenu avec
      d'autres reflexions dans son propre texte -- voir OutilResultatBulle.tsx cote frontend.
    - {"type": "raisonnement", "texte": "..."}   -> fragment de raisonnement interne du modele, avant la reponse finale (modeles de MODELES_AVEC_REASONING_EFFORT uniquement)
    - {"type": "sources", "sources": [{"titre": "...", "url": "..."}]} -> resultats d'une
      recherche web (Tavily) utilisee pour repondre. Peut etre emis plusieurs fois dans le
      meme echange (plusieurs recherches) -- l'appelant accumule/fusionne, ne remplace pas.
    - {"type": "reponse", "texte": "..."}        -> morceau de la reponse finale (streaming)
    - {"type": "fichiers_generes", "nom_outil": "...", "fichiers": [{"url": "...", "nom": "..."}]}
      -> (28/07) emis des qu'un outil produit un fichier telechargeable (detecte par
      extension d'URL, voir _extraire_fichiers_generes) -- INDEPENDANT de ce que le
      modele ecrit dans sa reponse texte, garanti a chaque fois. Le frontend l'affiche
      en carte fichier (FichierChip.tsx) a la fin du message assistant.
    - {"type": "outils_suggeres", "outils": ["nom_outil", ...]} -> routeur d'outils (28/07,
      _router_outils) : DERNIER evenement de l'echange (rien n'est sauvegarde, aucune
      reponse n'est generee ce tour-ci). Emis a la place d'une reponse quand aucun
      outil n'est deja force (ni menu manuel, ni suggestion precedente) ET que le
      routeur juge au moins un outil pertinent. Le frontend affiche un bouton par
      outil ; un clic renvoie la MEME question avec ce nom dans outil_force, exactement
      comme une selection manuelle -- voir BarreDeSaisie.tsx / ChatIA.tsx.
    - {"type": "confirmation_requise", ...}      -> un outil qui MODIFIE les donnees de
      l'utilisateur (ex: creer une page Notion) attend une confirmation avant de s'executer.
      Contient "nom_lisible", "arguments" (a afficher a l'utilisateur), et "etat_reprise"
      (a repasser tel quel a chat(reprise=...) une fois la decision prise).
    - {"type": "limite_outils_atteinte", "etat_reprise": {...}}  -> (02/09/2026) le budget
      dynamique d'aller-retours "outil" a atteint son plafond_absolu (voir parametres_outils()
      dans mcp_tools.py) sans que le modele ait pu conclure. Une VRAIE reponse texte a quand
      meme ete generee juste avant cet evenement (le modele explique lui-meme qu'il a atteint
      sa limite) : le frontend affiche un bouton "Continuer" sous CE message, qui rappelle
      chat(reprise={"etat_reprise": evenement["etat_reprise"], "type": "continuer_agent"})
      pour reprendre avec un budget neuf, sans rien reexecuter (messages_agent garde tous les
      resultats d'outils deja obtenus).
    - {"type": "repetition_detectee", "etat_reprise": {...}}  -> (02/09/2026) le meme appel
      d'outil (nom + arguments identiques) a ete tente plusieurs fois d'affilee (voir
      tolerance_repetition dans parametres_outils()) : signe d'une vraie boucle, l'appel N'A
      PAS ete execute. Une reponse texte explique le blocage, puis cet evenement porte
      l'etat pour un bouton "Reessayer" -- meme mecanisme de reprise que limite_outils_atteinte
      (chat(reprise={"etat_reprise": ..., "type": "continuer_agent"})).
      `message_utilisateur` (optionnel) peut accompagner cette reprise : le bouton
      Continuer/Reessayer envoie ce chemin SANS texte (equivaut a "continue"), mais si
      l'utilisateur tape autre chose a la place (ajustement, "j'arrete"...), ce texte doit
      passer par CE MEME chemin plutot que par un nouveau message normal, pour que le modele
      garde tout le contexte deja accumule (voir chat.py cote frontend, reprendreAgent()).
    - {"type": "meta", "message_id_user": ..., "message_id_assistant": ...,
      "created_at_assistant": ...}                -> DERNIER evenement emis, une fois
      l'echange persiste dans historique_conversations (voir _sauvegarder_echange).
      Ids necessaires cote appelant (API du frontend Next.js) pour indexer un
      feedback like/dislike sur CE message precis. Absent si l'utilisateur n'est pas
      connecte (user_id=None) : dans ce cas aucun feedback n'est possible non plus.

    Le frontend doit distinguer ces types pour savoir quoi afficher, et ne
    garder que "reponse" dans l'historique de conversation.

    `longueur_reponse` (optionnel, "courte" | "moyenne" | "longue", defaut
    "moyenne" = comportement historique inchange) pilote la longueur de la
    reponse generee via une consigne ajoutee au prompt systeme (voir
    INSTRUCTIONS_LONGUEUR_REPONSE). Migration Next.js, section 3.3 :
    modifiable a chaque message par l'utilisateur.

    `user_id` (session.user.id de Supabase Auth, ou None si l'utilisateur n'est
    pas connecte) est transmis au registre d'outils pour que les outils "par
    utilisateur" (ex: Notion) sachent pour qui aller chercher un token. Il sert
    aussi a scoper la memoire long-terme (conversation_summaries, scope par
    user_id seul depuis le compte unifie de juillet 2026 -> le resume suit
    l'utilisateur d'un agent a l'autre, pas cloisonne par agent) : sans user_id
    (utilisateur non connecte), rien n'est lu ni ecrit en memoire.

    `agent_id` (optionnel) determine quel prompt systeme et quelles donnees
    RAG utiliser (voir configuration.py / retriever.py). Si non fourni, on
    utilise le secret AGENT_ID du deploiement, puis AGENT_ID_PAR_DEFAUT.

    `conversation_id` (optionnel, 2026-07-13) identifie le fil de
    discussion affiche dans la sidebar de chat.py (liste de conversations
    distinctes et cliquables, façon Claude.ai) -- genere cote chat.py, une
    valeur par conversation, pas par message. Simplement transmis a
    _sauvegarder_echange(). None accepte : un appelant qui ne gere pas
    encore les fils continue de fonctionner normalement.

    Pour reprendre apres une confirmation_requise, appeler :
        chat(reprise={"etat_reprise": evenement["etat_reprise"], "approuve": True|False})
    (message_utilisateur/historique/user_id sont alors ignores.)
    LIMITE CONNUE : la memoire long-terme n'est PAS persistee sur ce chemin de
    reprise (etat_reprise ne transporte ni agent_id, ni user_id, ni le message
    utilisateur d'origine, ni conversation_id). A etendre si besoin en les
    ajoutant a etat_reprise dans _evenement_confirmation.

    `image_url` (optionnel, 2026-07-20) : URL publique d'une image jointe au
    message (voir api/uploads.py:uploader_image_chat). Si presente, on ne
    passe PAS par le cascade Groq habituel (aucun des modeles Groq de
    GROQ_PRIMARY/GROQ_FALLBACKS n'est multimodal) : on route directement et
    uniquement vers Gemini, seul modele vision de la cascade. Consequence
    connue : pas d'outils MCP (Notion, Wolfram, recherche web) sur un
    message avec image, comme pour le fallback Gemini texte plus bas. Si
    Gemini echoue sur ce chemin, on renvoie MESSAGE_ERREUR direct (pas de
    retry cascade complet comme pour le texte : un seul modele disponible).

    `localisation` (optionnel, 2026-07-20) : dict {"latitude":..., "longitude":...}
    transmis explicitement par l'utilisateur (bouton dedie, jamais automatique).
    Injecte en fin de prompt systeme, jamais traite comme un fait dit par
    l'utilisateur. N'affecte ni le cascade ni le choix de modele.

    `fuseau_horaire` (optionnel, 2026-07-20) : nom de fuseau IANA lu depuis
    le navigateur (Intl.DateTimeFormat().resolvedOptions().timeZone, voir
    ChatIA.tsx:envoyerMessage). PAS de fuseau fixe côté serveur -- Djiguignè
    est panafricain, aucune hypothèse de pays. Repli sur UTC si absent ou
    invalide.

    `images_base64` (optionnel, 2026-07-20) : liste de frames JPEG en
    base64, extraites d'une vidéo uploadée (voir
    api/uploads.py:uploader_video_chat et core/video.py:_extraire_frames_video).
    Combinable avec image_url (rare en pratique) -- toutes les images sont
    envoyées à Gemini dans le MÊME message. Le son de la vidéo n'est PAS
    envoyé ici : il est transcrit à part (Whisper) et injecté comme texte
    dans message_utilisateur par le frontend, avant l'appel à chat().

    `recherche_forcee` (optionnel, 2026-07-23, defaut False) : icône de
    recherche dans la barre de saisie (djiguigne-frontend) -- force une
    consigne de recherche web systematique pour CE message. Le modele
    peut de toute facon decider seul d'utiliser Tavily sans ce flag
    (tool-calling normal, des lors que le serveur "tavily" est active
    pour l'agent) ; ce parametre garantit juste que ca arrive.

    Liens colles dans message_utilisateur (page web ou video YouTube) :
    recuperes automatiquement (_enrichir_message_avec_urls) et ajoutes en
    contexte APRES le message original avant envoi au modele. Le message
    BRUT (sans ce contenu) reste ce qui est sauvegarde dans l'historique.

    Si TOUS les maillons de la cascade (Groq principal, Gemini, fallbacks
    Groq) echouent uniquement a cause d'un timeout, on retente une seconde
    fois toute la cascade. Si au moins une erreur n'est pas un timeout (ex:
    429, cle invalide...), on ne retente pas et on part direct sur le
    message d'erreur.

    `modele_force` (optionnel, 02/08/2026, voir core/fournisseurs_llm.py) :
    modele_id premium (Claude/GPT/Gemini/DeepSeek) choisi par l'utilisateur
    pour CE message, deja revalide cote appelant (api/chat.py) contre les
    modeles reellement debloques de l'agent -- ce module ne refait PAS
    cette verification, il fait confiance a l'appelant. Ignore si un
    image_url/images_base64 est present (la vision reste reservee au
    chemin Gemini existant plus bas). LIMITE CONNUE : ce chemin ne passe
    PAS par le cascade Groq ni par les outils MCP (pas de RAG, Wolfram,
    Notion, recherche web...) -- reponse texte seule, comme le chemin
    vision Gemini juste en dessous. A etendre en v2 si le tool-calling
    multi-fournisseurs est prioritaire.
    """
    if reprise is not None and reprise.get("type") == "continuer_agent":
        # Reprise apres "limite_outils_atteinte" ou "repetition_detectee"
        # (02/09/2026) : pas d'appel en attente ici (contrairement a la
        # reprise apres confirmation juste en dessous) -- on relance
        # directement _agent_groq avec un budget neuf a partir de l'etat
        # garde par _evenement_reprise_agent. messages_agent contient deja
        # tous les resultats d'outils obtenus jusque-la : rien n'est
        # reexecute, rien n'est perdu.
        #
        # `message_utilisateur` optionnel (02/09/2026, correction demandee
        # par Bourama) : le bouton "Continuer" n'est qu'un raccourci pour
        # eviter a l'utilisateur de taper ce mot -- si a la place il tape
        # un ajustement ou "j'arrete", ce texte doit arriver DANS ce meme
        # contexte complet (avec tous les resultats d'outils deja
        # obtenus), pas repartir sur une conversation vierge. C'est au
        # modele de decider quoi en faire (continuer, s'arreter,
        # s'ajuster), pas au frontend de trancher en amont.
        etat = reprise["etat_reprise"]
        messages_agent = etat["messages_agent"]
        outils_mcp = etat["outils_mcp"]
        table_routage = etat["table_routage"]
        modele_reprise = etat.get("modele", GROQ_PRIMARY)
        reasoning_effort_reprise = etat.get("reasoning_effort")

        if reprise.get("message_utilisateur"):
            messages_agent.append({"role": "user", "content": reprise["message_utilisateur"]})

        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
        try:
            yield from _agent_groq(
                client_groq, messages_agent, outils_mcp, table_routage,
                modele=modele_reprise, reasoning_effort=reasoning_effort_reprise,
                agent_nom=etat.get("agent_nom"), conversation_id=conversation_id,
            )
        except Exception as e:
            logging.error(f"ERREUR GROQ (reprise apres limite/répétition) {modele_reprise}: {e}")
            yield {"type": "reponse", "texte": MESSAGE_ERREUR}
        return

    if reprise is not None:
        etat = reprise["etat_reprise"]
        approuve = reprise["approuve"]
        messages_agent = etat["messages_agent"]
        outils_mcp = etat["outils_mcp"]
        table_routage = etat["table_routage"]
        appel = etat["appel"]
        modele_reprise = etat.get("modele", GROQ_PRIMARY)
        reasoning_effort_reprise = etat.get("reasoning_effort")

        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)

        if approuve:
            yield {"type": "statut", "texte": f"{_nom_lisible_appel(appel)}..."}
            try:
                arguments = json.loads(appel["arguments"] or "{}")
            except Exception:
                arguments = {}
            # CORRECTIF 2026-07-30 (audit UX, même principe que
            # _traiter_appels ci-dessus) : cet appel n'était protégé par
            # AUCUN try/except -- une exception ici (ex: token GitHub
            # invalide/expiré, panne réseau) faisait planter tout le flux
            # sans passer ni par la cascade de secours, ni par MESSAGE_ERREUR :
            # rien n'était renvoyé à la personne, pas même une erreur générique.
            deja_ajoute_a_messages_agent = False
            try:
                resultat = appeler_outil(appel["name"], arguments, table_routage)
            except Exception as e:
                logging.error(f"ERREUR OUTIL APPROUVÉ ({appel['name']}) : {e}")
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
                deja_ajoute_a_messages_agent = True
            if not deja_ajoute_a_messages_agent:
                yield {"type": "statut_termine", "texte": f"{_nom_lisible_appel(appel)} effectuée"}
                yield {
                    "type": "outil_resultat",
                    "nom_outil": appel["name"],
                    "nom_lisible": _nom_lisible_appel(appel),
                    "resultat": _resultat_pour_affichage(resultat),
                }
        else:
            resultat = "Action annulée par l'utilisateur : cet outil n'a pas été exécuté."
            yield {"type": "statut_termine", "texte": f"{_nom_lisible_appel(appel)} annulée"}
            deja_ajoute_a_messages_agent = False

        if not deja_ajoute_a_messages_agent:
            messages_agent.append({
                "role": "tool",
                "tool_call_id": appel["id"],
                "content": resultat,
            })

        try:
            yield from _agent_groq(
                client_groq, messages_agent, outils_mcp, table_routage,
                appels_en_cours_a_finir=etat.get("appels_restants") or None,
                modele=modele_reprise, reasoning_effort=reasoning_effort_reprise,
                agent_nom=etat.get("agent_nom"), conversation_id=conversation_id,
            )
        except Exception as e:
            logging.error(f"ERREUR GROQ (reprise apres confirmation) {modele_reprise}: {e}")
            yield {"type": "reponse", "texte": MESSAGE_ERREUR}
        return

    # --- Chemin normal : nouvelle question --------------------------------
    if historique is None:
        historique = []

    # Perf (10/08, demande Bourama : "dis-moi tout ce qui se passe") : la
    # modération d'entrée (juste en dessous) est elle-même un appel LLM
    # complet -- un modèle DE RAISONNEMENT (gpt-oss-safeguard-20b), pas un
    # petit modèle instantané comme les routeurs -- et jusqu'ici
    # BLOQUANTE avant absolument tout le reste (routeur d'outils, RAG,
    # prompt système...). Elle démarre maintenant en tâche de fond ICI,
    # en parallèle de tout ce qui suit, et son résultat n'est vérifié
    # qu'aux deux points où du contenu serait effectivement révélé
    # (marqués PERF plus bas) -- jamais avant. Aucun changement de
    # comportement de sécurité : un message toujours détecté comme non
    # sûr est toujours bloqué avant toute réponse, juste sans avoir
    # attendu son tour en premier.
    f_moderation = None
    if MODERATION_ENTREE_ACTIVE and message_utilisateur:
        f_moderation_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        f_moderation = f_moderation_executor.submit(_verifier_message_utilisateur, message_utilisateur)

    # Modération d'entrée (25/07, remplacée Llama Guard -> gpt-oss-safeguard,
    # voir _verifier_message_utilisateur plus haut) : lancée en tâche de
    # fond juste au-dessus (perf 10/08), plus décrite ici comme un simple
    # bloc synchrone -- son résultat est vérifié plus bas, aux deux points
    # marqués PERF, jamais avant qu'une réponse ne soit sur le point d'être
    # révélée. Demande Bourama (25/07) : uniquement l'entrée pour
    # l'instant, pas de vérification sur la sortie de l'agent (pour
    # limiter le surcoût en tokens -- l'agent garde ses garde-fous via son
    # prompt système + le filet JSON cassé déjà en place).

    if agent_id is None:
        agent_id = get_secret("AGENT_ID") or AGENT_ID_PAR_DEFAUT

    # Comportements de l'étudiant + programmes (14/08) : calculés UNE
    # SEULE FOIS ici, jamais recalculés plus bas (voir _construire_system_prompt,
    # qui les reçoit désormais en paramètre) -- pas de second appel LLM
    # inutile au petit routeur choisir_comportements_pertinents.
    #
    # Corrige une désynchronisation : le mini-routeur "à la skill"
    # ci-dessous décide QUELS candidats annoncer au modèle, mais c'est un
    # mécanisme totalement séparé du routeur général d'outils plus bas
    # (_router_outils), qui lui décide, sans rien savoir de ces candidats,
    # si gerer_comportement/consulter_programme sont réellement
    # branchés à l'appel Groq. Avant ce fix, le premier pouvait annoncer un
    # candidat pertinent sans que le second n'ait jamais branché l'outil
    # correspondant -- même contradiction que le bug du 12/08 (prompt qui
    # affirme une capacité non réellement présente). outils_forces_contexte
    # est donc fusionné à CHAQUE point plus bas où la liste finale d'outils
    # est décidée, indépendamment de ce que dit le routeur général.
    #
    # Pas de chemin image/vidéo (Gemini, aucun outil MCP dans cette
    # branche, voir plus bas) -- comportements_etudiant/mes_programmes y
    # restent calculés (coût négligeable, mes_programmes est déjà quasi
    # gratuit) mais outils_forces_contexte n'y est jamais fusionné.
    # Système de codes de partage (14/08, voir core/codes_partage.py) :
    # ce que cet utilisateur a REÇU (comportement/programme) via un code
    # entré est fusionné avec ses propres comportements/programmes AVANT
    # le petit routeur "à la skill" -- même traitement, même pertinence
    # jugée par message, pas d'affichage systématique.
    # Routage en deux niveaux (22/08/2026, demande Bourama -- corrige la
    # saturation du petit routeur causée par l'audit qui a créé un skill
    # par chapitre, cf. discussion) : niveau 1 (générique/programme/matière)
    # d'abord, tout comme avant l'audit. Niveau 2 (chapitres) SEULEMENT pour
    # les matières que le niveau 1 a retenues -- jamais les ~70 skills de
    # chapitre d'un coup. Contrairement au niveau 1 (invisible), le niveau 2
    # est affiché comme un vrai résultat d'outil (voir
    # core/registre_outils.py::consulter_skills_chapitres_matiere) même si
    # c'est ce petit routeur qui décide de le déclencher, pas le grand LLM.
    comportements_etudiant = []
    if user_id and message_utilisateur:
        tous_comportements = (
            [c for c in lister_comportements_etudiant(agent_id, user_id) if c.get("actif", True)]
            + lister_comportements_recus(user_id)
        )
        candidats_niveau1, candidats_chapitre = separer_comportements_par_niveau(tous_comportements)
        retenus_niveau1 = choisir_comportements_pertinents(message_utilisateur, candidats_niveau1)
        comportements_etudiant = list(retenus_niveau1)

        matieres_retenues = {
            c["lien_id"] for c in retenus_niveau1
            if c.get("lien_type") == "matiere" and c.get("lien_id")
        }
        candidats_niveau2 = []
        for matiere_id in matieres_retenues:
            candidats_niveau2 += lister_comportements_chapitres_pour_matiere(candidats_chapitre, matiere_id)

        if candidats_niveau2:
            nom_outil_niveau2 = "consulter_skills_chapitres_matiere"
            yield {"type": "statut", "texte": f"{_nom_lisible(nom_outil_niveau2)}..."}
            retenus_niveau2 = choisir_comportements_pertinents(message_utilisateur, candidats_niveau2)
            yield {"type": "statut_termine", "texte": f"{_nom_lisible(nom_outil_niveau2)} effectuée"}
            yield {
                "type": "outil_resultat",
                "nom_outil": nom_outil_niveau2,
                "nom_lisible": _nom_lisible(nom_outil_niveau2),
                "resultat": (
                    f"{len(candidats_niveau2)} skill(s) de chapitre trouvé(s), "
                    f"{len(retenus_niveau2)} retenu(s) comme pertinent(s)."
                ),
            }
            comportements_etudiant += retenus_niveau2
    # mes_programmes toujours vide désormais -- fonctionnalité "Programme"
    # désactivée le 29/08/2026, voir
    # _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md (anciennement :
    # lister_mes_programmes_legers(user_id) + lister_programmes_recus_legers(user_id)).
    mes_programmes = []
    outils_forces_contexte = []
    if comportements_etudiant:
        outils_forces_contexte.append("gerer_comportement")
    # Outils toujours actifs pour Clovis (04/09/2026, demande Bourama) :
    # "sa source de connaissance dès qu'il connaît pas ou ne comprend
    # pas" -- doivent être disponibles au grand modèle à CHAQUE message,
    # sans dépendre de ce que suggère _router_outils ni d'une sélection
    # manuelle. Condition en dur sur agent_id (et non une colonne agent
    # générique comme routeur_outils_auto) : décision explicite de
    # Bourama, clovis-frontend fixe AGENT_ID="clovis" en dur partout
    # (simplification du 14/08, plus de vrai système multi-agents côté
    # produit malgré le code partagé avec djiguigne-backend).
    if agent_id == "clovis":
        outils_forces_contexte.append("gerer_document_bibliotheque")
        if natif:
            # Paire indissociable (voir règle "monde téléphone" dans
            # _router_outils) : explorer_dossier a besoin des noms
            # listés par gerer_dossier_telephone pour fonctionner.
            outils_forces_contexte += ["gerer_dossier_telephone", "explorer_dossier"]

    # Outils gardés par le grand modèle au tour précédent (2026-09-04,
    # demande Bourama) : voir _outil_garder_outils/_lire_outils_retenus.
    # VOLONTAIREMENT séparé de outils_forces_contexte -- ne doit jamais
    # entrer dans le calcul de outils_suggeres (ligne plus bas) sous
    # peine de redéclencher le bouton de confirmation "outils_suggeres"
    # côté frontend pour un outil déjà consenti tacitement par le modèle
    # lui-même. Fusionné uniquement aux deux points où le catalogue final
    # est réellement construit (sans passer par ce bouton) : le prompt
    # optimiste et la branche outil_force directe, plus bas.
    outils_retenus_precedents = _lire_outils_retenus(conversation_id)

    def _fusionner_outils(liste_base, extra):
        """Union ordonnée sans doublons, jamais liste vide (None si rien)."""
        fusion = list(dict.fromkeys((liste_base or []) + (extra or [])))
        return fusion or None

    # Routeur d'outils (2026-07-28, demande Bourama) : voir _router_outils
    # plus haut pour la doc complète. Ne se déclenche QUE si rien n'est
    # déjà forcé (ni sélection manuelle via BarreDeSaisie.tsx, ni clic sur
    # une suggestion précédente qui a renvoyé outil_force lui-même) --
    # sinon on tournerait en boucle. Pas de chemin image/vidéo (Gemini,
    # aucun outil MCP dans cette branche de toute façon, voir plus bas) ni
    # de reprise (déjà retournée avant ce point).
    #
    # ignorer_suggestion_outils (31/07, demande Bourama) : bouton "Aucun"
    # à côté des suggestions -- le routeur se trompe souvent (suggère un
    # outil sans rapport avec la question), l'utilisateur doit pouvoir
    # relancer sa question SANS repasser par le routeur (sinon, comme
    # outil_force serait vide/falsy, la condition ci-dessous re-déclencherait
    # le routeur et redonnerait potentiellement la même suggestion à côté --
    # boucle silencieuse pour l'utilisateur). Distinct de outil_force=None
    # normal : ici on VEUT explicitement zéro outil, pas "laisse le routeur
    # décider".
    # Perf (10/08, demande Bourama : "avant c'était quasi instantané") :
    # le routeur d'outils est un appel LLM séparé et complet (voir
    # _router_outils), payé en SÉQUENCE avant même de commencer à
    # construire le prompt de la vraie réponse -- gros ajout de latence
    # sur quasi tous les messages texte normaux. Dans l'écrasante
    # majorité des cas, le routeur ne suggère RIEN (voir ses instructions :
    # "liste vide si rien n'est pertinent", conçu pour rester silencieux
    # sauf besoin réel), auquel cas le prompt qu'on aurait construit sans
    # lui est de toute façon exactement le bon. On lance donc le routeur
    # ET la construction "optimiste" du prompt (comme si aucun outil
    # n'était suggéré, cas normal AVANT ce correctif) EN PARALLÈLE :
    # - routeur muet (cas normal) -> on utilise directement ce qui a déjà
    #   été calculé en parallèle, latence du routeur totalement absorbée.
    # - routeur_outils_auto=false (comportement bouton) -> retour
    #   immédiat avec l'événement outils_suggeres comme avant, le travail
    #   optimiste est simplement jeté (rien de cassé, coût négligeable).
    # - routeur_outils_auto=true ET des outils sont suggérés (seul cas où
    #   outil_force change réellement) -> le prompt optimiste ne convient
    #   plus, on le recalcule avec le bon outil_force, exactement comme
    #   avant ce correctif (donc jamais plus lent que l'ancien
    #   comportement dans ce cas rare, seulement dans les cas fréquents
    #   où ça ne change rien).
    outils_suggeres = []
    routeur_auto = False
    if not outil_force and not ignorer_suggestion_outils and message_utilisateur and not image_url and not images_base64:
        def _tache_routeur():
            outils_disponibles_agent, _ = lister_outils_autorises_pour_agent(get_secret, user_id, agent_id)
            # Notion + GitHub exclus du CATALOGUE envoyé au routeur automatique
            # (14/08, demande Bourama : "enlève le catalogue que ce ne soit
            # plus suggéré, comme s'il ne fonctionne plus"). Ces deux serveurs
            # à eux seuls totalisent ~31 outils sur 62 disponibles pour Clovis,
            # chacun avec sa description complète -- ce qui fait dépasser à
            # coup sûr la limite TPM (6000) du petit modèle routeur
            # (MODELE_ROUTEUR_OUTILS), d'où le 413 Payload Too Large observé
            # à CHAQUE appel et donc AUCUNE suggestion automatique possible,
            # quelle que soit la question. Filtre appliqué uniquement ici (le
            # catalogue proposé au routeur) : la sélection MANUELLE (bouton
            # Outils, outil_force) n'est pas touchée, Notion et GitHub restent
            # entièrement utilisables ainsi -- voir lister_tous_les_outils
            # plus bas, non modifié.
            outils_disponibles_agent = [
                o for o in outils_disponibles_agent
                if not o["function"]["name"].startswith("notion-")
                and "depot_github" not in o["function"]["name"]
            ]
            # 18/08 : filtre "onglet == generer" retire. La vraie cause de
            # l'echec du routeur etait le budget de SORTIE
            # (max_completion_tokens + reasoning_effort implicite, voir
            # _router_outils plus haut), pas la taille du catalogue
            # d'ENTREE -- retirer les outils de generation ne corrigeait
            # donc rien sur ce point precis. Les outils de generation
            # restent dans le catalogue automatique.
            return _router_outils(message_utilisateur, outils_disponibles_agent, historique)

        def _tache_prompt_optimiste():
            outil_force_contexte_seul = _fusionner_outils(None, outils_forces_contexte + outils_retenus_precedents)
            outils_mcp, table_routage = lister_tous_les_outils(get_secret, user_id, agent_id, outil_force_contexte_seul)
            outil_force_verifie_optimiste = [o["function"]["name"] for o in outils_mcp] if outil_force_contexte_seul else None
            system_final = _construire_system_prompt(message_utilisateur, agent_id, user_id, longueur_reponse, fuseau_horaire, recherche_forcee, outil_force_verifie_optimiste, sans_enseignant, comportements_etudiant, mes_programmes)
            return outils_mcp, table_routage, system_final

        with concurrent.futures.ThreadPoolExecutor() as executor:
            f_routeur = executor.submit(_tache_routeur)
            f_optimiste = executor.submit(_tache_prompt_optimiste)
        # CORRECTIF PERF (04/09/2026, bug remonté par Bourama : "clovis
        # répond trop lentement") : la décision d'entrer dans le bloc
        # coûteux ci-dessous (if outils_suggeres:) doit se baser
        # UNIQUEMENT sur ce que le ROUTEUR a réellement trouvé de
        # nouveau, jamais sur outils_forces_contexte -- ces outils
        # toujours forcés (ex: gerer_document_bibliotheque pour clovis,
        # voir plus haut) sont de toute façon déjà inclus dans
        # outil_force_contexte_seul et donc dans le prompt "optimiste"
        # calculé en parallèle juste au-dessus (_tache_prompt_optimiste).
        # Avant ce correctif, fusionner outils_forces_contexte ici
        # rendait outils_suggeres non-vide à CHAQUE message pour clovis
        # (gerer_document_bibliotheque toujours présent), qui entrait
        # donc systématiquement dans le bloc `if outils_suggeres:` plus
        # bas -- lequel jette le prompt optimiste déjà calculé et le
        # recalcule intégralement en séquentiel (routeur_outils_auto=true
        # pour clovis, confirmé en base) : coût payé sur 100% des
        # messages au lieu des seuls cas où le petit routeur suggère
        # vraiment quelque chose de nouveau, comme prévu à l'origine
        # (voir commentaire PERF 10/08 juste au-dessus).
        outils_suggeres_routeur = f_routeur.result()
        outils_suggeres = _fusionner_outils(outils_suggeres_routeur, outils_forces_contexte) or []
        outils_mcp, table_routage, system_final = f_optimiste.result()
        outil_force_verifie = None

        # PERF (10/08) : premier point où quelque chose serait révélé à
        # l'utilisateur (l'événement outils_suggeres juste en dessous) --
        # c'est ici, et seulement ici, qu'on attend enfin la modération
        # lancée en tâche de fond plus haut (déjà terminée ou presque,
        # vu qu'elle a tourné en parallèle du routeur d'outils + de la
        # construction du prompt pendant tout ce temps).
        if f_moderation is not None:
            est_sur, categorie = f_moderation.result()
            f_moderation_executor.shutdown(wait=False)
            if not est_sur:
                logging.warning(f"Message bloqué par la modération d'entrée (gpt-oss-safeguard, {categorie}).")
                yield {"type": "reponse", "texte": MESSAGE_CONTENU_BLOQUE}
                return

        if outils_suggeres_routeur:
            # routeur_outils_auto (03/08, demande Bourama, agent par agent) :
            # colonne sur `agents`, false par defaut. Si true pour CET agent,
            # on saute l'etape bouton cliquable (evenement "outils_suggeres")
            # et on envoie directement la suggestion du routeur au modele,
            # comme si l'utilisateur avait force ces outils lui-meme --
            # aucune confirmation cliquee. Les autres agents gardent le
            # comportement bouton normal (return ci-dessous inchange).
            try:
                agent_ligne = (
                    supabase.table("agents").select("routeur_outils_auto").eq("id", agent_id).maybe_single().execute()
                )
                routeur_auto = bool((agent_ligne.data or {}).get("routeur_outils_auto"))
            except Exception as e:
                logging.error(f"ERREUR lecture routeur_outils_auto agent={agent_id} : {e}")
                routeur_auto = False

            if routeur_auto:
                # Prompt optimiste invalide (calculé avec outil_force=None) :
                # DOIT être explicitement jeté, sinon le bloc plus bas
                # (`if system_final is None`) le laisserait passer tel
                # quel malgré outil_force mis à jour juste en dessous --
                # seul cas où on repaie le coût séquentiel, exactement
                # comme avant ce correctif.
                outil_force = _fusionner_outils(outils_suggeres, outils_retenus_precedents)
                outils_mcp = table_routage = system_final = None
            else:
                yield {"type": "outils_suggeres", "outils": outils_suggeres}
                return
    else:
        outils_mcp = table_routage = system_final = None  # recalculés ci-dessous dans tous les autres cas
        if not (image_url or images_base64):
            # Routeur général court-circuité ici (outil déjà forcé manuellement,
            # bouton "Aucun" cliqué, ou pas de message) -- les outils de
            # contexte (comportement/programme) doivent quand même être
            # fusionnés, ils ne dépendent pas du routeur général.
            outil_force = _fusionner_outils(outil_force, outils_forces_contexte + outils_retenus_precedents)

    # CORRECTION (29/07, Bourama) : la liste réelle d'outils (celle qui
    # part dans tools=... vers Groq, filtrée par autorisation agent en
    # base) doit être calculée AVANT le system prompt, et c'est ELLE qui
    # doit servir à annoncer "OUTIL(S) ACTIF(S)" -- jamais outil_force brut
    # (sélection frontend non vérifiée). Avant ce fix : si un outil
    # sélectionné (ex: generer_code) n'était pas autorisé en base pour cet
    # agent, il disparaissait silencieusement de outils_mcp mais le system
    # prompt continuait d'affirmer au modèle qu'il était "disponible et
    # prêt à être appelé" -- contradiction qui pouvait pousser le modèle à
    # halluciner un faux appel (bloc TOOL_CODE) plutôt que d'utiliser un
    # vrai outil absent de son schéma technique réel.
    if system_final is None:
        if image_url or images_base64:
            # Chemin image = Gemini, aucun outil MCP jamais utilisé ici (voir
            # plus bas) -- inutile d'interroger les serveurs MCP pour rien.
            outils_mcp, table_routage = [], {}
            outil_force_verifie = outil_force
        else:
            outils_mcp, table_routage = lister_tous_les_outils(get_secret, user_id, agent_id, outil_force)
            outil_force_verifie = [o["function"]["name"] for o in outils_mcp] if outil_force else outil_force
        system_final = _construire_system_prompt(message_utilisateur, agent_id, user_id, longueur_reponse, fuseau_horaire, recherche_forcee, outil_force_verifie, sans_enseignant, comportements_etudiant, mes_programmes)

        # PERF (10/08) : second (et dernier) point de vérification --
        # couvre tous les chemins qui ne passent PAS par le premier
        # (outil déjà forcé côté frontend, bouton "Aucun" cliqué, ou
        # message avec image/vidéo jointe). f_moderation.result() est
        # sans coût s'il a déjà terminé (cas normal, il tourne depuis le
        # tout début de la fonction) -- appeler .result() deux fois sur
        # le même Future ne relance rien, renvoie juste la même valeur.
        if f_moderation is not None:
            est_sur, categorie = f_moderation.result()
            f_moderation_executor.shutdown(wait=False)
            if not est_sur:
                logging.warning(f"Message bloqué par la modération d'entrée (gpt-oss-safeguard, {categorie}).")
                yield {"type": "reponse", "texte": MESSAGE_CONTENU_BLOQUE}
                return

    # Outil interne garder_outils (2026-09-04, demande Bourama) : ajouté
    # UNE SEULE FOIS ici, une fois outils_mcp définitivement établi (tous
    # les chemins ci-dessus convergent à ce point) -- voir
    # _outil_garder_outils. Rien à garder si aucun outil n'est proposé ce
    # tour-ci de toute façon. Le tour est aussi réinitialisé à liste vide
    # ici (jamais dans _agent_groq lui-même, qui peut être rappelé
    # plusieurs fois pour CE MÊME tour -- cascade de secours, rattrapage
    # TOOL_CODE) : garder_outils, s'il est appelé plus loin, réécrira la
    # bonne valeur par-dessus avant la fin du tour.
    if outils_mcp:
        outils_mcp = outils_mcp + [_outil_garder_outils([o["function"]["name"] for o in outils_mcp])]
        _ecrire_outils_retenus(conversation_id, [])

    if localisation and localisation.get("latitude") is not None and localisation.get("longitude") is not None:
        # Contexte "système/environnement" (2026-07-20) : position GPS
        # transmise explicitement par l'utilisateur (bouton dédié côté
        # frontend, jamais automatique/silencieux -- voir BarreDeSaisie.tsx
        # et la permission navigateur navigator.geolocation). Ajoutée en
        # fin de prompt système, jamais comme un fait affirmé par
        # l'utilisateur lui-même.
        system_final += (
            "\n\nContexte de localisation (fourni par le navigateur de "
            "l'utilisateur, à utiliser seulement si pertinent pour la "
            f"question) : latitude {localisation['latitude']}, "
            f"longitude {localisation['longitude']}."
        )

    # Liens collés dans le message (page web ou vidéo YouTube) : récupérés
    # ICI, sur le message pour le modèle uniquement -- message_utilisateur
    # (brut, sans le contenu des liens) reste ce qui est sauvegardé dans
    # l'historique via _sauvegarder_echange plus bas.
    message_pour_modele = _enrichir_message_avec_urls(message_utilisateur, user_id)

    messages_base = [{"role": "system", "content": system_final}]
    messages_base += historique
    messages_base.append({"role": "user", "content": message_pour_modele})

    if image_url or images_base64:
        # Chemin dédié image(s) : voir docstring ci-dessus. Pas de cascade
        # multi-modeles ici, Gemini est le seul maillon capable de traiter
        # de la vision -- s'il echoue, il n'y a pas de second recours
        # multimodal. `images_base64` (2026-07-20) : frames extraites d'une
        # vidéo par _extraire_frames_video, voir la branche vidéo dédiée
        # dans api/uploads.py:uploader_video_chat -- même mécanique que
        # l'image simple, juste plusieurs inline_data au lieu d'un seul.
        images = []
        if image_url:
            try:
                images.append(_telecharger_image(image_url))
            except Exception as e:
                logging.error(f"ERREUR TELECHARGEMENT IMAGE ({image_url}): {e}")
                yield {"type": "reponse", "texte": "Désolé, je n'ai pas pu récupérer l'image envoyée. Réessaie."}
                return
        if images_base64:
            for image_b64 in images_base64:
                images.append((base64.b64decode(image_b64), "image/jpeg"))

        gemini_messages = [
            {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
            for m in messages_base[:-1] if m["role"] != "system"
        ]
        gemini_messages.append({
            "role": "user",
            "parts": _construire_parts_gemini(message_pour_modele, images),
        })

        reponse_accumulee = []
        meta_utilisateur = {"pieces_jointes": []}
        if image_url:
            meta_utilisateur["pieces_jointes"].append({
                "nom": image_url.split("/")[-1].split("?")[0] or "image",
                "type": "image",
                "previewUrl": image_url,
            })
        if images_base64:
            # Pas d'URL persistante disponible ici (frames extraites a la
            # volee, voir api/uploads.py:uploader_video_chat) -- la video
            # originale est bien stockee cote bibliotheque mais son URL
            # n'est aujourd'hui pas transmise jusqu'a chat(). Best-effort :
            # on note au moins qu'une video etait jointe, plutot que de
            # perdre totalement la trace.
            meta_utilisateur["pieces_jointes"].append({
                "nom": "vidéo",
                "type": "video",
                "erreur": "aperçu non conservé après rechargement",
            })
        try:
            client_google = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
            response = client_google.models.generate_content_stream(
                model=GOOGLE_MODEL,
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    system_instruction=system_final
                )
            )
            for chunk in response:
                if chunk.text:
                    reponse_accumulee.append(chunk.text)
                    yield {"type": "reponse", "texte": chunk.text}
            logging.info("Réponse via GEMINI (image)")
            ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id, modele=GOOGLE_MODEL, meta_utilisateur=meta_utilisateur)
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
            _mettre_a_jour_profil_utilisateur_si_besoin(user_id, agent_id)
        except Exception as e:
            logging.error(f"ERREUR GEMINI (image): {e}")
            if not reponse_accumulee:
                yield {"type": "reponse", "texte": MESSAGE_ERREUR}
        return

    if modele_force:
        # Modele premium (Claude/GPT/Gemini/DeepSeek), voir docstring de
        # chat() -- meme structure que le bloc image juste au-dessus :
        # pas d'outils MCP, pas de cascade de secours multi-modeles, un
        # seul appel, on retombe sur MESSAGE_ERREUR s'il echoue.
        messages_premium = [
            {"role": m["role"], "content": m["content"]}
            for m in messages_base if m["role"] != "system"
        ]
        reponse_accumulee = []
        try:
            for morceau in generer_reponse_premium(modele_force, system_final, messages_premium):
                reponse_accumulee.append(morceau)
                yield {"type": "reponse", "texte": morceau}
            logging.info(f"Réponse via MODELE PREMIUM : {modele_force}")
            ids_historique = _sauvegarder_echange(
                user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id, modele=modele_force
            )
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
            _mettre_a_jour_profil_utilisateur_si_besoin(user_id, agent_id)
        except Exception as e:
            logging.error(f"ERREUR MODELE PREMIUM ({modele_force}) : {e}")
            if not reponse_accumulee:
                yield {"type": "reponse", "texte": MESSAGE_ERREUR}
        return

    client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
    # Nom affiché de l'agent (ex. "Nucleos"), calculé UNE fois ici -- voir
    # _nom_agent, utilisé pour que la confirmation d'une action sensible
    # dise "Nucleos veut faire X" plutôt qu'une description générique.
    agent_nom = _nom_agent(agent_id)

    for _passage in range(MAX_PASSAGES_CASCADE):
        tout_est_timeout = True

        # Une SEULE liste de messages pour tout ce passage de la cascade
        # Groq (modele principal + fallbacks), au lieu d'en recreer une a
        # chaque modele. Raison : si un modele a deja appele un outil (ex:
        # notion-search) et obtenu un resultat AVANT d'echouer sur l'appel
        # Groq suivant (429/413 en essayant de rediger la reponse finale),
        # le resultat de cet outil est deja present dans messages_agent
        # (ajoute par _agent_groq/_traiter_appels). Si on repartait de
        # messages_base a chaque modele, ce resultat serait perdu et le
        # modele de secours suivant redemarrerait a zero, sans le contexte
        # deja recupere (cause du bug ou la page Notion trouvee n'arrivait
        # jamais dans la reponse finale).
        messages_agent = list(messages_base)
        reponse_accumulee = []
        meta_assistant = {}

        # 1. GPT-OSS 120B, avec cycle d'outils MCP dynamique
        try:
            yield from _capturer_reponse(
                _agent_groq(client_groq, messages_agent, outils_mcp, table_routage, agent_nom=agent_nom,
                            reasoning_effort=MODELES_AVEC_REASONING_EFFORT.get(GROQ_PRIMARY),
                            conversation_id=conversation_id),
                reponse_accumulee,
                meta_assistant,
            )
            ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id, modele=GROQ_PRIMARY, meta_assistant=meta_assistant)
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
            _mettre_a_jour_profil_utilisateur_si_besoin(user_id, agent_id)
            return
        except Exception as e:
            if not _est_timeout(e):
                tout_est_timeout = False
                logging.error(f"ERREUR GROQ {GROQ_PRIMARY}: {e}")
            evenement_repli = _repli_si_reponse_partielle(reponse_accumulee)
            if evenement_repli:
                yield evenement_repli

        # 2. Fallbacks Groq — AVEC les memes outils MCP (via _agent_groq),
        # pour que Notion/Wolfram restent utilisables meme quand
        # GROQ_PRIMARY sature son quota TPM (ce qui est le cas le plus
        # frequent de bascule ici, pas une vraie panne du modele).
        # reasoning_pour_ce_modele vient de MODELES_AVEC_REASONING_EFFORT.get(model) :
        # chaque modele recoit sa propre valeur valide ("none" pour Qwen 3,
        # "low" pour GPT-OSS -- jamais "none" pour ce dernier, invalide cote
        # API, voir la definition du dict plus haut), None (donc rien envoye)
        # pour les modeles non-raisonnement comme llama-3.3-70b-versatile et
        # llama-3.1-8b-instant.
        # IMPORTANT : on reutilise messages_agent tel quel (meme instance,
        # mutee en place par _agent_groq) d'un modele a l'autre — on ne le
        # reinitialise PAS a messages_base a chaque tour de boucle (voir
        # commentaire ci-dessus).
        for model in GROQ_FALLBACKS:
            try:
                reasoning_pour_ce_modele = MODELES_AVEC_REASONING_EFFORT.get(model)
                yield from _capturer_reponse(
                    _agent_groq(
                        client_groq, messages_agent, outils_mcp, table_routage,
                        modele=model, reasoning_effort=reasoning_pour_ce_modele, agent_nom=agent_nom,
                        conversation_id=conversation_id,
                    ),
                    reponse_accumulee,
                    meta_assistant,
                )
                ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id, modele=model, meta_assistant=meta_assistant)
                # Signale au frontend quand la reponse vient d'un modele de
                # qualite reduite (demande Bourama, 26/07) : evite que
                # l'utilisateur juge la plateforme sur une reponse plus
                # faible que la normale sans le savoir -- voir
                # MODELES_QUALITE_REDUITE plus haut et StatutOutil.tsx /
                # ChatIA.tsx cote frontend pour l'affichage.
                meta_a_envoyer = dict(ids_historique) if ids_historique else {}
                if model in MODELES_QUALITE_REDUITE:
                    meta_a_envoyer["modele_qualite_reduite"] = True
                if meta_a_envoyer:
                    yield {"type": "meta", **meta_a_envoyer}
                _mettre_a_jour_resume_si_besoin(user_id)
                _mettre_a_jour_profil_utilisateur_si_besoin(user_id, agent_id)
                return
            except Exception as e:
                if not _est_timeout(e):
                    tout_est_timeout = False
                    logging.error(f"ERREUR GROQ {model}: {e}")
                evenement_repli = _repli_si_reponse_partielle(reponse_accumulee)
                if evenement_repli:
                    yield evenement_repli
                continue

        # 3. Gemini 2.5 Flash — tout dernier recours, sans outils MCP a lui,
        # mais REND COMPTE de ce qu'un outil Groq a deja execute avant lui
        # dans cette meme cascade (2026-07-31, corrige suite a un cas reel
        # observe par Bourama en logs : tavily_search execute avec succes,
        # 7000+ caracteres de resultat obtenus, mais tous les modeles Groq
        # tombent en rate limit sur l'appel de REDACTION finale -- Gemini
        # prend le relais et repond quand meme "je ne peux pas verifier en
        # direct", car il partait de messages_base (jamais mute), qui ne
        # contient jamais les resultats d'outils -- le resultat deja obtenu
        # etait donc silencieusement jete. Utilise messages_agent (comme la
        # cascade Groq juste au-dessus, meme logique/meme commentaire) : SI
        # un outil a deja tourne, son resultat y est present et Gemini doit
        # s'en servir ; SINON (aucun outil execute avant d'arriver ici),
        # Gemini doit dire que l'outil n'est pas disponible MAINTENANT
        # (indisponibilite technique temporaire) plutot que de se presenter
        # comme une IA incapable de faire des recherches par nature.
        try:
            client_google = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
            outil_deja_execute = any(m.get("role") == "tool" for m in messages_agent)
            gemini_messages = []
            for m in messages_agent:
                if m["role"] == "system":
                    continue
                if m["role"] == "tool":
                    # Pas de role "tool" natif dans ce format simplifie de
                    # contenus Gemini (contents/parts) -- integre comme
                    # contexte texte explicite, marque clairement pour que
                    # l'instruction ci-dessous puisse s'y referer sans
                    # ambiguite.
                    gemini_messages.append(
                        {"role": "user", "parts": [{"text": f"[Résultat de l'outil déjà exécuté] {m['content']}"}]}
                    )
                elif m["role"] == "assistant" and not m.get("content"):
                    # Message "assistant" qui ne fait QUE declarer un appel
                    # d'outil (content=None, voir messages_agent.append
                    # plus haut dans _agent_groq) -- rien a montrer a
                    # Gemini, le resultat juste apres (role "tool"
                    # ci-dessus) suffit.
                    continue
                else:
                    gemini_messages.append(
                        {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
                    )
            # Instruction ciblee (2026-07-24, trouve par Bourama en test
            # reel, PERDUE le 25/07 par le commit de57439 -- modification
            # concurrente partie d'une base sans ce fix, qui a ecrase la
            # ligne system_instruction=system_gemini_sans_outils par
            # system_instruction=system_final -- reappliquee le 25/07
            # apres reapparition confirmee du bug) -- la regle generale
            # anti-hallucination du prompt (voir la page Notion Clovis, bloc
            # n'a PAS suffi ici : Gemini a quand meme invente un faux appel
            # d'outil (default_api.get_exchange_rate(...),
            # default_api.search_news(...), noms qui n'existent nulle part
            # dans le code reel -- "default_api" est un nom generique que
            # Gemini associe au function-calling dans ses propres exemples
            # d'entrainement). Ce chemin precis n'a REELEMENT aucun outil
            # branche (pas de parametre tools= sur cet appel), donc
            # l'instruction est directe et sans ambiguite plutot que de
            # compter sur la regle generale noyee dans un long prompt
            # systeme.
            if outil_deja_execute:
                system_gemini_sans_outils = (
                    system_final
                    + "\n\nIMPORTANT : un outil a DÉJÀ été exécuté plus tôt dans cet échange et son "
                    "résultat est présent ci-dessus, marqué \"[Résultat de l'outil déjà exécuté]\" -- "
                    "base ta réponse DESSUS. Ne dis JAMAIS que tu ne peux pas faire de recherche ou "
                    "vérifier l'information : le résultat est déjà là, utilise-le. Tu n'as par "
                    "contre AUCUN outil à appeler toi-même dans cette réponse précise (pas de "
                    "nouvel appel) -- n'écris jamais de code, de pseudo-code, ou de texte qui "
                    "ressemble à un appel d'outil/API."
                )
            else:
                system_gemini_sans_outils = (
                    system_final
                    + "\n\nIMPORTANT : tu n'as accès à AUCUN outil réel dans cette réponse précise "
                    "(pas de recherche web, pas d'API externe, pas de Notion, rien). Si la question "
                    "porte sur une information qui change (prix, taux de change, actualité, données "
                    "en temps réel...) et nécessiterait normalement un outil, dis clairement que cet "
                    "outil n'est PAS DISPONIBLE POUR L'INSTANT (indisponibilité technique "
                    "temporaire) plutôt que de deviner OU de te présenter comme une IA qui ne peut "
                    "pas faire de recherches par nature -- ce n'est pas une limite permanente, "
                    "juste indisponible sur ce message précis. N'écris JAMAIS de code, de "
                    "pseudo-code, ou de texte qui ressemble à un appel d'outil/API (même dans un "
                    "bloc de code) -- tu n'as aucun outil à appeler, l'écrire ne fait qu'inventer "
                    "un résultat qui n'existe pas."
                )
            response = client_google.models.generate_content_stream(
                model=GOOGLE_MODEL,
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    system_instruction=system_gemini_sans_outils
                )
            )
            for chunk in response:
                if chunk.text:
                    reponse_accumulee.append(chunk.text)
                    yield {"type": "reponse", "texte": chunk.text}
            logging.info("Réponse via GEMINI")
            ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id, modele=GOOGLE_MODEL)
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
            _mettre_a_jour_profil_utilisateur_si_besoin(user_id, agent_id)
            return
        except Exception as e:
            if not _est_timeout(e):
                tout_est_timeout = False
            logging.error(f"ERREUR GEMINI: {e}")
            evenement_repli = _repli_si_reponse_partielle(reponse_accumulee)
            if evenement_repli:
                yield evenement_repli

        if not tout_est_timeout:
            break  # au moins une vraie erreur (pas juste lent) : inutile de retenter

        logging.info("Toute la cascade a timeout, on retente un passage complet.")

    # Echec complet : on ne persiste jamais un message d'erreur technique
    # en memoire (polluerait le resume avec du bruit sans valeur).
    yield {"type": "reponse", "texte": MESSAGE_ERREUR}

