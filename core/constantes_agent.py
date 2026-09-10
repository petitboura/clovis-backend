# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs, main.py faisait 3984 lignes). Ce fichier regroupe uniquement
# les constantes partagees (modeles, seuils, politique de moderation) et le
# client Supabase, pour que tous les autres modules issus du decoupage de
# main.py (et main.py lui-meme) les importent depuis un seul endroit plutot
# que de dupliquer/redevenir circulaires entre eux.
import os
from supabase import create_client, ClientOptions
from client_http_supabase import nouveau_client_http_supabase

def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET, options=ClientOptions(httpx_client=nouveau_client_http_supabase()))

# 07/09/2026, demande Bourama : DeepSeek en position 1 de la cascade
# (avant GROQ_PRIMARY) -- decision explicite : V4 Flash fixe pour
# l'instant (pas de repli V4 Pro pour l'instant). API compatible OpenAI
# (tools/tool_calls), meme format que Groq -- verifie sur la doc
# officielle du fournisseur, a confirmer en conditions reelles. Cle API
# absente (DEEPSEEK_API_KEY) = fournisseur invisible, meme gating
# silencieux que fournisseurs_llm.py -- voir main.py.
#
# Grok (xAI) envisage un temps a la position 1 (avant DeepSeek) le meme
# jour, abandonne : plus de tier gratuit chez xAI depuis mai 2025,
# facturation des le premier jour -- ne correspond plus au budget vise.
# Aucune trace de code laissee (retire de main.py le jour meme).
DEEPSEEK_PRIMARY = "deepseek-v4-flash"

GROQ_PRIMARY = "openai/gpt-oss-120b"
GOOGLE_MODEL = "gemini-2.5-flash"
GROQ_FALLBACKS = [
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-20b",
    # llama-4-scout-17b-16e-instruct et qwen3-32b retires par Groq le
    # 17/06/2026 (voir console.groq.com/docs/deprecations) -- 404
    # systematique, retires de la chaine le 26/07/2026.
    #
    # 17/08 : llama-3.3-70b-versatile (1er maillon) et llama-3.1-8b-instant
    # (dernier recours Groq avant Gemini) retires a leur tour -- 404
    # "model_not_found" constate en prod sur les deux. Coherence avec
    # l'annonce Groq du 17/06/2026 : shutdown officiel de ces deux modeles
    # au 16/08/2026 (voir console.groq.com/docs/deprecations), la veille
    # de ce correctif. Pas de remplacement 1:1 ajoute pour le dernier
    # recours -- openai/gpt-oss-20b (deja present juste au-dessus) ferme
    # desormais la cascade Groq avant Gemini. A REVERIFIER : Bourama n'a
    # pas encore confirme via console.groq.com/dashboard/limits si ces
    # deux modeles sont bien morts pour sa cle precise (la doc publique
    # Groq les liste encore comme "Production Models" au moment de ce
    # correctif, contrairement aux 404 observes en prod).
]

# Modeles de secours dont la qualite de reponse est nettement en retrait par
# rapport a GROQ_PRIMARY (utilises seulement quand tout le reste a echoue) --
# quand un de ces modeles repond, on le signale explicitement a l'utilisateur
# (evenement "meta", voir _agent_groq et le frontend) plutot que de laisser
# une reponse plus faible passer pour une reponse normale de la plateforme.
MODELES_QUALITE_REDUITE = {"llama-3.1-8b-instant"}
MESSAGE_ERREUR = "Désolé, je rencontre un souci technique pour répondre. Merci de réessayer dans un instant."

# Modération d'entrée (25/07) : verifie le message BRUT de l'utilisateur avant
# tout le reste. IMPORTANT : Llama Guard 4 (meta-llama/llama-guard-4-12b)
# a ete retire par Groq (deprecation du 10/02/2026, voir
# console.groq.com/docs/deprecations) -- constate en prod le 25/07 (fail-
# open, donc rien n'etait bloque depuis le debut, gpt-oss-120b refusait
# parfois tout seul en anglais sur son propre entrainement de securite,
# d'ou la confusion initiale). Remplace par openai/gpt-oss-safeguard-20b,
# le modele recommande par Groq -- fonctionne differemment : "bring your
# own policy", on lui fournit notre propre politique de moderation
# (POLITIQUE_MODERATION plus bas) en role "system", et il repond en JSON
# structure {"violation": 0|1, "category": "...", "rationale": "..."} au
# lieu du simple "safe"/"unsafe" de Llama Guard (voir
# console.groq.com/docs/content-moderation). Demande Bourama (25/07) :
# uniquement l'entree pour l'instant (pas la sortie), pour limiter le
# surcout en tokens.
# Flag desactive le 2026-09-05 (demande Bourama) : la moderation d'entree
# bloquait des messages a tort. Le code de moderation (fonction
# _verifier_message_utilisateur, POLITIQUE_MODERATION ci-dessous, et les
# deux points de verification PERF plus bas dans cette fonction) est
# intact -- il suffit de remettre ce flag a True pour la reactiver, rien
# d'autre a toucher.
MODERATION_ENTREE_ACTIVE = False

MODELE_MODERATION = "openai/gpt-oss-safeguard-20b"
POLITIQUE_MODERATION = """# Politique de modération -- messages d'étudiants vers un assistant IA éducatif

## Catégories de violation
- violence : instructions ou encouragement à la violence, fabrication d'armes ou d'explosifs.
- haine : contenu haineux ou discriminatoire visant un groupe protégé.
- sexuel : contenu sexuel explicite, ou impliquant des mineurs sous quelque forme que ce soit.
- automutilation : encouragement ou instructions de suicide/automutilation.
- illegal : instructions pour des activités clairement illégales (drogues dures, piratage malveillant, fraude...).
- harcelement : insultes graves ou harcèlement ciblé envers une personne précise.

## Ce qui N'EST PAS une violation (à laisser passer)
- Questions scolaires/académiques, même sur des sujets sensibles en soi (histoire des guerres, chimie de base, biologie, philosophie...).
- Langage familier, frustration ou grossièretés légères sans intention de nuire à quelqu'un.
- Demandes créatives ou hypothétiques clairement encadrées (devoirs, fiction, débat argumenté).
- Un vrai JSON ou du code demandé explicitement par l'étudiant.

## Format de réponse (JSON uniquement, rien d'autre)
{"violation": 0 ou 1, "category": "<une des catégories ci-dessus ou null si aucune>", "rationale": "<explication en une phrase>"}
"""
MESSAGE_CONTENU_BLOQUE = "Je ne peux pas répondre à ce message. Reformule ta question autrement, je suis là pour t'aider !"

# Valeur de repli si le secret AGENT_ID n'est pas defini pour ce deploiement
# (doit rester alignee avec AGENT_ID_PAR_DEFAUT dans retriever.py).
AGENT_ID_PAR_DEFAUT = "clovis"  # 12/08 : ce depot isole ne sert plus que Clovis

# Au-dela de ce nombre de messages non resumes (table conversations), on
# redemande un resume condense au modele plutot que d'empiler indefiniment
# l'historique brut dans conversation_summaries.
SEUIL_RESUME_MESSAGES = 20
MODELE_RESUME = "openai/gpt-oss-20b"  # 17/08 : llama-3.1-8b-instant decommissionne par Groq (404 en prod) -- quota TPM separe de la cascade principale, evite la contention

# Profil utilisateur dynamique par agent (2026-07-21, voir
# agents.profil_utilisateur_schema et _mettre_a_jour_profil_utilisateur_si_besoin
# plus bas). Seuil plus bas que SEUIL_RESUME_MESSAGES : contrairement au
# resume memoire (qui compte TOUS les messages de l'utilisateur, tous agents
# confondus), celui-ci compte seulement les messages avec CET agent -- ils
# s'accumulent donc plus lentement, un seuil identique mettrait
# potentiellement des semaines a se declencher pour un agent utilise
# occasionnellement.
SEUIL_PROFIL_MESSAGES = 10
MODELE_PROFIL = "openai/gpt-oss-20b"  # 17/08 : llama-3.1-8b-instant decommissionne par Groq (404 en prod) -- meme raison que MODELE_RESUME : quota TPM separe

# Routeur d'outils (2026-07-28, demande Bourama) : premier appel LLM
# séparé, rapide, qui juge quels outils seraient pertinents pour la
# question -- voir _router_outils plus bas. Tâche de classification
# simple (pas besoin de raisonnement) -- un petit modèle rapide et open
# source plutôt que MODELE_PROFIL/MODELE_RESUME (llama-3.3-70b-versatile).
#
# 06/09/2026, demande Bourama : passe de openai/gpt-oss-20b a
# groq/compound-mini comme modele PRINCIPAL -- 70 000 TPM sur le tier
# gratuit contre 8 000 pour gpt-oss-20b, marge bien plus confortable pour
# ce routeur appele a chaque message. ATTENTION, compound-mini n'est pas
# un modele de chat classique -- c'est un systeme agentique qui peut
# decider tout seul d'appeler un outil integre (recherche web, code).
# Deux consequences geree dans _router_outils :
# 1) reasoning_effort n'est documente que pour GPT-OSS/Qwen3, pas pour
#    compound -- ne JAMAIS le lui envoyer (voir MODELES_AVEC_REASONING_EFFORT
#    plus bas, qui ne le liste pas expres).
# 2) response_format=json_object est incompatible avec l'appel d'outils
#    cote Groq -- il faut donc desactiver ses outils integres via
#    compound_custom.tools.enabled_tools=[], sinon risque d'erreur ou de
#    vraie recherche web declenchee pour un simple routage (lent, hors
#    sujet).
# Pas encore verifie en conditions reelles (pas d'acces reseau direct a
# l'API Groq depuis l'environnement de dev) -- a surveiller dans les logs
# Railway ("Routeur d'outils -> suggérés"), avec repli automatique sur
# gpt-oss-20b (MODELE_ROUTEUR_OUTILS_REPLI) en cas d'echec.
MODELE_ROUTEUR_OUTILS = "groq/compound-mini"

# Repli du routeur d'outils (06/09/2026, demande Bourama) : si
# MODELE_ROUTEUR_OUTILS (compound-mini) echoue, on retombe sur
# openai/gpt-oss-20b -- moins de marge TPM (8 000 contre 70 000) mais
# modele de raisonnement classique deja eprouve en prod depuis le 17/08
# (voir l'historique ci-dessus : gemma2-9b-it decommissionne, remplace
# par gpt-oss-20b).
MODELE_ROUTEUR_OUTILS_REPLI = "openai/gpt-oss-20b"

# DESACTIVE (10/09/2026, demande Bourama) : le routeur automatique
# (_router_outils, appel LLM separe avant chaque reponse pour suggerer des
# outils) fait perdre du temps sur chaque message et fait double emploi
# avec demander_outils (voir routage_outils.py), desormais toujours
# propose au grand modele -- celui-ci peut demander lui-meme un outil des
# qu'il en a besoin, sans routeur prealable. Code du routeur garde tel
# quel (voir routage_outils.py), simplement plus jamais appele -- flag a
# repasser a False pour le reactiver.
ROUTEUR_OUTILS_AUTO_DESACTIVE = True

# D'apres la doc Groq (console.groq.com/docs/reasoning), le parametre
# reasoning_effort n'est reconnu que par certains modeles (GPT-OSS 20B/120B,
# Qwen 3). Les autres modeles de GROQ_FALLBACKS (ex: llama-3.3-70b-versatile,
# llama-3.1-8b-instant) ne sont PAS des modeles de raisonnement : leur
# envoyer ce parametre risque une erreur API plutot qu'un simple no-op. On
# ne l'active donc que pour les modeles confirmes compatibles -- ET la
# valeur qui desactive/minimise le raisonnement DIFFERE selon la famille :
# "none" pour Qwen 3 (raisonnement desactivable), mais GPT-OSS exige
# obligatoirement low/medium/high (pas de "none") -- bug reel trouve le
# 26/07/2026 : gpt-oss-20b recevait "none" et echouait a CHAQUE appel avec
# une erreur 400 "`reasoning_effort` must be one of `low`, `medium`, or
# `high`", le rendant inutilisable comme filet de secours depuis le debut.
MODELES_AVEC_REASONING_EFFORT = {
    "openai/gpt-oss-20b": "low",       # pas de "none" chez GPT-OSS -- "low" pour rester rapide
    "openai/gpt-oss-120b": "low",
    "qwen/qwen3.6-27b": "none",        # Qwen 3 peut vraiment desactiver le raisonnement
}

# ANCIEN : MAX_ETAPES_OUTILS = 5, un plafond fixe d'aller-retours "outil"
# par question, pour eviter qu'un modele ne boucle indefiniment sur le
# meme outil -- remplace le 02/09/2026 (demande Bourama : un usage
# legitime a plus de 5 etapes se faisait couper sans raison) par un
# budget dynamique lu depuis parametres_outils() (table Supabase
# parametres_outils, voir mcp_tools.py) + une vraie detection de
# repetition (meme appel refait plusieurs fois d'affilee) plutot qu'un
# simple compteur brut. Voir _agent_groq ci-dessous.

# Deplacees ici le 05/09/2026 (correctif) : etaient definies dans
# construction_system_prompt.py mais utilisees aussi dans profils_agents.py,
# routage_outils.py, persistance_echanges.py et boucle_agent.py -- rester
# la-bas aurait cree un import circulaire (construction_system_prompt.py
# importe deja profils_agents.py). constantes_agent.py est le seul module
# sans dependance vers aucun des autres, donc le seul endroit sur qui tout
# le monde peut s'appuyer sans cycle.
DELAI_MAX_PAR_APPEL = 10  # secondes : on bascule vite plutot que d'attendre
MAX_PASSAGES_CASCADE = 2  # on ne retente toute la cascade que si TOUT a timeout

