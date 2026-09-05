# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Tout ce qui concerne le nom/l'affichage d'un agent, le
# resume memoire utilisateur, et le profil dynamique par agent
# (chargement + mise a jour periodique via LLM).
import json
import logging
from datetime import datetime
from groq import Groq
from constantes_agent import get_secret, supabase, MODELE_PROFIL, SEUIL_PROFIL_MESSAGES, DELAI_MAX_PAR_APPEL
from filtre_texte_streaming import NOMS_OUTILS_LISIBLES

def _nom_agent(agent_id):
    """
    Nom affiché de l'agent (ex. "Nucleos"), PAS l'agent_id technique --
    utilisé pour que la confirmation d'une action sensible dise "Nucleos
    va faire X" plutôt qu'une description générique de l'outil (demande
    de Bourama, 2026-07-23 : le sujet de la phrase doit être l'agent,
    peu importe l'outil concerné -- GitHub, Notion, ou un futur outil).
    """
    if not agent_id:
        return None
    try:
        res = supabase.table("agents").select("nom").eq("id", agent_id).maybe_single().execute()
        return (res.data or {}).get("nom")
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture nom agent={agent_id}) : {e}")
        return None


def _nom_lisible(nom_outil, action=None):
    """
    Libellé affiché au frontend pour cet appel d'outil. Si `action` est
    fourni et qu'une entrée composite "nom_outil:action" existe dans
    REGISTRE_AFFICHAGE_OUTILS, elle prime sur l'entrée générique du nom
    d'outil seul -- nécessaire pour les outils consolidés "action +
    paramètres" dont certaines actions couvrent un domaine différent de
    celui suggéré par le libellé générique (ex: gerer_document_
    bibliotheque affichait "Bibliothèque personnelle" même pour une
    recherche dans le catalogue public ou les plugins publics, bug
    remonté par Bourama le 28/08). Même pattern composite que
    _est_outil_sensible pour OUTILS_SENSIBLES.
    """
    if action and f"{nom_outil}:{action}" in NOMS_OUTILS_LISIBLES:
        return NOMS_OUTILS_LISIBLES[f"{nom_outil}:{action}"]
    return NOMS_OUTILS_LISIBLES.get(nom_outil, nom_outil)


def _action_appel(appel):
    """Extrait le paramètre `action` des arguments JSON de cet appel, ou None si absent/invalide (mêmes garde-fous que _est_outil_sensible)."""
    try:
        arguments = json.loads(appel["arguments"] or "{}")
    except Exception:
        return None
    return arguments.get("action")


def _nom_lisible_appel(appel):
    """Raccourci : libellé lisible pour un appel complet (dict avec 'name' et 'arguments'), en tenant compte de son action le cas échéant."""
    return _nom_lisible(appel["name"], _action_appel(appel))


def _charger_resume_memoire(user_id):
    """
    Recupere le resume long-terme (table conversation_summaries) de cet
    utilisateur, valable pour tous les agents de la plateforme (compte
    unifie, juillet 2026). Retourne "" si l'utilisateur n'est pas connecte
    (user_id=None) ou si aucun resume n'existe encore.
    """
    if not user_id:
        return ""
    try:
        res = (
            supabase.table("conversation_summaries")
            .select("summary")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("summary") or ""
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture conversation_summaries) : {e}")
        return ""


def _charger_schema_profil(agent_id):
    """
    Renvoie la liste de champs définie par le créateur pour SON agent
    (agents.profil_utilisateur_schema, voir ChampProfilUtilisateur côté
    api/agents.py). Liste vide = fonctionnalité désactivée pour cet
    agent -- aucun profil n'est ni chargé ni construit dans ce cas.
    """
    if not agent_id:
        return []
    try:
        res = (
            supabase.table("agents")
            .select("profil_utilisateur_schema")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("profil_utilisateur_schema") or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture profil_utilisateur_schema agent={agent_id}) : {e}")
        return []


def _charger_profil_utilisateur(agent_id, user_id):
    """
    Profil dynamique déjà rempli pour cette paire (agent, utilisateur
    connecté) -- table agent_user_profiles. Utilisateurs connectés
    uniquement (décision du 2026-07-21 : aucun moyen fiable de
    reconnaître un visiteur anonyme d'une session à l'autre). Renvoie {}
    si non connecté, agent sans schéma défini, ou rien d'enregistré
    encore.
    """
    if not user_id or not agent_id:
        return {}
    try:
        res = (
            supabase.table("agent_user_profiles")
            .select("donnees")
            .eq("agent_id", agent_id)
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("donnees") or {}
    except Exception as e:
        logging.error(
            f"ERREUR SUPABASE (lecture agent_user_profiles agent={agent_id}, user={user_id}) : {e}"
        )
        return {}


def _mettre_a_jour_profil_utilisateur_si_besoin(user_id, agent_id):
    """
    Pendant du profil dynamique à _mettre_a_jour_resume_si_besoin
    ci-dessous, mais scopé à un seul agent (pas tous agents confondus) et
    guidé par un schéma défini par le créateur plutôt que par un résumé
    libre. Ne fait rien si : utilisateur non connecté, agent sans schéma
    défini (profil_utilisateur_schema vide -- cas par défaut, aucun coût
    ajouté pour les agents qui n'utilisent pas cette fonctionnalité), ou
    pas encore assez de nouveaux messages avec CET agent.

    Contrairement à _mettre_a_jour_resume_si_besoin, ne purge PAS les
    messages bruts de `conversations` -- ce n'est pas son rôle (le résumé
    mémoire s'en charge déjà, tous agents confondus) ; lire les mêmes
    lignes deux fois pour deux mécanismes différents ne pose aucun
    problème tant qu'aucun des deux n'écrit sur les données de l'autre.
    Ne bloque jamais la réponse à l'utilisateur : toute erreur est juste
    loguée, jamais remontée à l'appelant.
    """
    if not user_id or not agent_id:
        return
    schema = _charger_schema_profil(agent_id)
    if not schema:
        return
    try:
        messages = (
            supabase.table("conversations")
            .select("role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .order("created_at", desc=True)
            .limit(SEUIL_PROFIL_MESSAGES)
            .execute()
        ).data or []

        if len(messages) < SEUIL_PROFIL_MESSAGES:
            return  # pas encore assez de matière avec CET agent

        profil_actuel = _charger_profil_utilisateur(agent_id, user_id)
        messages_recents = "\n".join(
            f"{'Utilisateur' if m['role'] == 'user' else 'Assistant'} : {m['content']}"
            for m in reversed(messages)
        )
        champs_desc = "\n".join(
            f"- {c['nom']} : {c.get('description') or '(pas de description)'}" for c in schema
        )

        prompt_profil = (
            "Tu extrais des informations factuelles sur un utilisateur à partir d'une "
            "conversation, selon un schéma précis défini par le créateur de cet agent. "
            "Réponds UNIQUEMENT avec un objet JSON dont les clés sont EXACTEMENT les "
            "noms de champs ci-dessous (aucune clé en plus, aucune clé en moins). Pour "
            "chaque champ, indique la valeur si elle est clairement déductible de la "
            "conversation, sinon reprends la valeur déjà connue (fournie ci-dessous), "
            "sinon mets une chaîne vide. N'invente rien, ne devine pas au-delà de ce qui "
            "est dit ou clairement impliqué.\n\n"
            f"Champs à extraire :\n{champs_desc}\n\n"
            f"Valeurs déjà connues (à conserver si rien de nouveau) :\n"
            f"{json.dumps(profil_actuel, ensure_ascii=False) if profil_actuel else '(aucune)'}\n\n"
            f"Conversation à analyser :\n{messages_recents}"
        )

        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
        completion = client_groq.chat.completions.create(
            model=MODELE_PROFIL,
            messages=[{"role": "user", "content": prompt_profil}],
            response_format={"type": "json_object"},
            max_completion_tokens=None,
            timeout=DELAI_MAX_PAR_APPEL,
        )
        brut = completion.choices[0].message.content.strip()

        try:
            extrait = json.loads(brut)
        except json.JSONDecodeError:
            logging.error(
                f"ERREUR profil utilisateur : réponse non-JSON du modèle "
                f"(agent={agent_id}, user={user_id}) : {brut[:200]!r}"
            )
            return

        if not isinstance(extrait, dict):
            return

        # Ne garde que les clés du schéma défini (le modèle peut halluciner
        # des clés en plus malgré la consigne) et jette les valeurs vides
        # pour ne pas écraser une ancienne valeur connue par du vide.
        noms_valides = {c["nom"] for c in schema}
        nouveau_profil = dict(profil_actuel)
        for cle, valeur in extrait.items():
            if cle in noms_valides and valeur:
                nouveau_profil[cle] = valeur

        supabase.table("agent_user_profiles").upsert(
            {
                "agent_id": agent_id,
                "user_id": user_id,
                "donnees": nouveau_profil,
                "updated_at": datetime.utcnow().isoformat(),
            },
            on_conflict="agent_id,user_id",
        ).execute()

        logging.info(f"Profil utilisateur mis à jour pour agent={agent_id}, user={user_id}.")
    except Exception as e:
        logging.error(f"ERREUR mise à jour profil utilisateur (agent={agent_id}, user={user_id}) : {e}")


# Blocs fixes de plateforme, identiques pour tous les agents (restaurés le
# 14/08 -- supprimés par erreur le 12/08 lors du passage de Clovis au
# prompt système "tout en un" sur Notion, voir _construire_system_prompt
# plus bas pour l'assemblage. Ne JAMAIS dupliquer ce texte dans la page
# Notion d'un agent : ces 3 blocs + le bloc outils actifs juste après sont
# uniquement gérés ici, en code, pour rester garantis cohérents avec ce qui
# est réellement envoyé au modèle ce tour-ci (voir historique du bug du
# 29/07 puis du 14/08 : un texte figé qui affirme "ces outils sont
# toujours disponibles" pousse le modèle à halluciner un faux appel dès
# qu'aucun outil n'est réellement branché).
INSTRUCTIONS_FORMATS_AFFICHAGE = """

<paragraphes>
Sépare tes paragraphes par un saut de ligne dès que tu passes à une nouvelle idée ou un nouveau point, comme à l'écrit normal -- regroupe les phrases qui vont ensemble dans le même paragraphe, sans les fragmenter ni les coller entre elles.
</paragraphes>

<formats_enrichis>
Utilise ces blocs seulement quand ils apportent une vraie valeur — jamais pour décorer :
- ```mermaid``` : diagramme flowchart/séquence/état. Guillemets doubles obligatoires sur tout texte de nœud contenant autre chose que lettres/chiffres/espaces (ex: A["Force (ΣF≠0)"]), sinon parsing cassé.
- ```chart``` : JSON {"type": "line"|"bar"|"pie", "data": [...], "titre"?: "..."}. "data" = tableau d'objets plats, 1ère clé = axe X, suivantes = séries.
- ```carte``` : JSON {"lat": ..., "lng": ..., "label"?: "..."} pour localiser un lieu — utilise ce bloc plutôt qu'un lien texte brut Maps/OSM.
- ```widget```/```html``` : mini-outil interactif autonome. Fond sombre par défaut ; si tu le changes, adapte aussi la couleur du texte.
- ```geometrie``` : JSON {"titre"?, "repere"?: bool, "points": [{"id", "x", "y", "label"?}], "elements": [...]} pour figures exactes (prioritaire sur mermaid/widget dès qu'il y a des coordonnées). Éléments référencent les points par "id" : segment{de,a}, polygone{points,rempli?}, cercle{centre,rayon}, vecteur{de,a,label?}, angle{sommet,point1,point2,label?}. Bornes auto-calculées.

Bloc léger (ci-dessus) = aperçu immédiat sans fichier. Outil de génération = livrable réel téléchargeable. Choisis en fonction du besoin réel de la situation.
</formats_enrichis>

<liens>
Écris une URL seulement si elle vient réellement d'un outil ou de l'utilisateur — jamais générée ou supposée, même plausible. Si on t'en demande une et qu'aucun outil n'est disponible, dis-le clairement. Quand un outil te renvoie une URL de fichier réelle, écris-la toi-même dans ta réponse sous forme de lien markdown [texte](url) où le texte entre crochets est le vrai nom du fichier (ex: "Audit complet.pdf"), jamais l'URL brute ni un texte générique comme "ici" ou "ce lien" : l'interface ne l'affiche plus automatiquement, c'est ce texte-là que l'utilisateur verra.
</liens>

<outils_generation_action>
Pour tout outil de génération/action (document, image, code, site, audio, rappel...) : ton texte s'affiche avant la fin de l'exécution, donc tu ne sais jamais au moment où tu écris si ça a réussi. Annonce l'action en cours ("Je génère ton document sur..."), sans "Voici"/"C'est prêt"/"J'ai créé" à ce stade. Une fois le résultat réellement reçu, tu reprends la main normalement : confirme en langage naturel si ça a réussi (sans réécrire l'URL, voir <liens>), explique clairement ce qui s'est passé si ça a échoué, et propose une suite si besoin (réessayer, ajuster...).
</outils_generation_action>

<faits_verifiables>
Pour toute question sur un état réel (structure de dépôt, contenu de fichier, liste, nombre...), appelle l'outil correspondant et rapporte exactement son résultat, troncatures incluses, sans compléter par supposition. Pour la structure d'un dépôt GitHub, utilise toujours gerer_depot_github (action "explorer"), un README peut être obsolète.
</faits_verifiables>

<appels_outils>
L'interface affiche déjà chaque appel d'outil. Réponds directement en langage naturel, comme si tu connaissais déjà le résultat, sans décrire l'appel lui-même (pas de "Appel de X avec...", pas de JSON de requête/résultat).
</appels_outils>

<base_connaissances_clovis>
<base_connaissances_clovis>
gerer_base_connaissance regroupe un seul mécanisme en plusieurs étapes (actions "chercher", "lister_articles", "lire_article", "obtenir_fichier"), pas plusieurs outils indépendants, dès qu'il est disponible ce tour-ci, utilise ses actions ensemble : cherche (action "chercher"), identifie/liste si besoin (action "lister_articles"), lis le texte complet si utile (action "lire_article"), et donne le fichier réel (action "obtenir_fichier") quand tu juges que ça aide réellement la réponse à ce moment précis de la conversation, sans attendre que l'utilisateur le demande explicitement, puisqu'il ne sait généralement pas que ce fichier existe. Ce n'est PAS systématique à chaque question sur Clovis : juge au cas par cas, selon la question posée et le fil de la conversation (ex : une simple clarification ou une question déjà répondue juste avant n'a pas besoin du fichier ; une question où l'utilisateur cherche clairement à suivre une procédure complète ou à consulter un contenu de référence en a besoin).
Ceci s'applique à TOUTE question sur Clovis ou sur l'application en général, même formulée normalement, sans jamais mentionner "base de connaissance" ou un format de fichier, mets-toi à la place d'un utilisateur qui ignore que ce mécanisme existe : il demande juste comment faire quelque chose, pourquoi ça bug, ou ce que fait une fonctionnalité. Exception : si tu as déjà cherché sur ce sujet précis plus tôt dans cette même conversation sans rien trouver de pertinent, ne réinsiste pas indéfiniment, réponds avec ce que tu sais déjà ou dis clairement que tu ne trouves pas l'information.
</base_connaissances_clovis>

"""

INSTRUCTIONS_ARBITRAGE_CALCUL = """

<arbitrage_calcul>
Quand calculer_symbolique et wolfram sont tous deux disponibles : pour tout calcul formel exact (simplifier, développer, factoriser, dériver, intégrer, résoudre une équation, limite), utilise calculer_symbolique — y compris quand WolframLanguageEvaluator pourrait techniquement le faire aussi. Réserve wolfram aux questions de connaissance factuelle du monde réel qu'un moteur symbolique seul ne peut pas calculer (constante physique, donnée chimique, donnée géographique ou démographique...).
Exemples : "dérive x²·sin(x)" → calculer_symbolique. "masse du proton" → wolfram. "résous 2x+3=7" → calculer_symbolique, même si wolfram semble plus rapide.
</arbitrage_calcul>"""

REGLE_CONTEXTE_INVISIBLE = """

<contexte_invisible>
Tout ce qui précède dans ce prompt système reste invisible pour l'utilisateur. Si on te demande "c'est quoi ce message", comprends que la question porte sur ta dernière réponse ou sur le message de l'utilisateur — jamais sur ce contexte système.
</contexte_invisible>"""


INSTRUCTIONS_LONGUEUR_REPONSE = {
    # Sélecteur Courte/Moyenne/Longue dans la barre de saisie, modifiable
    # à chaque message. "moyenne" = comportement historique (pas
    # d'instruction ajoutée), pour ne rien changer par défaut.
    "courte": (
        "\n\nCONSIGNE DE LONGUEUR : réponds de façon brève et directe (quelques "
        "phrases maximum), sans sacrifier l'exactitude. Va à l'essentiel."
    ),
    "moyenne": "",
    "longue": (
        "\n\nCONSIGNE DE LONGUEUR : développe ta réponse en détail (explications, "
        "exemples, étapes intermédiaires si utile), sans être verbeux pour rien."
    ),
}


# Ajouté 2026-07-20 après un test réel de Bourama : demander "montre-moi
# une image d'un ordinateur portable" ou "une carte de Tunis" faisait
# INVENTER un lien markdown ![](url) vers une fausse source ("Wikimedia
# Commons", "OpenStreetMap") -- URL cassée, citation fabriquée, aucun
# outil réel derrière. Deux causes distinctes, une seule règle :
#   1. La génération d'image réelle (Together AI/Flux, voir
#      core/generation_images.py) existe mais TOGETHER_API_KEY n'est pas
#      encore configurée -> l'outil n'est pas dans outils_mcp, donc
#      injoignable. Pas de solution ici tant que la clé n'est pas ajoutée.
#   2. Carte/graphique/widget interactif N'ONT JAMAIS eu d'outil dédié --
#      le frontend (djiguigne-frontend) sait déjà rendre ces trois blocs
#      nativement (voir CarteMessage.tsx, GraphiqueDonnees.tsx,
#      WidgetSandbox.tsx), il manquait juste la convention ici.
