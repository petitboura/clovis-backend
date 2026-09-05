# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Sauvegarde d'un echange (message + reponse) et mise a jour
# periodique du resume memoire de l'utilisateur.
import logging
from groq import Groq
from constantes_agent import get_secret, supabase, MODELE_RESUME, SEUIL_RESUME_MESSAGES
from profils_agents import _charger_resume_memoire

def _sauvegarder_echange(user_id, agent_id, message_utilisateur, reponse_finale, conversation_id=None, modele=None, meta_utilisateur=None, meta_assistant=None):
    """
    Persiste l'echange (question + reponse) dans `conversations`, pour la
    memoire long-terme. Ignore silencieusement si l'utilisateur n'est pas
    connecte (user_id=None) ou si la reponse est vide (ex: message
    d'erreur technique, qu'on ne veut pas polluer la memoire avec).

    `modele` (optionnel, 02/08/2026) : modele_id qui a genere
    `reponse_finale`, ecrit UNIQUEMENT sur la ligne "assistant" de
    `historique_conversations` (pas sur `conversations`, table de memoire
    court terme sans vocation d'affichage) -- None si la cascade Groq/
    Gemini par defaut a repondu (comportement historique inchange, colonne
    nullable), sinon le modele_id premium (voir core/fournisseurs_llm.py).
    Permet au frontend d'afficher quel modele a repondu sous chaque
    message (voir AgentEditable.modeles_disponibles cote api/agents.py).

    `meta_utilisateur`/`meta_assistant` (optionnels, dict, 28/08/2026) :
    ecrits respectivement sur la ligne "user" et la ligne "assistant" de
    historique_conversations (colonne meta, jsonb). But : ce qui n'existe
    aujourd'hui que le temps du direct (evenements SSE outil_resultat/
    sources, piece jointe image) disparaissait entierement a la
    reouverture d'une conversation, seul le texte brut survivant. Contenu
    attendu : meta_utilisateur = {"pieces_jointes": [...]},
    meta_assistant = {"outils": [{"nomOutil", "nomLisible", "resultat",
    "sources"}, ...]} -- voir _capturer_reponse pour la construction de
    meta_assistant, structure alignee sur MessageAffiche.outilsResultats
    cote frontend.
    """
    ids_historique = None  # renvoyé à l'appelant pour l'indexation du feedback

    if not user_id or not (reponse_finale or "").strip():
        return ids_historique
    try:
        supabase.table("conversations").insert([
            {"user_id": user_id, "agent_id": agent_id, "role": "user", "content": message_utilisateur},
            {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": reponse_finale},
        ]).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (sauvegarde conversations) : {e}")

    # Ajouté le 2026-07-13 (Bourama : historique de conversation visible,
    # conservée par agent, dans le tableau de bord). Table SÉPARÉE de
    # `conversations` ci-dessus, jamais purgée -- voir le commentaire de
    # migration (historique_conversations) pour le detail de la
    # distinction. Volontairement dans un bloc try/except À PART : si cette
    # écriture échoue, ça ne doit jamais faire échouer la mémoire de l'IA
    # ci-dessus, qui est la partie critique pour la qualité des réponses.
    #
    # `conversation_id` (2026-07-13, Bourama : liste de conversations
    # distinctes et cliquables dans la sidebar de chat.py, façon Claude.ai)
    # regroupe les messages d'un même fil de discussion, généré côté
    # chat.py (une valeur par conversation affichée, PAS par message) et
    # simplement transmis ici tel quel. None accepté (colonne nullable) :
    # un appelant qui ne gère pas encore les fils continue de fonctionner
    # sans erreur, ses messages sont juste groupés sous "historique ancien"
    # côté affichage plutôt que dans un fil précis.
    try:
        res = (
            supabase.table("historique_conversations")
            .insert([
                {"user_id": user_id, "agent_id": agent_id, "role": "user", "content": message_utilisateur, "conversation_id": conversation_id, "meta": meta_utilisateur or None},
                {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": reponse_finale, "conversation_id": conversation_id, "modele": modele, "meta": meta_assistant or None},
            ])
            .execute()
        )
        lignes = res.data or []
        ligne_user = next((l for l in lignes if l["role"] == "user"), None)
        ligne_assistant = next((l for l in lignes if l["role"] == "assistant"), None)
        if ligne_user and ligne_assistant:
            ids_historique = {
                "message_id_user": ligne_user["id"],
                "message_id_assistant": ligne_assistant["id"],
                "created_at_assistant": ligne_assistant.get("created_at"),
                # Propage automatiquement dans tous les evenements SSE
                # "meta" (voir chaque site d'appel plus haut, tous font
                # **ids_historique) -- evite de dupliquer ce champ a la
                # main partout, voir ChatIA.tsx cote frontend pour l'usage.
                "modele": modele,
            }
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (sauvegarde historique_conversations) : {e}")

    return ids_historique


def _mettre_a_jour_resume_si_besoin(user_id):
    """
    Si assez de nouveaux messages bruts se sont accumules (>= SEUIL_RESUME_MESSAGES)
    depuis le dernier resume, en regenere un condense (ancien resume + messages
    recents) via un modele Groq rapide, l'ecrit dans conversation_summaries, puis
    purge les messages bruts desormais condenses. Ne bloque jamais la reponse a
    la personne : toute erreur est juste loguee, jamais remontee a l'appelant.

    Compte unifie (juillet 2026) : scope par user_id seul, tous agents
    confondus. `agent_id` reste present dans `conversations` en tant que
    simple metadonnee de tracabilite (colonne non retiree par la
    migration), mais ne filtre plus rien ici -> les messages de tous les
    agents de la plateforme alimentent le meme resume.
    """
    if not user_id:
        return
    try:
        messages = (
            supabase.table("conversations")
            .select("id, role, content, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(SEUIL_RESUME_MESSAGES)
            .execute()
        ).data or []

        if len(messages) < SEUIL_RESUME_MESSAGES:
            return  # pas encore assez de matiere pour justifier un resume

        ancien_resume = _charger_resume_memoire(user_id)
        messages_recents = "\n".join(
            f"{'Utilisateur' if m['role'] == 'user' else 'Assistant'} : {m['content']}"
            for m in reversed(messages)
        )

        # Neutralisé le 2026-07-22 (Bourama : la plateforme n'est pas
        # réservée aux étudiants, ce n'était que le point de départ du
        # projet -- un ancien prompt ici forçait "niveau apparent" et
        # "sujets de difficulté d'étudiant" sur N'IMPORTE QUELLE
        # conversation, y compris des sessions de test technique sans
        # aucun rapport avec l'école, produisant des résumés inventés/hors
        # sujet). Ne présuppose plus rien sur qui est cette personne ni
        # sur la nature de l'agent avec qui elle parle.
        instruction_resume = (
            "Condense ce qui suit en un résumé factuel et concis (5-8 lignes maximum) "
            "de cette personne, utile pour personnaliser une future session avec elle : "
            "ses centres d'intérêt ou sujets récurrents, ses préférences, le contexte "
            "réellement présent dans les échanges. N'invente rien qui ne soit pas "
            "clairement indiqué -- ne présuppose ni niveau scolaire, ni statut "
            "d'étudiant, ni progression pédagogique si rien dans la conversation ne "
            "l'indique explicitement. Pas de politesse, pas de méta-commentaire, "
            "juste les faits utiles."
        )

        # CORRECTIF (16/08, decouvert via lire_memoire cote MCP -- capture
        # d'ecran Bourama) -- le resume genere par ce petit modele rapide
        # (llama-3.1-8b-instant) etait parfois une reponse conversationnelle
        # ("Je vais bien, merci !...") au lieu d'un resume, sauvegardee
        # telle quelle en base. Cause : tout partait dans un seul message
        # role="user" qui se terminait par "Utilisateur : <dernier
        # message>" -- un modele rapide/leger suit alors le pattern de la
        # transcription et "repond" a ce dernier tour au lieu d'executer la
        # consigne, placee plus haut, loin de la fin. Fix : instruction en
        # role="system" (jamais melangee a la transcription), transcription
        # nettement delimitee et explicitement marquee comme NE PAS y
        # repondre, et rappel de la consigne juste apres (les modeles
        # legers suivent mieux une instruction proche de la fin du prompt).
        contenu_utilisateur = ""
        if ancien_resume:
            contenu_utilisateur += f"Résumé précédent :\n{ancien_resume}\n\n"
        contenu_utilisateur += (
            "Transcription à condenser (ne PAS y répondre, ne PAS continuer "
            "cette conversation -- ta seule tâche est de la résumer selon la "
            "consigne ci-dessus) :\n"
            "--- DÉBUT TRANSCRIPTION ---\n"
            f"{messages_recents}\n"
            "--- FIN TRANSCRIPTION ---\n\n"
            "Rappel : produis uniquement le résumé factuel demandé (5-8 lignes), "
            "jamais une réponse à la personne."
        )

        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
        completion = client_groq.chat.completions.create(
            model=MODELE_RESUME,
            messages=[
                {"role": "system", "content": instruction_resume},
                {"role": "user", "content": contenu_utilisateur},
            ],
            max_completion_tokens=None,
            timeout=DELAI_MAX_PAR_APPEL,
        )
        nouveau_resume = completion.choices[0].message.content.strip()

        supabase.table("conversation_summaries").upsert({
            "user_id": user_id,
            "summary": nouveau_resume,
        }).execute()

        # Purge les messages bruts maintenant condenses, pour ne pas
        # reconstruire indefiniment le meme resume a chaque appel suivant.
        ids_a_purger = [m["id"] for m in messages if m.get("id") is not None]
        if ids_a_purger:
            supabase.table("conversations").delete().in_("id", ids_a_purger).execute()

        logging.info(f"Résumé mémoire mis à jour pour user={user_id}.")
    except Exception as e:
        logging.error(f"ERREUR mise à jour résumé mémoire : {e}")


