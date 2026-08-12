"""
Résolution du system_prompt pour les agents à "contenu dynamique par
matière" (agent "Nitrux", 2026-08-06, demande Bourama) -- appelé depuis
core/main.py:_construire_system_prompt à la place de get_system_prompt()
pour les agents marqués `agents.contenu_dynamique_par_matiere = true`.

Principe : le system_prompt de ces agents n'est JAMAIS celui stocké tel
quel sur `agents.system_prompt` (qui sert ici uniquement de repli
généraliste, voir _prompt_generaliste) -- il dépend de l'étudiant ET du
message. Conséquence assumée (Bourama) : ces agents ne profitent jamais
du cache de préfixe Groq (voir l'ordre du prompt dans
_construire_system_prompt côté core/main.py), contrairement aux agents
classiques.

Étapes à chaque message :
1. Charger les rattachements ACTIFS de l'étudiant sur cet agent (aucun
   s'il n'est pas connecté, ou n'a jamais entré de code).
2. Aucun rattachement actif -> repli généraliste directement, pas
   d'appel LLM (rien à choisir).
3. Sinon, un routeur (même pattern que _router_outils dans core/main.py :
   petit modèle Groq, JSON strict) choisit la matière la plus pertinente
   PARMI UNIQUEMENT ces rattachements actifs (jamais une matière que
   l'étudiant n'a pas débloquée). Le routeur peut aussi répondre
   "aucune" si la question ne colle à aucune matière débloquée -> repli
   généraliste dans ce cas aussi.
4. Matière choisie -> system_prompt du contenu correspondant.

Fail-safe : toute erreur (Supabase, Groq, JSON mal formé) retombe sur le
repli généraliste plutôt que de bloquer la réponse.
"""

import json
import logging
import os
import time

from groq import Groq
from supabase import create_client

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

MODELE_ROUTEUR_MATIERE = "llama-3.1-8b-instant"
DELAI_MAX_ROUTEUR = 8  # secondes, même valeur que les autres routeurs rapides

# Cache léger du flag "cet agent a du contenu dynamique par matière ?" --
# évite une requête Supabase à CHAQUE message pour les ~tous les autres
# agents de la plateforme (qui restent sur get_system_prompt() classique).
_cache_flag: dict[str, dict] = {}
_CACHE_FLAG_DUREE = 300  # 5 minutes, même convention que core/configuration.py


def agent_a_contenu_dynamique(agent_id: str) -> bool:
    if not agent_id:
        return False
    entree = _cache_flag.get(agent_id)
    if entree and time.time() - entree["timestamp"] < _CACHE_FLAG_DUREE:
        return entree["valeur"]

    valeur = False
    try:
        res = (
            supabase
            .table("agents")
            .select("contenu_dynamique_par_matiere")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
        valeur = bool(res.data and res.data.get("contenu_dynamique_par_matiere"))
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture flag contenu_dynamique_par_matiere {agent_id}) : {e}")

    _cache_flag[agent_id] = {"valeur": valeur, "timestamp": time.time()}
    return valeur


def _prompt_generaliste(agent_id: str) -> str:
    """Repli : le system_prompt "de base" écrit par le créateur de l'agent
    (Bourama/l'équipe), stocké normalement sur `agents.system_prompt` --
    utilisé tel quel quand l'étudiant n'a aucun rattachement actif, ou que
    sa question ne colle à aucune matière débloquée."""
    try:
        res = (
            supabase
            .table("agents")
            .select("system_prompt")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("system_prompt") or ""
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (repli généraliste, agent {agent_id}) : {e}")
        return ""


def _rattachements_actifs(agent_id: str, user_id: str) -> list[dict]:
    """[{matiere, system_prompt}] pour les rattachements actifs de cet
    étudiant sur cet agent."""
    try:
        res = (
            supabase
            .table("rattachements_par_matiere")
            .select("matiere, contenus_par_matiere(system_prompt)")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", user_id)
            .eq("actif", True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (rattachements actifs {agent_id}/{user_id}) : {e}")
        return []

    resultat = []
    for ligne in res.data or []:
        contenu = ligne.get("contenus_par_matiere") or {}
        if contenu.get("system_prompt"):
            resultat.append({"matiere": ligne["matiere"], "system_prompt": contenu["system_prompt"]})
    return resultat


def _choisir_matiere(message_utilisateur: str, matieres: list[str]) -> str | None:
    """Routeur LLM, même pattern que _router_outils (core/main.py) : ne
    répond jamais à la question, choisit juste une matière parmi celles
    données (ou None si aucune ne colle). Fail-safe : None sur toute
    erreur -> repli généraliste côté appelant."""
    # Même à une seule matière débloquée, on passe quand même par le
    # routeur (pas de repli automatique dessus) : une question hors sujet
    # doit retomber sur le généraliste, jamais être forcée sur la seule
    # matière débloquée par défaut (précision explicite de Bourama).
    catalogue = "\n".join(f"- {m}" for m in matieres)
    prompt_routeur = (
        "Tu es un routeur de matière : tu ne réponds JAMAIS à la question "
        "toi-même, tu choisis seulement à quelle matière ci-dessous elle "
        "correspond le mieux. Si la question ne correspond clairement à "
        "aucune de ces matières (bavardage, question générale, salutation, "
        "autre sujet), réponds \"aucune\" -- ne force jamais une matière par "
        "défaut.\n\n"
        f"Matières disponibles :\n{catalogue}\n\n"
        f"Question de l'étudiant : {message_utilisateur}\n\n"
        "Réponds UNIQUEMENT en JSON strict : "
        '{"matiere": "<un nom EXACTEMENT comme listé ci-dessus, ou \\"aucune\\">"}'
    )

    try:
        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=DELAI_MAX_ROUTEUR)
        completion = client_groq.chat.completions.create(
            model=MODELE_ROUTEUR_MATIERE,
            messages=[{"role": "user", "content": prompt_routeur}],
            response_format={"type": "json_object"},
            max_completion_tokens=100,
            timeout=DELAI_MAX_ROUTEUR,
        )
        brut = completion.choices[0].message.content.strip()
        suggestion = json.loads(brut)
        matiere = suggestion.get("matiere")
        if matiere in matieres:
            return matiere
        return None
    except Exception as e:
        logging.error(f"ERREUR routeur matière : {e}")
        return None


def resoudre_system_prompt(message_utilisateur: str, agent_id: str, user_id: str | None, forcer_generaliste: bool = False) -> str:
    # Bouton "Sans enseignant" (06/08/2026, demande Bourama) : l'étudiant
    # veut une réponse SANS utiliser le contenu d'aucun enseignant pour
    # CE message précis, même s'il a des matières débloquées -- court-
    # circuite tout le reste (pas d'appel au routeur, aucune requête
    # Supabase sur les rattachements) et retombe directement sur le
    # repli généraliste.
    if forcer_generaliste:
        return _prompt_generaliste(agent_id)

    if not user_id:
        return _prompt_generaliste(agent_id)

    actifs = _rattachements_actifs(agent_id, user_id)
    if not actifs:
        return _prompt_generaliste(agent_id)

    matieres = [a["matiere"] for a in actifs]
    matiere_choisie = _choisir_matiere(message_utilisateur or "", matieres) if message_utilisateur else None

    if not matiere_choisie:
        return _prompt_generaliste(agent_id)

    for a in actifs:
        if a["matiere"] == matiere_choisie:
            return a["system_prompt"]
    return _prompt_generaliste(agent_id)
