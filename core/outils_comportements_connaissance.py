"""
Outils MCP liés aux comportements (skills) de l'IA et à sa base de
connaissance (documents indexés, matière active).

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code.
"""

import logging

from retriever import chercher_candidats as _chercher_candidats
from contenu_dynamique_matiere import resoudre_system_prompt as _resoudre_system_prompt_matiere

from core.comportements_etudiants import (
    obtenir_comportement_skill as _obtenir_comportement_skill,
    lister_comportements as _lister_comportements,
    ajouter_comportement as _ajouter_comportement,
    modifier_comportement as _modifier_comportement,
    supprimer_comportement as _supprimer_comportement,
)
from core.codes_partage import (
    obtenir_comportement_skill_recu as _obtenir_comportement_skill_recu,
)

from core.outils_generation_commun import (
    mcp_generation,
    Context,
    _supabase_memoire,
    _SUPABASE_URL,
    TYPES_EMPLACEMENT_BIBLIOTHEQUE,
    _libelle_emplacement,
)



@mcp_generation.tool()
def gerer_comportement(
    action: str,
    ctx: Context,
    comportement_id: str = "",
    texte: str = "",
) -> str:
    """
    Gère les instructions personnelles ("skills" dans toute l'interface,
    "comportement" seulement en interne) que CET étudiant a écrites pour
    Clovis, section "Mes comportements" de "Mon espace" -- consolidé le
    26/08, un seul outil, plusieurs actions.

    `action` doit être l'une de :
    - "lister" : liste tous les comportements de cet étudiant (id,
      description courte, emplacement lié le cas échéant -- PAS le texte
      complet, voir "consulter" pour ça). IMPORTANT (terme utilisateur) :
      dans TOUTE l'interface, cette fonctionnalité s'appelle "skill(s)" --
      l'utilisateur ne dira presque jamais "comportement". Utilise cette
      action dès qu'il demande "mes skills", "quels sont mes skills",
      "montre-moi mes skills/mes comportements", etc. -- pas seulement
      quand un skill semble déjà pertinent pour le message en cours (ça,
      c'est géré par la liste de candidats du message système, voir
      "consulter") : "lister" répond à une vraie demande d'énumération.
      Aucun paramètre.
    - "consulter" : lit le skill COMPLET (format Claude, frontmatter +
      instructions) d'un comportement précis, que cet utilisateur l'ait
      écrit lui-même, ou qu'il l'ait reçu d'un autre utilisateur via un
      code (id préfixé "recu:"). Le message système t'a déjà donné une
      courte description de ceux qui semblent pertinents pour ce
      message -- utilise cette action quand l'un d'eux semble
      s'appliquer, AVANT de répondre, pour lire son contenu réel plutôt
      que de deviner à partir de la description seule. Paramètre :
      `comportement_id`.
    - "ajouter" : enregistre une NOUVELLE instruction personnelle, à
      utiliser SEULEMENT quand l'étudiant exprime CLAIREMENT et
      EXPLICITEMENT une préférence ou une règle à retenir pour la suite
      (ex: "explique-moi toujours avec des schémas", "ne me donne jamais
      la réponse directe, guide-moi", "crée-moi un skill qui..."). S'ajoute
      EN PLUS de ses autres comportements, ne les remplace pas.
      N'UTILISE JAMAIS CETTE ACTION SUR UNE SUPPOSITION. Si la demande
      est vague, ambiguë, ou que tu devines seulement ce que l'étudiant
      voudrait retenir sans qu'il l'ait dit clairement, NE CRÉE RIEN --
      demande-lui d'abord de préciser ce qu'il veut que tu retiennes
      exactement. Ne crée jamais un comportement "au cas où", pour
      anticiper un besoin non exprimé, ou à partir d'une remarque en
      passant qui n'était pas une vraie demande de mémorisation. Une
      création hâtive et mal comprise est pire qu'aucune création : elle
      pollue durablement ses instructions et influence toutes ses
      conversations futures avec toi. Paramètre : `texte`.
    - "modifier" : remplace le texte COMPLET d'un comportement existant
      (à partir de son id, vu via "consulter" ou la description courte
      donnée dans le message système). Utilise cette action quand
      l'étudiant veut corriger ou préciser une instruction déjà
      enregistrée -- pas pour en ajouter une nouvelle (voir "ajouter").
      Paramètres : `comportement_id`, `texte`.
    - "supprimer" : supprime DÉFINITIVEMENT un comportement, à partir de
      son id. Paramètre : `comportement_id`. SENSIBLE : demande toujours
      confirmation à l'étudiant avant d'être exécuté, quelle que soit la
      formulation de sa demande.
    """
    requete = ctx.request_context.request
    user_id = requete.query_params.get("user_id")
    agent_id = requete.query_params.get("agent_id")
    if not user_id or not agent_id:
        return "Erreur : impossible d'identifier l'étudiant ou l'agent."

    if action == "lister":
        try:
            comportements = _lister_comportements(agent_id, user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (lister) : {e}")
            return "Erreur : impossible de lister les comportements, réessaie."
        if not comportements:
            return "Aucun comportement enregistré pour l'instant."
        lignes = []
        for c in comportements:
            ligne = f"- {c['description']}"
            if c.get("lien_type") and c.get("lien_id"):
                libelle = _libelle_emplacement(c["lien_type"], c["lien_id"]) if c["lien_type"] in TYPES_EMPLACEMENT_BIBLIOTHEQUE else None
                ligne += f"\n  lié à : {libelle or (c['lien_type'] + ' ' + c['lien_id'])}"
            ligne += f"\n  [id: {c['id']}]"
            lignes.append(ligne)
        return "\n".join(lignes)

    if action == "consulter":
        try:
            if comportement_id.startswith("recu:"):
                skill_md = _obtenir_comportement_skill_recu(user_id, comportement_id)
            else:
                skill_md = _obtenir_comportement_skill(agent_id, user_id, comportement_id)
            if skill_md is None:
                return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
            return skill_md
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (consulter) : {e}")
            return "Erreur : impossible de consulter ce comportement, réessaie."

    if action == "ajouter":
        try:
            ligne = _ajouter_comportement(agent_id, user_id, texte)
            return f"Comportement enregistré (id {ligne['id']}) : {ligne['description']}"
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (ajouter) : {e}")
            return "Erreur : impossible d'enregistrer ce comportement, réessaie."

    if action == "modifier":
        try:
            ligne = _modifier_comportement(agent_id, user_id, comportement_id, texte)
            if ligne is None:
                return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
            return f"Comportement modifié : {ligne['description']}"
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (modifier) : {e}")
            return "Erreur : impossible de modifier ce comportement, réessaie."

    if action == "supprimer":
        try:
            ok = _supprimer_comportement(agent_id, user_id, comportement_id)
            if not ok:
                return "Ce comportement est introuvable (id invalide, ou ne correspond pas à cet étudiant)."
            return "Comportement supprimé."
        except Exception as e:
            logging.error(f"ERREUR gerer_comportement (supprimer) : {e}")
            return "Erreur : impossible de supprimer ce comportement, réessaie."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister, "
        "consulter, ajouter, modifier, supprimer."
    )



@mcp_generation.tool()
def gerer_base_connaissance(
    action: str,
    ctx: Context,
    question: str = "",
    nom: str = "",
) -> str:
    """
    Cherche et lit dans la base de connaissances de l'agent (documents et
    instructions de référence préparés à l'avance par l'équipe Clovis SUR
    Clovis et l'application elle-même, PAS les documents personnels de
    l'utilisateur, voir gerer_document_bibliotheque pour ça), consolidé
    le 26/08, un seul outil, plusieurs actions qui fonctionnaient déjà
    ensemble comme un mécanisme à plusieurs étapes.

    `action` doit être l'une de :
    - "chercher" : cherche les passages pertinents pour répondre à
      `question`. À utiliser quand la question touche un sujet précis où
      un contenu de référence a pu être préparé à l'avance, pas
      systématique, seulement si pertinent. Renvoie les extraits trouvés
      ou un message si rien de pertinent. Paramètre : `question`.
    - "lister_articles" : liste les noms de tous les articles
      disponibles. À utiliser avant "lire_article" si le nom exact de
      l'article recherché n'est pas connu. Aucun paramètre.
    - "lire_article" : renvoie le texte COMPLET et EXACT (pas un résumé,
      pas une reformulation, le contenu tel qu'il est stocké, mot pour
      mot) d'un article, identifié par son `nom` exact. À utiliser quand
      la question porte sur l'ensemble d'un article plutôt que sur un
      point précis (ex : "montre-moi l'article Bibliothèque", "affiche
      le fichier tel qu'il est"), en complément de "chercher" qui ne
      renvoie que des passages. Quand tu restitues ce résultat à
      l'utilisateur, recopie-le intégralement et tel quel (verbatim), ne
      le résume pas, ne le reformule pas, ne le raccourcis pas. Si `nom`
      est inconnu, utilise d'abord "chercher" pour identifier le bon
      nom, ou "lister_articles" pour voir les noms disponibles.
      Paramètre : `nom`.
    - "obtenir_fichier" : renvoie le FICHIER original (pas son texte
      recopié) d'un article, sous forme d'un lien vers le fichier tel
      qu'il a été déposé. À utiliser quand tu juges que le fichier
      lui-même aide réellement la réponse (l'utilisateur ne sait
      généralement pas qu'il existe, donc ne le demandera pas
      explicitement), pas systématiquement à chaque question. Pas juste
      lire son contenu (pour ça, "lire_article"). Si `nom` est inconnu,
      utilise "lister_articles" pour voir les noms disponibles.
      Paramètre : `nom`.
    """
    requete = ctx.request_context.request
    agent_id = requete.query_params.get("agent_id")

    if action == "chercher":
        try:
            candidats = _chercher_candidats(question, agent_id=agent_id)
            morceaux = [c["contenu"] for c in candidats.get("prompts", [])] + [
                c["contenu"] for c in candidats.get("documents", [])
            ]
            if not morceaux:
                return "Rien de pertinent trouvé dans la base de connaissances pour cette question."
            return "\n\n---\n\n".join(morceaux)
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (chercher) : {e}")
            return "Erreur : la recherche a échoué, réessaie."

    if action == "lister_articles":
        try:
            res = (
                _supabase_memoire.table("documents")
                .select("nom")
                .eq("agent_id", agent_id)
                .execute()
            )
            noms = sorted({r["nom"] for r in (res.data or [])})
            if not noms:
                return "Aucun article dans la base de connaissances pour l'instant."
            return "\n".join(noms)
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (lister_articles) : {e}")
            return "Erreur : la liste des articles a échoué, réessaie."

    if action == "lire_article":
        try:
            res = (
                _supabase_memoire.table("documents")
                .select("contenu, position")
                .eq("agent_id", agent_id)
                .eq("nom", nom)
                .order("position", desc=False, nullsfirst=False)
                .execute()
            )
            morceaux = res.data or []
            if not morceaux:
                return f"Aucun article nommé '{nom}' trouvé dans la base de connaissances."
            return " ".join(m["contenu"] for m in morceaux)
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (lire_article) : {e}")
            return "Erreur : la lecture de l'article a échoué, réessaie."

    if action == "obtenir_fichier":
        try:
            res = (
                _supabase_memoire.table("documents")
                .select("nom")
                .eq("agent_id", agent_id)
                .eq("nom", nom)
                .limit(1)
                .execute()
            )
            if not res.data:
                return f"Aucun article nommé '{nom}' trouvé dans la base de connaissances."
            url = f"{_SUPABASE_URL}/storage/v1/object/public/documents-agents/{agent_id}/{nom}"
            return f"Fichier : {url}"
        except Exception as e:
            logging.error(f"ERREUR gerer_base_connaissance (obtenir_fichier) : {e}")
            return "Erreur : la récupération du fichier a échoué, réessaie."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : chercher, "
        "lister_articles, lire_article, obtenir_fichier."
    )


@mcp_generation.tool()
def consulter_matiere_active(message_utilisateur: str, ctx: Context) -> str:
    """
    Consulte le contenu pédagogique spécifique (cours, consignes d'un
    enseignant) débloqué par CET utilisateur pour la matière la plus
    pertinente par rapport à `message_utilisateur` (le message en cours
    de l'utilisateur, tel quel). À utiliser si la question ressemble à
    une question de cours et que l'utilisateur a pu débloquer une
    matière avec un code. Ce contenu est un COMPLÉMENT à tes
    instructions habituelles, pas un remplacement -- utilise-le comme
    référence, pas comme un bloc à recopier tel quel. Peut renvoyer un
    message générique si aucune matière n'est débloquée.
    """
    try:
        requete = ctx.request_context.request
        agent_id = requete.query_params.get("agent_id")
        user_id = requete.query_params.get("user_id")
        return _resoudre_system_prompt_matiere(message_utilisateur, agent_id, user_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_matiere_active : {e}")
        return "Erreur : impossible de consulter le contenu de la matière, réessaie."
