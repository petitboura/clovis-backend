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
# 17/08 : llama-3.1-8b-instant decommissionne par Groq (404 en prod).
MODELE_PETIT = "openai/gpt-oss-20b"

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
    nom = texte if len(texte) <= 60 else texte[:57] + "..."
    skill_md = f"---\nname: {_slugifier(description)}\ndescription: {description}\n---\n\n{texte}\n"
    return {"nom": nom, "description": description, "skill_md": skill_md}


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

    En plus du frontmatter name/description standard, on demande un
    troisième champ `nom_affichage` (18/08/2026, demande Bourama) : un nom
    court et soigné destiné à être VU par l'étudiant dans "Mes
    comportements" -- même exercice que `name`, mais lisible (accents,
    majuscules, espaces) plutôt qu'un slug technique. `name`/`description`
    restent inchangés (routeur + skill, jamais affichés tels quels à
    l'étudiant). Utilisé seulement si l'étudiant n'a pas choisi son propre
    nom (voir ajouter_comportement/modifier_comportement).
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
                    "avec exactement trois champs `name` (identifiant court en "
                    "minuscules, mots séparés par des tirets, sans accents, max 64 "
                    "caractères), `description` (UNE phrase à la troisième "
                    "personne, qui dit CE QUE fait ce comportement ET QUAND "
                    "l'appliquer, avec des mots concrets qui déclenchent son usage, "
                    "max 500 caractères) et `nom_affichage` (un nom court et soigné, "
                    "2 à 5 mots, avec accents/majuscules/espaces normaux, pensé pour "
                    "être LU par l'étudiant dans une liste -- pas un slug technique, "
                    "max 40 caractères), suivi d'un corps en Markdown qui détaille "
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
        nom_affichage = ""
        for ligne in entete.splitlines():
            ligne_basse = ligne.strip().lower()
            if ligne_basse.startswith("description:"):
                description = ligne.split(":", 1)[1].strip().strip('"')
            elif ligne_basse.startswith("nom_affichage:"):
                nom_affichage = ligne.split(":", 1)[1].strip().strip('"')
        if not description or not corps:
            raise ValueError("frontmatter ou corps manquant")
        if not nom_affichage:
            # Repli léger : pas de nouvel appel LLM pour un simple nom
            # manquant, description tronquée suffit (voir _skill_repli).
            nom_affichage = description if len(description) <= 40 else description[:37] + "..."
        skill_md = brut if brut.endswith("\n") else brut + "\n"
        return {"nom": nom_affichage, "description": description, "skill_md": skill_md}
    except Exception as e:
        logging.error(f"ERREUR génération skill comportement : {e}")
        return _skill_repli(texte)


def lister_comportements(agent_id: str, etudiant_id: str) -> list[dict]:
    """Liste ordonnée (plus ancien -> plus récent) des instructions
    perso de cet étudiant pour cet agent. Liste vide si rien
    d'enregistré -- jamais None, pour simplifier les appelants (endpoint
    GET, petit routeur, et injection prompt).

    22/08/2026, demande Bourama (distinguer les 4 origines d'un skill
    dans "Mes skills" -- créé, téléchargé du public, attaché à un
    emplacement, issu d'un audit) : ajoute depuis_audit (colonne directe)
    et depuis_public (déduit via un JOIN léger sur
    comportement_public_activations, aucune colonne dédiée sur
    comportements_etudiants pour ça -- voir migration
    2026_08_21_actif_comportements_publics_bibliotheque_publique.sql).
    Les deux origines peuvent se cumuler avec un lien_type/lien_id
    (un skill d'audit a TOUJOURS un lien, un skill téléchargé PEUT être
    attaché après coup) -- volontairement pas exclusif, laissé au
    frontend de décider dans quel(s) onglet(s) afficher quoi.

    22/08/2026, suite (demande Bourama : "les audits regroupés par
    matière") : ajoute matiere_id/matiere_nom pour les skills liés à un
    CHAPITRE -- résolus en 2 requêtes groupées (chapitres puis matieres),
    jamais une requête par ligne, pour ne pas exploser en N+1 sur les
    dizaines de skills d'audit par chapitre."""
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("id, texte, description, nom, lien_type, lien_id, actif, depuis_audit")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportements {agent_id}/{etudiant_id}) : {e}")
        return []

    lignes = [ligne for ligne in (res.data or []) if ligne.get("texte", "").strip()]

    ids_depuis_public: set[str] = set()
    if lignes:
        try:
            activations = (
                supabase.table("comportement_public_activations")
                .select("comportement_etudiant_id")
                .eq("active_par", etudiant_id)
                .execute()
            )
            ids_depuis_public = {
                a["comportement_etudiant_id"] for a in (activations.data or []) if a.get("comportement_etudiant_id")
            }
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture activations publiques {etudiant_id}) : {e}")
            # Silencieux -- depuis_public reste False pour tout le monde plutôt
            # que de faire planter toute la liste pour un souci secondaire.

    # Résolution mati_id/nom pour les skills liés à un chapitre -- batch,
    # pas de N+1.
    chapitre_ids = {l["lien_id"] for l in lignes if l.get("lien_type") == "chapitre" and l.get("lien_id")}
    matiere_id_par_chapitre: dict[str, str] = {}
    if chapitre_ids:
        try:
            chapitres = (
                supabase.table("chapitres").select("id, matiere_id").in_("id", list(chapitre_ids)).execute()
            )
            matiere_id_par_chapitre = {c["id"]: c["matiere_id"] for c in (chapitres.data or [])}
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (résolution matières des chapitres) : {e}")

    matiere_ids = set(matiere_id_par_chapitre.values())
    nom_matiere_par_id: dict[str, str] = {}
    if matiere_ids:
        try:
            matieres = supabase.table("matieres").select("id, nom").in_("id", list(matiere_ids)).execute()
            nom_matiere_par_id = {m["id"]: m["nom"] for m in (matieres.data or [])}
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture noms matières) : {e}")

    resultat = []
    for ligne in lignes:
        matiere_id = None
        matiere_nom = None
        if ligne.get("lien_type") == "chapitre" and ligne.get("lien_id"):
            matiere_id = matiere_id_par_chapitre.get(ligne["lien_id"])
            matiere_nom = nom_matiere_par_id.get(matiere_id) if matiere_id else None
        resultat.append(
            {
                "id": ligne["id"],
                "texte": ligne["texte"],
                "description": ligne.get("description") or "",
                "nom": ligne.get("nom") or "",
                "lien_type": ligne.get("lien_type"),
                "lien_id": ligne.get("lien_id"),
                "actif": ligne.get("actif", True),
                "depuis_audit": ligne.get("depuis_audit", False),
                "depuis_public": ligne["id"] in ids_depuis_public,
                "matiere_id": matiere_id,
                "matiere_nom": matiere_nom,
            }
        )
    return resultat


def separer_comportements_par_niveau(comportements: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Sépare une liste de comportements (forme de lister_comportements, avec
    lien_type/lien_id) en deux groupes (22/08/2026, demande Bourama --
    correctif au bug de saturation du petit routeur causé par l'audit qui a
    créé un skill par chapitre) :
    - niveau 1 : comportements génériques (lien_type vide/None) + liés à un
      programme + liés à une matière -- catalogue montré d'office au petit
      routeur (choisir_comportements_pertinents), comme avant l'audit.
    - niveau 2 (chapitre) : comportements liés à un chapitre -- PLUS montrés
      d'office. Ils sont filtrés par matière ensuite (voir
      lister_comportements_chapitres_pour_matiere) et seulement si le niveau
      1 a retenu cette matière comme pertinente.
    """
    niveau1 = [c for c in comportements if c.get("lien_type") != "chapitre"]
    niveau2_chapitre = [c for c in comportements if c.get("lien_type") == "chapitre"]
    return niveau1, niveau2_chapitre


def lister_comportements_chapitres_pour_matiere(comportements_chapitre: list[dict], matiere_id: str) -> list[dict]:
    """
    Filtre `comportements_chapitre` (lien_type == "chapitre") pour ne garder
    que ceux dont le chapitre appartient à `matiere_id`. Requête une seule
    fois `chapitres.matiere_id` pour les lien_id concernés -- jamais un
    aller-retour Supabase par comportement.
    """
    if not comportements_chapitre:
        return []
    chapitre_ids = list({c["lien_id"] for c in comportements_chapitre if c.get("lien_id")})
    if not chapitre_ids:
        return []
    try:
        res = (
            supabase.table("chapitres")
            .select("id, matiere_id")
            .in_("id", chapitre_ids)
            .eq("matiere_id", matiere_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (chapitres pour matiere {matiere_id}) : {e}")
        return []
    ids_valides = {ligne["id"] for ligne in (res.data or [])}
    return [c for c in comportements_chapitre if c.get("lien_id") in ids_valides]


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


def modifier_skill_comportement(agent_id: str, etudiant_id: str, comportement_id: str, skill_md: str) -> dict | None:
    """
    18/08/2026, demande Bourama ("les deux : édite le texte, l'impacte,
    ou tu peux l'éditer directement") -- édition DIRECTE du skill
    complet, sans passer par _generer_skill/le texte brut. Complète
    modifier_comportement (qui régénère toujours le skill depuis le
    texte) : ici l'étudiant écrit/corrige le frontmatter+corps lui-même
    et on le stocke tel quel, en ne touchant PAS au texte ni au nom.

    `description` est re-extraite du frontmatter fourni (le routeur
    choisir_comportements_pertinents en dépend) -- rejeté si le
    frontmatter est absent ou invalide, plutôt que de stocker un skill
    cassé silencieusement.

    Point d'attention pour l'appelant (16/08, même logique que le texte
    brut) : si l'étudiant modifie ensuite son texte brut et enregistre
    depuis l'onglet "Texte", modifier_comportement régénère le skill
    depuis ce texte et écrase donc cette édition manuelle -- comportement
    voulu, pas un bug (confirmé par Bourama).
    """
    skill_md = skill_md.strip()
    correspondance = _RE_FRONTMATTER.match(skill_md)
    if not correspondance:
        raise ValueError("FRONTMATTER_INVALIDE")
    entete, corps = correspondance.group(1), correspondance.group(2).strip()
    description = ""
    for ligne in entete.splitlines():
        if ligne.strip().lower().startswith("description:"):
            description = ligne.split(":", 1)[1].strip().strip('"')
            break
    if not description or not corps:
        raise ValueError("FRONTMATTER_INCOMPLET")
    skill_md_normalise = skill_md if skill_md.endswith("\n") else skill_md + "\n"

    res = (
        supabase.table("comportements_etudiants")
        .update({"skill_md": skill_md_normalise, "description": description})
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
        "nom": ligne.get("nom") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
        "actif": ligne.get("actif", True),
    }


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
    agent_id: str,
    etudiant_id: str,
    texte: str,
    nom: str | None = None,
    lien_type: str | None = None,
    lien_id: str | None = None,
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

    nom (18/08/2026, demande Bourama) : nom d'affichage choisi par
    l'étudiant. Vide/absent -> mode "auto" : on prend le nom_affichage
    généré par _generer_skill (même appel LLM que le skill, aucun coût
    supplémentaire).
    """
    texte = texte.strip()
    nom = (nom or "").strip()
    skill = _generer_skill(texte)
    ligne_a_inserer = {
        "agent_id": agent_id,
        "etudiant_id": etudiant_id,
        "texte": texte,
        "description": skill["description"],
        "skill_md": skill["skill_md"],
        "nom": nom or skill["nom"],
        "lien_type": lien_type,
        "lien_id": lien_id,
    }
    res = supabase.table("comportements_etudiants").insert(ligne_a_inserer).execute()
    ligne = res.data[0]
    return {
        "id": ligne["id"],
        "texte": ligne["texte"],
        "description": ligne.get("description") or "",
        "nom": ligne.get("nom") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
        "actif": ligne.get("actif", True),
    }


def importer_comportement_depuis_skill_md(
    agent_id: str, etudiant_id: str, nom: str, skill_md: str, lien_type: str | None = None, lien_id: str | None = None
) -> dict:
    """25/08/2026, demande Bourama : uploader un fichier .md directement
    dans "Mes comportements", GARDÉ TEL QUEL -- contrairement à
    ajouter_comportement ci-dessus, ne passe PAS par _generer_skill
    (Bourama : "gardé tel quel, sans y toucher"). `texte` (colonne
    NOT NULL) est rempli avec le skill_md lui-même faute de "texte brut"
    distinct ; `description` (envoyée au petit routeur) est extraite du
    frontmatter s'il y en a un.

    04/09/2026, correctif Bourama (bug remonté : import en masse d'un pack
    de skills externe, la moitié affichait la description "input_format:
    json" au lieu d'une vraie description) -- l'ancienne regex (motif
    "description:" suivi d'espaces) matchait aussi les retours
    à la ligne : quand le frontmatter du fichier importé a une ligne
    "description:" vide (valide, mais jamais produit par _generer_skill en
    interne, donc jamais testé avant), la regex débordait sur la ligne
    SUIVANTE du frontmatter et prenait son contenu à la place. Corrigé en
    n'autorisant plus que espaces/tabulations après les deux points,
    jamais la ligne suivante.

    Si la description reste vide après extraction (frontmatter sans
    description, ou vide comme ci-dessus), le skill est quand même
    enregistré tout de suite (upload jamais bloqué) mais avec
    statut_description="en_attente" -- une vraie description est générée
    ensuite en arrière-plan à partir du skill_md (voir
    core/file_attente_description_skills.py), sans jamais toucher au texte
    ni au skill_md lui-même (gardé tel quel). Seulement pour les imports à
    partir de maintenant, les skills déjà importés avant ce correctif ne
    sont pas repris automatiquement (demande explicite Bourama)."""
    correspondance = re.search(r"^description:[ \t]*(.+)$", skill_md, re.MULTILINE)
    description = correspondance.group(1).strip().strip('"') if correspondance else ""
    ligne_a_inserer = {
        "agent_id": agent_id,
        "etudiant_id": etudiant_id,
        "texte": skill_md,
        "description": description,
        "skill_md": skill_md,
        "nom": nom.strip() or "Sans nom",
        "lien_type": lien_type,
        "lien_id": lien_id,
        "statut_description": "pret" if description else "en_attente",
    }
    res = supabase.table("comportements_etudiants").insert(ligne_a_inserer).execute()
    ligne = res.data[0]
    return {
        "id": ligne["id"],
        "texte": ligne["texte"],
        "description": ligne.get("description") or "",
        "nom": ligne.get("nom") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
        "actif": ligne.get("actif", True),
    }


def modifier_comportement(
    agent_id: str, etudiant_id: str, comportement_id: str, texte: str, nom: str | None = None
) -> dict | None:
    """Modifie le texte -- ne touche jamais lien_type/lien_id (pas
    demandé ici : modifier le TEXTE d'un comportement lié ne doit pas
    le détacher accidentellement -- voir attacher_comportement
    ci-dessous pour changer lien_type/lien_id explicitement, séparé
    exprès en deux actions distinctes, 20/08/2026 demande Bourama :
    "au moment de la création ou après tu peux l'attacher").

    nom (18/08/2026) : même règle qu'à la création -- vide/absent -> nom
    auto régénéré avec le nouveau skill ; rempli -> gardé tel quel.
    L'appelant doit donc renvoyer le nom manuel actuel s'il veut le
    préserver lors d'une modification du texte seul (voir
    api/comportements_etudiants.py)."""
    texte = texte.strip()
    nom = (nom or "").strip()
    skill = _generer_skill(texte)
    res = (
        supabase.table("comportements_etudiants")
        .update({
            "texte": texte,
            "description": skill["description"],
            "skill_md": skill["skill_md"],
            "nom": nom or skill["nom"],
        })
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
        "nom": ligne.get("nom") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
        "actif": ligne.get("actif", True),
    }


def attacher_comportement(
    agent_id: str, etudiant_id: str, comportement_id: str, lien_type: str | None, lien_id: str | None
) -> dict | None:
    """
    Attache (ou détache si lien_type/lien_id sont None) un comportement
    DÉJÀ EXISTANT à un emplacement du programme -- séparé de
    modifier_comportement exprès (20/08/2026, demande Bourama : "au
    moment de la création ou après tu peux l'attacher"). L'appelant est
    responsable de vérifier que lien_id appartient bien à cet étudiant
    AVANT d'appeler cette fonction (voir proprietaire_lien_comportement
    dans core/bibliotheque_programme.py), même convention que
    ajouter_comportement.
    """
    res = (
        supabase.table("comportements_etudiants")
        .update({"lien_type": lien_type, "lien_id": lien_id})
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
        "nom": ligne.get("nom") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
        "actif": ligne.get("actif", True),
    }


def lister_comportements_par_lien(agent_id: str, etudiant_id: str, lien_type: str, lien_id: str) -> list[dict]:
    """Comportements de cet étudiant attachés PRÉCISÉMENT à cet
    emplacement -- pour afficher, depuis un écran programme (chapitre,
    matière...), les comportements déjà accrochés là (20/08/2026)."""
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("id, texte, description, nom")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .eq("lien_type", lien_type)
            .eq("lien_id", lien_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comportements par lien {lien_type}={lien_id}) : {e}")
        return []
    return [
        {"id": l["id"], "texte": l["texte"], "description": l.get("description") or "", "nom": l.get("nom") or ""}
        for l in (res.data or [])
        if l.get("texte", "").strip()
    ]


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


def activer_desactiver_comportement(agent_id: str, etudiant_id: str, comportement_id: str, actif: bool) -> dict | None:
    """
    21/08/2026, demande Bourama : "ajoute activer et désactiver aux
    comportements". Désactiver != supprimer -- le comportement reste
    visible/modifiable dans "Mes comportements", seul le filtre posé
    dans core/main.py (avant choisir_comportements_pertinents) l'exclut
    des candidats proposés au grand modèle. Les outils MCP de lecture
    (consulter_comportement et consorts) continuent de le montrer tel
    quel -- volontairement pas de double filtrage côté outils, un
    comportement désactivé reste consultable par l'étudiant lui-même.
    """
    res = (
        supabase.table("comportements_etudiants")
        .update({"actif": actif})
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
        "nom": ligne.get("nom") or "",
        "lien_type": ligne.get("lien_type"),
        "lien_id": ligne.get("lien_id"),
        "actif": ligne.get("actif", True),
    }


# ---------------------------------------------------------------------
# Comportements publics (21/08/2026, demande Bourama : "les comportements
# aussi, je veux un onglet public... quelqu'un peut l'uploader et
# l'activer"). Même philosophie que le système de plugins (voir
# api/plugins_programme.py) : publier prend un INSTANTANÉ indépendant du
# comportement source (l'original de l'auteur n'est jamais modifié ni
# lié après coup) ; activer crée une VRAIE ligne comportements_etudiants
# chez l'utilisateur qui active (toujours pour AGENT_ID_ESPACE -- "Mon
# espace" est le seul endroit où cette section existe côté frontend),
# actif=true par défaut, indépendante elle aussi du comportement public
# d'origine dès sa création.
# ---------------------------------------------------------------------

# Valeur vérifiée dans core/serveur_mcp_espace.py::AGENT_ID_ESPACE -- pas
# importée directement ici (import circulaire : serveur_mcp_espace.py
# importe déjà ce module), donc dupliquée avec ce commentaire comme
# rappel si jamais l'une des deux valeurs change sans l'autre.
AGENT_ID_ESPACE = "clovis"


def publier_comportement_public(agent_id: str, etudiant_id: str, comportement_id: str) -> dict | None:
    """Publie une copie figée d'un comportement de CET étudiant. None si
    le comportement n'existe pas ou ne lui appartient pas (jamais de
    publication d'un comportement d'un autre)."""
    source = (
        supabase.table("comportements_etudiants")
        .select("nom, description, texte, skill_md")
        .eq("id", comportement_id)
        .eq("agent_id", agent_id)
        .eq("etudiant_id", etudiant_id)
        .maybe_single()
        .execute()
    )
    if not source or not source.data:
        return None
    ligne = (
        supabase.table("comportements_publics")
        .insert({
            "auteur_id": etudiant_id,
            "nom": source.data.get("nom") or "Sans nom",
            "description": source.data.get("description") or "",
            "texte": source.data["texte"],
            "skill_md": source.data.get("skill_md") or "",
        })
        .execute()
    )
    return ligne.data[0]


def uploader_comportement_public(auteur_id: str, nom: str, description: str, skill_md: str) -> dict:
    """25/08/2026, demande Bourama : uploader directement un fichier .md
    dans le catalogue public des skills, publié immédiatement pour tout
    le monde -- pas de passage par "Mes comportements" ni par le bouton
    "Publier" existant (voir publier_comportement_public ci-dessus pour
    ce second chemin). `texte` (colonne NOT NULL, normalement le texte
    brut ayant servi à générer le skill via _generer_skill) est ici
    rempli avec le contenu du .md lui-même : il n'y a pas de "texte brut"
    séparé quand on uploade un skill déjà rédigé."""
    ligne = (
        supabase.table("comportements_publics")
        .insert({
            "auteur_id": auteur_id,
            "nom": nom.strip() or "Sans nom",
            "description": (description or "").strip(),
            "texte": skill_md,
            "skill_md": skill_md,
        })
        .execute()
    )
    return ligne.data[0]


def lister_comportements_publics(mot_cle: str | None = None) -> list[dict]:
    requete = supabase.table("comportements_publics").select("*")
    mot_cle = (mot_cle or "").strip()
    if mot_cle:
        requete = requete.or_(f"nom.ilike.%{mot_cle}%,description.ilike.%{mot_cle}%")
    try:
        res = requete.order("activations_count", desc=True).limit(100).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche comportements publics q={mot_cle}) : {e}")
        return []
    return res.data or []


def activer_comportement_public(comportement_public_id: str, etudiant_id: str) -> dict | None:
    """
    Crée une copie indépendante (comportements_etudiants, actif=true)
    chez `etudiant_id` pour AGENT_ID_ESPACE. Déjà activé par cet
    utilisateur -> renvoie sa copie existante plutôt que d'en recréer une
    deuxième (même principe que telecharger_plugin côté plugins).
    """
    deja = (
        supabase.table("comportement_public_activations")
        .select("comportement_etudiant_id")
        .eq("comportement_public_id", comportement_public_id)
        .eq("active_par", etudiant_id)
        .maybe_single()
        .execute()
    )
    if deja and deja.data and deja.data.get("comportement_etudiant_id"):
        copie = (
            supabase.table("comportements_etudiants")
            .select("id, texte, description, nom, lien_type, lien_id, actif")
            .eq("id", deja.data["comportement_etudiant_id"])
            .maybe_single()
            .execute()
        )
        if copie and copie.data:
            return copie.data

    source = (
        supabase.table("comportements_publics")
        .select("nom, description, texte, skill_md, activations_count")
        .eq("id", comportement_public_id)
        .maybe_single()
        .execute()
    )
    if not source or not source.data:
        return None

    nouvelle = (
        supabase.table("comportements_etudiants")
        .insert({
            "agent_id": AGENT_ID_ESPACE,
            "etudiant_id": etudiant_id,
            "texte": source.data["texte"],
            "description": source.data.get("description") or "",
            "skill_md": source.data.get("skill_md") or "",
            "nom": source.data.get("nom") or "",
            "actif": True,
        })
        .execute()
    ).data[0]

    supabase.table("comportement_public_activations").insert({
        "comportement_public_id": comportement_public_id,
        "active_par": etudiant_id,
        "comportement_etudiant_id": nouvelle["id"],
    }).execute()

    try:
        supabase.table("comportements_publics").update(
            {"activations_count": (source.data.get("activations_count") or 0) + 1}
        ).eq("id", comportement_public_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (compteur activations comportement public {comportement_public_id}) : {e}")

    return nouvelle
