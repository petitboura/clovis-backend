"""
Section "Mes comportements" -- l'étudiant peut enregistrer PLUSIEURS
instructions perso (2026-08-06, demande Bourama : "on peut en mettre
plusieurs hein, pas juste un"), pas un seul texte fourre-tout. Chacune
s'applique EN PLUS du system_prompt déjà résolu (généraliste, matière
d'un enseignant, ou "sans enseignant"), quel que soit l'agent -- pas
seulement les agents à contenu dynamique par matière.

Mécanisme "à la skill" (13/08/2026, demande Bourama) : chaque
comportement a maintenant une DESCRIPTION courte (générée
automatiquement à partir du texte, jamais saisie par l'étudiant) en plus
du TEXTE long. Le texte complet n'est plus jamais injecté d'office --
un petit routeur (même modèle/pattern que _router_outils dans
core/main.py, voir choisir_comportements_pertinents ci-dessous) décide,
à chaque message, quels comportements (id + description SEULEMENT) sont
des candidats plausibles. Ces candidats sont annoncés au grand modèle
comme un outil disponible (consulter_comportement, voir
core/serveur_mcp_generation.py) -- c'est le grand modèle, jamais ce
fichier, qui décide en dernier ressort s'il va lire le texte complet.

Voir l'injection dans core/main.py::_construire_system_prompt et les
endpoints dans api/comportements_etudiants.py.
"""

import json
import logging
import os

from groq import Groq
from supabase import create_client


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

# Même petit modèle rapide que le routeur d'outils existant (voir
# MODELE_ROUTEUR_OUTILS dans core/main.py) -- pas de raison d'en
# introduire un deuxième pour un rôle équivalent.
MODELE_PETIT = "llama-3.1-8b-instant"


def _generer_description(texte: str) -> str:
    """
    Petit appel LLM qui résume `texte` (le comportement long écrit par
    l'étudiant) en une description courte -- SEULE chose montrée au
    routeur et au grand modèle avant qu'il ne décide de lire le texte
    complet via l'outil consulter_comportement. Fail-safe (13/08) : si
    l'appel échoue, on retombe sur une simple troncature plutôt que de
    bloquer la création/modification du comportement -- une description
    imparfaite vaut mieux qu'un enregistrement qui échoue.
    """
    repli = texte if len(texte) <= 120 else texte[:117] + "..."
    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=10.0)
        completion = client.chat.completions.create(
            model=MODELE_PETIT,
            messages=[{
                "role": "user",
                "content": (
                    "Résume l'instruction personnelle suivante en UNE phrase courte "
                    "(moins de 15 mots), qui servira à décider plus tard si cette "
                    "instruction s'applique à une situation donnée -- sois concret et "
                    "spécifique, jamais vague. Réponds UNIQUEMENT avec la phrase, "
                    "rien d'autre autour.\n\n"
                    f"Instruction :\n{texte}"
                ),
            }],
            max_completion_tokens=60,
            timeout=10.0,
        )
        description = (completion.choices[0].message.content or "").strip().strip('"')
        return description or repli
    except Exception as e:
        logging.error(f"ERREUR génération description comportement : {e}")
        return repli


def lister_comportements(agent_id: str, etudiant_id: str) -> list[dict]:
    """Liste ordonnée (plus ancien -> plus récent) des instructions
    perso de cet étudiant pour cet agent. Liste vide si rien
    d'enregistré -- jamais None, pour simplifier les appelants (endpoint
    GET, petit routeur, et injection prompt)."""
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("id, texte, description")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportements {agent_id}/{etudiant_id}) : {e}")
        return []
    return [
        {"id": ligne["id"], "texte": ligne["texte"], "description": ligne.get("description") or ""}
        for ligne in (res.data or [])
        if ligne.get("texte", "").strip()
    ]


def obtenir_comportement_texte(agent_id: str, etudiant_id: str, comportement_id: str) -> str | None:
    """
    Texte complet d'UN comportement précis, vérifié comme appartenant
    bien à cet (agent_id, etudiant_id) -- utilisé par l'outil
    consulter_comportement (core/serveur_mcp_generation.py) quand le
    grand modèle décide de le lire en entier. None si introuvable ou
    n'appartenant pas à cette paire (jamais une fuite entre étudiants).
    """
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("texte")
            .eq("id", comportement_id)
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture texte comportement {comportement_id}) : {e}")
        return None
    if not res.data:
        return None
    return res.data.get("texte")


def choisir_comportements_pertinents(message_utilisateur: str, comportements: list[dict]) -> list[dict]:
    """
    Petit routeur (même modèle/pattern que _router_outils dans
    core/main.py) : reçoit le message de l'utilisateur + TOUS les
    comportements de cet étudiant pour cet agent ({id, texte,
    description}), renvoie le sous-ensemble des candidats plausibles --
    id + description SEULEMENT, jamais le texte long (c'est au grand
    modèle de le demander via l'outil consulter_comportement s'il le
    juge utile). Fail-safe strict, comme _router_outils : toute erreur
    renvoie une liste vide plutôt que de bloquer la réponse normale.
    """
    if not comportements or not message_utilisateur:
        return []

    catalogue = "\n".join(f"- {c['id']} : {c['description']}" for c in comportements if c.get("description"))
    if not catalogue:
        return []

    prompt_routeur = (
        "Tu es un routeur : tu ne réponds JAMAIS au message toi-même, tu décides "
        "seulement quelles instructions personnelles (parmi la liste ci-dessous, "
        "écrites par l'étudiant lui-même) pourraient s'appliquer à ce message. Si "
        "aucune n'est pertinente (message général, salutation, sujet sans rapport), "
        "renvoie une liste vide -- ne force jamais une instruction par défaut ni "
        "\"au cas où\". Sois large plutôt que restrictif : en cas de doute, inclut "
        "le candidat, c'est le modèle principal qui tranchera ensuite s'il lit le "
        "texte complet ou non.\n\n"
        f"Instructions personnelles disponibles :\n{catalogue}\n\n"
        f"Message de l'utilisateur : {message_utilisateur}\n\n"
        "Réponds UNIQUEMENT avec un objet JSON de la forme "
        '{"ids": ["id_1", "id_2"]} (ids EXACTEMENT comme listés ci-dessus, liste '
        "vide si rien n'est pertinent)."
    )

    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=10.0)
        completion = client.chat.completions.create(
            model=MODELE_PETIT,
            messages=[{"role": "user", "content": prompt_routeur}],
            response_format={"type": "json_object"},
            max_completion_tokens=200,
            timeout=10.0,
        )
        brut = completion.choices[0].message.content.strip()
        suggestion = json.loads(brut)
        ids_valides = {c["id"] for c in comportements}
        ids_retenus = [i for i in suggestion.get("ids", []) if i in ids_valides]
        logging.info(f"Routeur de comportements -> retenus : {ids_retenus or '(aucun)'}")
        return [c for c in comportements if c["id"] in ids_retenus]
    except Exception as e:
        logging.error(f"ERREUR routeur comportements : {e}")
        return []


def ajouter_comportement(agent_id: str, etudiant_id: str, texte: str) -> dict:
    texte = texte.strip()
    description = _generer_description(texte)
    res = (
        supabase.table("comportements_etudiants")
        .insert({"agent_id": agent_id, "etudiant_id": etudiant_id, "texte": texte, "description": description})
        .execute()
    )
    ligne = res.data[0]
    return {"id": ligne["id"], "texte": ligne["texte"], "description": ligne.get("description") or ""}


def modifier_comportement(agent_id: str, etudiant_id: str, comportement_id: str, texte: str) -> dict | None:
    texte = texte.strip()
    description = _generer_description(texte)
    res = (
        supabase.table("comportements_etudiants")
        .update({"texte": texte, "description": description})
        .eq("id", comportement_id)
        .eq("agent_id", agent_id)
        .eq("etudiant_id", etudiant_id)
        .execute()
    )
    if not res.data:
        return None
    ligne = res.data[0]
    return {"id": ligne["id"], "texte": ligne["texte"], "description": ligne.get("description") or ""}


def supprimer_comportement(agent_id: str, etudiant_id: str, comportement_id: str) -> bool:
    res = (
        supabase.table("comportements_etudiants")
        .delete()
        .eq("id", comportement_id)
        .eq("agent_id", agent_id)
        .eq("etudiant_id", etudiant_id)
        .execute()
    )
    return bool(res.data)
