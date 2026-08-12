"""
Fournisseurs LLM premium (Claude, GPT, Gemini, DeepSeek) -- gardes-fous
"abonnement" par agent, distincts de la cascade Groq/Gemini-vision de
main.py qui reste le comportement PAR DEFAUT (voir GROQ_PRIMARY).

Contexte (voir page Notion "Pricing -- Agent Maths", 01/08/2026) : un agent
peut debloquer un `distributeur` (fournisseur) + un `palier` (qualite de
modele). La hierarchie des distributeurs est CUMULATIVE DESCENDANTE :

    claude > gpt > gemini > deepseek

debloquer "claude" donne aussi acces a gpt/gemini/deepseek. Le palier est
lui aussi cumulatif ascendant (pro >= avance >= essentiel) : un agent en
"pro" peut choisir un modele "essentiel" du meme distributeur si l'usager
veut une reponse plus rapide/moins chere.

IMPORTANT -- ces identifiants de modele bougent vite (nouvelles versions
tous les 1-2 mois chez chaque fournisseur). Modifier UNIQUEMENT
HIERARCHIE_MODELES ci-dessous quand un fournisseur sort un nouveau modele,
rien d'autre a toucher dans ce fichier ni dans main.py.

LIMITE CONNUE (v1, 02/08/2026) : ces fournisseurs premium repondent en
TEXTE SEUL, sans les outils MCP (Wolfram, RAG, Notion, recherche web...).
Meme limitation que le chemin Gemini-vision existant dans main.py (voir
_construire_parts_gemini) -- brancher les outils sur les 3 nouveaux SDKs
(formats de tool-calling tous differents) est un chantier a part, pas
fait ici. A previlegier pour la v2 si Bourama le confirme.
"""

import os
import logging

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    return os.environ.get(key)


# Ordre = hierarchie cumulative descendante (voir docstring plus haut).
ORDRE_DISTRIBUTEURS = ["claude", "gpt", "gemini", "deepseek"]

# Ordre = hierarchie cumulative ascendante.
ORDRE_PALIERS = ["essentiel", "avance", "pro"]

# palier "essentiel"/"avance"/"pro" (nom commercial, cote agents/pricing)
# -> mappe sur le palier de qualite "simple"/"parfait"/"pro" de la page
# Notion section 2 (nom technique, cote choix du modele). Meme hierarchie
# a 3 niveaux, deux vocabulaires differents selon le contexte (vente vs
# ingenierie) -- fusionnes ici pour eviter un 3e mapping inutile.
HIERARCHIE_MODELES = {
    "claude": {
        "essentiel": {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
        "avance": {"id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
        # "Claude Opus 5" (page Notion) n'existe pas encore cote Anthropic
        # au moment d'ecrire ceci -- Opus 4.8 est le dernier Opus reel,
        # a corriger ici des la sortie d'un vrai "Opus 5".
        "pro": {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
    },
    "gpt": {
        "essentiel": {"id": "gpt-5-nano", "label": "GPT-5 nano"},
        "avance": {"id": "gpt-5.4", "label": "GPT-5.4"},
        "pro": {"id": "gpt-5.5", "label": "GPT-5.5"},
    },
    "gemini": {
        "essentiel": {"id": "gemini-3.5-flash-lite", "label": "Gemini 3.5 Flash-Lite"},
        "avance": {"id": "gemini-3.6-flash", "label": "Gemini 3.6 Flash"},
        "pro": {"id": "gemini-3.1-pro", "label": "Gemini 3.1 Pro"},
    },
    "deepseek": {
        # L'API DeepSeek n'expose que 2 modeles stables a la fois
        # (aujourd'hui deepseek-v4-flash / deepseek-v4-pro), pas de nom
        # par version -- contrairement aux 3 paliers distincts vendus par
        # les autres fournisseurs. essentiel+avance partagent donc le
        # meme modele (flash), pro passe sur le plus gros (pro).
        # MAJ 02/08/2026 : les anciens alias deepseek-chat/deepseek-
        # reasoner (utilises jusqu'ici) ont ete retires par DeepSeek le
        # 24/07/2026 (15:59 UTC) -- remplaces ici par les noms actuels.
        # Bug decouvert en verifiant les prix pour Bourama : nos appels
        # DeepSeek echouaient probablement en silence depuis le 24/07.
        "essentiel": {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        "avance": {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
        "pro": {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
    },
}

# Gating pattern (voir principe etabli sur les autres fonctionnalites de
# la plateforme) : un fournisseur sans cle API configuree disparait
# silencieusement de la liste, jamais d'erreur visible pour l'usager.
VARIABLES_CLE_PAR_DISTRIBUTEUR = {
    "claude": "ANTHROPIC_API_KEY",
    "gpt": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",  # reprend la cle Gemini deja utilisee par main.py si presente
    "deepseek": "DEEPSEEK_API_KEY",
}


def _distributeur_disponible(distributeur):
    variable = VARIABLES_CLE_PAR_DISTRIBUTEUR.get(distributeur)
    return bool(variable and get_secret(variable))


def modeles_disponibles_pour_agent(distributeur_debloque, palier_debloque):
    """
    Calcule la liste des modeles reellement choisissables pour un agent,
    a partir de son distributeur/palier debloques (colonnes `agents`,
    voir api/agents.py). Renvoie [] si distributeur_debloque est None/
    "aucun" -- dans ce cas le frontend n'affiche AUCUN selecteur, l'agent
    tourne sur la cascade Groq habituelle comme avant cette feature.

    Chaque element : {"modele_id", "label", "distributeur", "palier"}.
    Cumulatif sur les deux axes (voir docstring du module) ET filtre par
    gating pattern (cle API absente = fournisseur invisible), donc un
    agent peut tres bien avoir un distributeur_debloque="claude" mais ne
    voir apparaitre que gemini/deepseek si ANTHROPIC_API_KEY/OPENAI_API_KEY
    manquent encore sur Railway.
    """
    if not distributeur_debloque or distributeur_debloque == "aucun":
        return []
    if distributeur_debloque not in ORDRE_DISTRIBUTEURS:
        logging.error(f"distributeur_debloque inconnu : {distributeur_debloque}")
        return []
    if palier_debloque not in ORDRE_PALIERS:
        palier_debloque = "essentiel"

    index_distributeur = ORDRE_DISTRIBUTEURS.index(distributeur_debloque)
    # BUG CORRIGE (02/08/2026, repere par Bourama : seul Claude
    # apparaissait pour un agent debloque en "claude") -- claude est en
    # TETE de ORDRE_DISTRIBUTEURS (index 0) et doit debloquer tout ce qui
    # suit (gpt/gemini/deepseek), donc la tranche va de cet index
    # JUSQU'A LA FIN de la liste, pas du debut jusqu'a cet index (l'ancien
    # ORDRE_DISTRIBUTEURS[:index+1] faisait l'inverse : "claude" ->
    # uniquement ["claude"], jamais rien en dessous).
    distributeurs_autorises = ORDRE_DISTRIBUTEURS[index_distributeur:]

    index_palier = ORDRE_PALIERS.index(palier_debloque)
    paliers_autorises = ORDRE_PALIERS[: index_palier + 1]

    resultats = []
    ids_deja_vus = set()
    for distributeur in distributeurs_autorises:
        if not _distributeur_disponible(distributeur):
            continue
        for palier in paliers_autorises:
            info = HIERARCHIE_MODELES[distributeur][palier]
            # Dedoublonnage par modele_id (cas DeepSeek, seul fournisseur
            # ou 2 paliers partagent le meme modele_id -- voir
            # HIERARCHIE_MODELES : essentiel et avance valent tous les
            # deux "deepseek-v4-flash" -- sans ca "DeepSeek V4 Flash"
            # apparaissait deux fois d'affilee dans la liste, repere par
            # Bourama sur la capture d'ecran de Nucleos).
            if info["id"] in ids_deja_vus:
                continue
            ids_deja_vus.add(info["id"])
            resultats.append({
                "modele_id": info["id"],
                "label": info["label"],
                "distributeur": distributeur,
                "palier": palier,
            })
    return resultats


def modele_id_est_autorise(modele_id, distributeur_debloque, palier_debloque):
    """
    Verification cote backend AVANT d'honorer un choix de modele envoye
    par le frontend (jamais fait confiance a un id de modele venu du
    client sans le revalider contre ce que l'agent a reellement
    debloque) -- voir api/chat.py et api/agents.py.
    """
    disponibles = modeles_disponibles_pour_agent(distributeur_debloque, palier_debloque)
    return any(m["modele_id"] == modele_id for m in disponibles)


def distributeur_pour_modele_id(modele_id):
    """Retrouve le nom du distributeur a partir d'un modele_id, pour
    savoir quel SDK appeler dans generer_reponse_premium ci-dessous."""
    for distributeur, paliers in HIERARCHIE_MODELES.items():
        for info in paliers.values():
            if info["id"] == modele_id:
                return distributeur
    return None


def _stream_claude(modele_id, system_prompt, messages):
    from anthropic import Anthropic
    client = Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))
    with client.messages.stream(
        model=modele_id,
        max_tokens=4096,
        system=system_prompt or "",
        messages=messages,
    ) as stream:
        for texte in stream.text_stream:
            yield texte


def _stream_gpt(modele_id, system_prompt, messages):
    from openai import OpenAI
    client = OpenAI(api_key=get_secret("OPENAI_API_KEY"))
    messages_openai = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages
    flux = client.chat.completions.create(
        model=modele_id,
        messages=messages_openai,
        stream=True,
    )
    for morceau in flux:
        delta = morceau.choices[0].delta.content if morceau.choices else None
        if delta:
            yield delta


def _stream_deepseek(modele_id, system_prompt, messages):
    # DeepSeek : API compatible OpenAI, seule la base_url change (voir
    # docstring du module -- deepseek-v4-flash / deepseek-v4-pro).
    from openai import OpenAI
    client = OpenAI(api_key=get_secret("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")
    messages_openai = ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages
    flux = client.chat.completions.create(
        model=modele_id,
        messages=messages_openai,
        stream=True,
    )
    for morceau in flux:
        delta = morceau.choices[0].delta.content if morceau.choices else None
        if delta:
            yield delta


def _stream_gemini(modele_id, system_prompt, messages):
    # Reutilise le client google-genai deja initialise ailleurs dans le
    # projet (voir core/main.py) -- instancie ici localement pour garder
    # ce module independant, cout negligeable (pas d'appel reseau a la
    # construction du client).
    from google import genai
    client = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
    contenu = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contenu.append({"role": role, "parts": [{"text": m["content"]}]})
    flux = client.models.generate_content_stream(
        model=modele_id,
        contents=contenu,
        config={"system_instruction": system_prompt} if system_prompt else None,
    )
    for morceau in flux:
        if morceau.text:
            yield morceau.text


_STREAMERS_PAR_DISTRIBUTEUR = {
    "claude": _stream_claude,
    "gpt": _stream_gpt,
    "deepseek": _stream_deepseek,
    "gemini": _stream_gemini,
}


def generer_reponse_premium(modele_id, system_prompt, messages):
    """
    Generateur de texte (morceaux de reponse, pas d'evenements structures
    -- voir LIMITE CONNUE en tete de fichier) pour un modele premium deja
    valide par modele_id_est_autorise(). `messages` : liste de
    {"role": "user"|"assistant", "content": "..."}, format deja utilise
    cote main.py pour l'historique. Leve l'exception du SDK sous-jacent
    telle quelle si l'appel echoue -- a l'appelant (main.py) de decider
    du repli (ex: retomber sur la cascade Groq).
    """
    distributeur = distributeur_pour_modele_id(modele_id)
    if distributeur is None:
        raise ValueError(f"modele_id inconnu : {modele_id}")
    streamer = _STREAMERS_PAR_DISTRIBUTEUR[distributeur]
    yield from streamer(modele_id, system_prompt, messages)
