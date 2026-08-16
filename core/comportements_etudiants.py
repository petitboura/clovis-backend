"""
Section "Mes comportements" -- l'étudiant peut enregistrer PLUSIEURS
instructions perso (2026-08-06, demande Bourama : "on peut en mettre
plusieurs hein, pas juste un"), pas un seul texte fourre-tout. Chacune
s'applique EN PLUS du system_prompt déjà résolu (généraliste, matière
d'un enseignant, ou "sans enseignant"), quel que soit l'agent -- pas
seulement les agents à contenu dynamique par matière.

Mécanisme "à la skill" (13/08/2026, demande Bourama), devenu un VRAI
skill Claude (16/08/2026, demande Bourama : "exactement un skill claude,
aucune différence") : chaque comportement a une DESCRIPTION courte
(générée automatiquement, jamais saisie par l'étudiant) ET un skill
complet au format SKILL.md (frontmatter name/description + corps
d'instructions markdown, voir _generer_skill ci-dessous -- même méthode
que le skill "skill-creator" d'Anthropic). Le texte brut de l'étudiant
n'est jamais injecté d'office -- un petit routeur (même modèle/pattern
que _router_outils dans core/main.py, voir choisir_comportements_pertinents
ci-dessous) décide, à chaque message, quels comportements (id +
description SEULEMENT) sont des candidats plausibles. Ces candidats sont
annoncés au grand modèle comme un outil disponible (consulter_comportement,
voir core/serveur_mcp_generation.py) -- c'est le grand modèle, jamais ce
fichier, qui décide en dernier ressort s'il va lire le skill complet.

Ce même mécanisme est repris à l'identique dans core/codes_partage.py
pour les comportements partagés par code (établissement/enseignant vers
étudiant) -- toute autre section qui écrit ou affiche un comportement
doit, de la même façon, produire un vrai skill via _generer_skill, jamais
retomber sur du texte brut.

Voir l'injection dans core/main.py::_construire_system_prompt et les
endpoints dans api/comportements_etudiants.py.
"""

import json
import logging
import os
import re
import unicodedata

from groq import Groq
from supabase import create_client


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

# Même petit modèle rapide que le routeur d'outils existant (voir
# MODELE_ROUTEUR_OUTILS dans core/main.py) -- utilisé seulement pour le
# routeur ci-dessous (choisir_comportements_pertinents), pas pour la
# génération du skill (voir MODELE_SKILL, plus costaud, plus bas).
MODELE_PETIT = "llama-3.1-8b-instant"

# Même modèle "costaud" que le modèle principal de la cascade de chat
# (GROQ_PRIMARY, core/main.py) -- écrire un skill complet (16/08/2026,
# demande Bourama : "un modèle costaud") est un travail plus lourd qu'un
# résumé d'une phrase, pas de raison de se limiter à MODELE_PETIT ici.
MODELE_SKILL = "openai/gpt-oss-120b"

_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _slugifier(texte: str) -> str:
    """Identifiant court en minuscules, tirets, sans accents -- même
    convention que le champ `name` d'un vrai SKILL.md."""
    normalise = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalise.lower()).strip("-")
    return slug[:64] or "comportement"


def _skill_repli(texte: str) -> dict:
    """Skill minimal construit sans appel LLM -- fail-safe utilisé
    SEULEMENT si _generer_skill échoue, pour ne jamais bloquer la
    création/modification d'un comportement (une description imparfaite
    vaut mieux qu'un enregistrement qui échoue)."""
    description = texte if len(texte) <= 120 else texte[:117] + "..."
    skill_md = f"---\nname: {_slugifier(description)}\ndescription: {description}\n---\n\n{texte}\n"
    return {"description": description, "skill_md": skill_md}


def _generer_skill(texte: str) -> dict:
    """
    Transforme l'instruction personnelle brute écrite par l'étudiant en un
    vrai skill Claude (frontmatter name/description + corps d'instructions
    markdown), en suivant la méthode du skill "skill-creator" (Anthropic) :
    description à la troisième personne, qui dit CE QUE fait le skill ET
    QUAND l'utiliser, riche en mots-clés de déclenchement ; corps en
    instructions claires, impératives, structurées. Remplace totalement
    l'ancien _generer_description (13/08) -- mêmes points d'appel
    (ajouter_comportement/modifier_comportement ici, creer_code/
    modifier_code dans core/codes_partage.py), donc toute façon de créer
    un comportement -- l'IA elle-même via l'outil MCP, l'étudiant
    directement dans "Mes comportements", ou un comportement partagé par
    code -- produit désormais un skill, sans distinction (2026-08-16,
    demande Bourama : "exactement un skill claude, aucune différence").

    Fail-safe : toute erreur (appel LLM, format de réponse invalide) fait
    retomber sur _skill_repli plutôt que de bloquer la création.
    """
    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=20.0)
        completion = client.chat.completions.create(
            model=MODELE_SKILL,
            messages=[{
                "role": "user",
                "content": (
                    "Transforme l'instruction personnelle suivante, écrite par un "
                    "étudiant pour personnaliser son assistant IA, en un skill au "
                    "format Anthropic (fichier SKILL.md) : un bloc frontmatter YAML "
                    "avec exactement deux champs `name` (identifiant court en "
                    "minuscules, mots séparés par des tirets, sans accents, max 64 "
                    "caractères) et `description` (UNE phrase à la troisième "
                    "personne, qui dit CE QUE fait ce comportement ET QUAND "
                    "l'appliquer, avec des mots concrets qui déclenchent son usage, "
                    "max 500 caractères), suivi d'un corps en Markdown qui détaille "
                    "l'instruction de façon claire et directe, à la deuxième "
                    "personne, comme des consignes que l'assistant doit suivre. Ne "
                    "réponds QUE avec le contenu du fichier, rien d'autre autour, "
                    "en commençant directement par ---.\n\n"
                    f"Instruction de l'étudiant :\n{texte}"
                ),
            }],
            max_completion_tokens=800,
            timeout=20.0,
        )
        brut = (completion.choices[0].message.content or "").strip()
        correspondance = _RE_FRONTMATTER.match(brut)
        if not correspondance:
            raise ValueError("réponse sans frontmatter valide")
        entete, corps = correspondance.group(1), correspondance.group(2).strip()
        description = ""
        for ligne in entete.splitlines():
            if ligne.strip().lower().startswith("description:"):
                description = ligne.split(":", 1)[1].strip().strip('"')
                break
        if not description or not corps:
            raise ValueError("frontmatter ou corps manquant")
        skill_md = brut if brut.endswith("\n") else brut + "\n"
        return {"description": description, "skill_md": skill_md}
    except Exception as e:
        logging.error(f"ERREUR génération skill comportement : {e}")
        return _skill_repli(texte)


def lister_comportements(agent_id: str, etudiant_id: str) -> list[dict]:
    """Liste ordonnée (plus ancien -> plus récent) des instructions
    perso de cet étudiant pour cet agent. Liste vide si rien
    d'enregistré -- jamais None, pour simplifier les appelants (endpoint
    GET, petit routeur, et injection prompt)."""
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("id, texte, description, lien_type, lien_id")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportements {agent_id}/{etudiant_id}) : {e}")
        return []
    return [
        {
            "id": ligne["id"],
            "texte": ligne["texte"],
            "description": ligne.get("description") or "",
            "lien_type": ligne.get("lien_type"),
            "lien_id": ligne.get("lien_id"),
        }
        for ligne in (res.data or [])
        if ligne.get("texte", "").strip()
    ]


def obtenir_comportement_skill(agent_id: str, etudiant_id: str, comportement_id: str) -> str | None:
    """
    Skill complet (frontmatter + corps markdown) d'UN comportement précis,
    vérifié comme appartenant bien à cet (agent_id, etudiant_id) --
    utilisé par l'outil consulter_comportement (core/serveur_mcp_generation.py)
    quand le grand modèle décide de le lire en entier. None si introuvable
    ou n'appartenant pas à cette paire (jamais une fuite entre étudiants).
    """
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("skill_md")
            .eq("id", comportement_id)
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture skill comportement {comportement_id}) : {e}")
        return None
    if not res.data:
        return None
    return res.data.get("skill_md")


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


def ajouter_comportement(
    agent_id: str, etudiant_id: str, texte: str, lien_type: str | None = None, lien_id: str | None = None
) -> dict:
    """
    lien_type/lien_id (16/08/2026, demande Bourama) : rattache
    optionnellement ce comportement à un emplacement du programme
    ("programme"/"matiere"/"chapitre"/"document"/"exercice"/"examen").
    L'appelant (core/bibliotheque_programme.py pour les outils MCP, ou
    api/comportements_etudiants.py pour le REST) est responsable de
    vérifier que cible_id appartient bien à cet étudiant AVANT
    d'appeler cette fonction -- ce module ne revérifie pas la
    propriété de la cible, même logique que le reste du fichier
    (aucune vérification RLS/FK réelle, tout est fait côté code).
    """
    texte = texte.strip()
    skill = _generer_skill(texte)
    ligne_a_inserer = {
        "agent_id": agent_id,
        "etudiant_id": etudiant_id,
        "texte": texte,
        "description": skill["description"],
        "skill_md": skill["skill_md"],
        "lien_type": lien_type,
        "lien_id": lien_id,
    }
    res = supabase.table("comportements_etudiants").insert(ligne_a_inserer).execute()
    ligne = res.data[0]
    return {
        "id": ligne["id"],
        "texte": ligne["texte"],
        "description": ligne.get("description") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
    }


def modifier_comportement(agent_id: str, etudiant_id: str, comportement_id: str, texte: str) -> dict | None:
    """Modifie le texte -- ne touche jamais lien_type/lien_id (pas
    demandé : un comportement lié le reste, seul son texte change)."""
    texte = texte.strip()
    skill = _generer_skill(texte)
    res = (
        supabase.table("comportements_etudiants")
        .update({"texte": texte, "description": skill["description"], "skill_md": skill["skill_md"]})
        .eq("id", comportement_id)
        .eq("agent_id", agent_id)
        .eq("etudiant_id", etudiant_id)
        .execute()
    )
    if not res.data:
        return None
    ligne = res.data[0]
    return {
        "id": ligne["id"],
        "texte": ligne["texte"],
        "description": ligne.get("description") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
    }


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
