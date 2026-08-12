"""
Serveur MCP local pour explorer et modifier un dépôt GitHub -- monté
comme core/serveur_mcp_generation.py (voir api/main.py), enregistré dans
registre_outils.py au même titre que Wolfram/Notion/generation.

Pourquoi pas un vrai `git clone` sur disque : ce process tourne sur
Railway, partagé entre TOUTES les personnes utilisant la plateforme en
même temps. Cloner un dépôt arbitraire (taille non maîtrisée) sur le
disque du conteneur partagé est un risque réel de saturation pour tout
le monde. À la place, tout passe par l'API REST/Git de GitHub -- même
résultat côté navigation (lister l'arborescence complète, lire n'importe
quel fichier, écrire un changement), sans jamais toucher au disque local.

Trois outils :
- explorer_depot_github : arborescence complète (récursive) du dépôt.
- lire_fichier_depot_github : contenu d'un fichier précis.
- modifier_fichier_depot_github : ÉCRIT un changement -- voir
  core/registre_outils.py, OUTILS_SENSIBLES : cet outil déclenche
  TOUJOURS une confirmation explicite avant exécution (main.py
  interrompt le flux, quel que soit `mode`), jamais silencieux.
  N'utilise JAMAIS un token de plateforme partagé pour écrire -- 
  uniquement le token OAuth de LA PERSONNE CONNECTÉE (voir
  connexions/oauth_generique.py), sinon n'importe quel agent pourrait
  écrire sur n'importe quel dépôt avec une clé globale. Si la personne
  n'est pas connectée à GitHub, l'outil renvoie un message clair au lieu
  d'essayer d'écrire.
"""

import logging

import requests
from mcp.server.mcpserver import MCPServer as FastMCP

from connexions.oauth_generique import obtenir_token_valide

TAILLE_MAX_ARBORESCENCE = 300  # entrées max listées, pour un gros dépôt
LONGUEUR_MAX_FICHIER = 20_000  # caractères, par fichier lu

mcp_github = FastMCP(name="github")


def _get_secret(key):
    import os
    return os.environ.get(key)


def _token_lecture(user_id):
    """
    Token pour une opération de LECTURE seule : celui de la personne
    connectée si disponible (dépôts privés), sinon le token de
    plateforme (GITHUB_TOKEN, dépôts publics, quota levé), sinon aucun
    (dépôts publics, quota serré -- 60 requêtes/heure partagées).
    """
    token_utilisateur = obtenir_token_valide("github", user_id) if user_id else None
    return token_utilisateur or _get_secret("GITHUB_TOKEN")


@mcp_github.tool()
def explorer_depot_github(repo: str, branche: str = "", chemin_depart: str = "", user_id: str = "") -> str:
    """
    Liste l'arborescence COMPLÈTE (récursive, un seul appel API GitHub --
    pas de git clone, voir docstring du module) d'un dépôt GitHub public
    ou privé (si la personne est connectée à GitHub). `repo` au format
    "proprietaire/nom-du-depot". `branche` optionnelle (branche par
    défaut du dépôt si vide). `chemin_depart` optionnel pour ne lister
    qu'un sous-dossier.

    Renvoie TOUJOURS le nombre TOTAL exact de fichiers et de dossiers en
    première ligne (calculé sur l'arborescence complète, jamais sur une
    liste tronquée) -- si on te demande juste "combien de fichiers dans
    ce dépôt", cette ligne suffit, pas besoin de compter la liste
    toi-même. Le détail ligne par ligne, lui, PEUT être tronqué si le
    dépôt est très volumineux (voir mention "TRONQUÉE").
    """
    token = _token_lecture(user_id or None)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        if not branche:
            info = requests.get(f"https://api.github.com/repos/{repo}", timeout=10, headers=headers)
            if info.status_code != 200:
                return f"Dépôt introuvable ou inaccessible (statut {info.status_code}) : {repo}"
            branche = info.json().get("default_branch", "main")

        ref = requests.get(
            f"https://api.github.com/repos/{repo}/git/refs/heads/{branche}", timeout=10, headers=headers
        )
        if ref.status_code != 200:
            return f"Branche introuvable : {branche} sur {repo}"
        sha_commit = ref.json()["object"]["sha"]

        arbo = requests.get(
            f"https://api.github.com/repos/{repo}/git/trees/{sha_commit}?recursive=1",
            timeout=15,
            headers=headers,
        )
        if arbo.status_code != 200:
            return f"Impossible de lire l'arborescence (statut {arbo.status_code}) : {repo}"

        donnees = arbo.json()
        entrees_completes = donnees.get("tree", [])
        if chemin_depart:
            entrees_completes = [e for e in entrees_completes if e["path"].startswith(chemin_depart)]

        # Distinction importante : "truncated" venant de GitHub lui-même
        # (dépôt extrêmement volumineux, >100k entrées ou >7 Mo de
        # réponse) veut dire que même entrees_completes est incomplète --
        # le total n'est alors qu'un minorant, pas un vrai total exact.
        # Notre propre troncature d'AFFICHAGE (TAILLE_MAX_ARBORESCENCE),
        # elle, n'affecte jamais le total puisqu'il est calculé avant.
        truncature_github = donnees.get("truncated", False)
        nb_fichiers = sum(1 for e in entrees_completes if e["type"] == "blob")
        nb_dossiers = sum(1 for e in entrees_completes if e["type"] == "tree")

        tronque_affichage = len(entrees_completes) > TAILLE_MAX_ARBORESCENCE
        entrees_affichees = entrees_completes[:TAILLE_MAX_ARBORESCENCE]

        if truncature_github:
            entete = (
                f"{repo} (branche {branche}) -- dépôt extrêmement volumineux, "
                f"GitHub lui-même limite la réponse : AU MOINS {nb_fichiers} fichiers, "
                f"{nb_dossiers} dossiers (décompte partiel, pas le vrai total)."
            )
        else:
            entete = (
                f"{repo} (branche {branche}) -- TOTAL EXACT : {nb_fichiers} fichiers, "
                f"{nb_dossiers} dossiers."
            )
        if tronque_affichage:
            entete += (
                f" Liste détaillée ci-dessous TRONQUÉE aux {TAILLE_MAX_ARBORESCENCE} "
                "premières entrées (le total ci-dessus reste correct)."
            )

        lignes = [
            f"- {e['path']} ({'dossier' if e['type'] == 'tree' else 'fichier'})" for e in entrees_affichees
        ]
        return entete + "\n\n" + "\n".join(lignes)
    except Exception as e:
        logging.error(f"ERREUR explorer_depot_github ({repo}) : {e}")
        return "Erreur : impossible d'explorer ce dépôt, réessaie."


@mcp_github.tool()
def lire_fichier_depot_github(repo: str, chemin: str, branche: str = "", user_id: str = "") -> str:
    """
    Lit le contenu d'un fichier précis dans un dépôt GitHub public ou
    privé (si la personne est connectée à GitHub). `repo` au format
    "proprietaire/nom-du-depot", `chemin` le chemin exact du fichier
    (ex: "src/main.py"), `branche` optionnelle (branche par défaut si
    vide).
    """
    token = _token_lecture(user_id or None)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        if not branche:
            info = requests.get(f"https://api.github.com/repos/{repo}", timeout=10, headers=headers)
            if info.status_code != 200:
                return f"Dépôt introuvable ou inaccessible (statut {info.status_code}) : {repo}"
            branche = info.json().get("default_branch", "main")

        raw_url = f"https://raw.githubusercontent.com/{repo}/{branche}/{chemin}"
        reponse = requests.get(raw_url, timeout=10, headers=headers)
        if reponse.status_code != 200:
            return f"Fichier introuvable (statut {reponse.status_code}) : {chemin} sur {repo}@{branche}"
        return reponse.text[:LONGUEUR_MAX_FICHIER]
    except Exception as e:
        logging.error(f"ERREUR lire_fichier_depot_github ({repo}/{chemin}) : {e}")
        return "Erreur : impossible de lire ce fichier, réessaie."


@mcp_github.tool()
def modifier_fichier_depot_github(
    repo: str,
    chemin: str,
    nouveau_contenu: str,
    message_commit: str,
    branche_base: str = "",
    mode: str = "branche_pr",
    user_id: str = "",
) -> str:
    """
    ÉCRIT un changement dans un fichier d'un dépôt GitHub -- crée le
    fichier s'il n'existe pas, le remplace sinon. Deux modes :
    "direct" (commit directement sur branche_base) ou "branche_pr"
    (crée une nouvelle branche + ouvre une Pull Request, jamais touché
    à branche_base directement -- mode recommandé, par défaut).
    NÉCESSITE que la personne soit connectée à son compte GitHub (accès
    en écriture) -- ne fonctionne jamais avec un token de plateforme
    partagé. Cet outil déclenche TOUJOURS une confirmation explicite
    avant d'être réellement exécuté, quel que soit le mode choisi.
    """
    if not user_id:
        return (
            "Impossible d'écrire sur GitHub : aucune personne connectée. "
            "Cette action nécessite que la personne connecte son propre "
            "compte GitHub (elle ne peut pas s'effectuer avec une clé "
            "partagée par la plateforme)."
        )

    token = obtenir_token_valide("github", user_id)
    if not token:
        return (
            "Impossible d'écrire sur GitHub : cette personne n'est pas "
            "connectée à son compte GitHub. Elle doit d'abord le connecter "
            "avant que cette action soit possible."
        )

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    try:
        if not branche_base:
            info = requests.get(f"https://api.github.com/repos/{repo}", timeout=10, headers=headers)
            if info.status_code != 200:
                return f"Dépôt introuvable ou inaccessible (statut {info.status_code}) : {repo}"
            branche_base = info.json().get("default_branch", "main")

        branche_cible = branche_base
        if mode == "branche_pr":
            ref_base = requests.get(
                f"https://api.github.com/repos/{repo}/git/refs/heads/{branche_base}",
                timeout=10,
                headers=headers,
            )
            if ref_base.status_code != 200:
                return f"Branche de base introuvable : {branche_base} sur {repo}"
            sha_base = ref_base.json()["object"]["sha"]

            import time
            branche_cible = f"djiguigne-modif-{int(time.time())}"
            creation_branche = requests.post(
                f"https://api.github.com/repos/{repo}/git/refs",
                timeout=10,
                headers=headers,
                json={"ref": f"refs/heads/{branche_cible}", "sha": sha_base},
            )
            if creation_branche.status_code != 201:
                return f"Impossible de créer la branche {branche_cible} (statut {creation_branche.status_code})"

        # Le PUT contents nécessite le sha du fichier existant s'il y en a
        # un (mise à jour), absent si c'est une création.
        sha_existant = None
        existant = requests.get(
            f"https://api.github.com/repos/{repo}/contents/{chemin}?ref={branche_cible}",
            timeout=10,
            headers=headers,
        )
        if existant.status_code == 200:
            sha_existant = existant.json().get("sha")

        import base64
        corps = {
            "message": message_commit,
            "content": base64.b64encode(nouveau_contenu.encode("utf-8")).decode("utf-8"),
            "branch": branche_cible,
        }
        if sha_existant:
            corps["sha"] = sha_existant

        ecriture = requests.put(
            f"https://api.github.com/repos/{repo}/contents/{chemin}",
            timeout=15,
            headers=headers,
            json=corps,
        )
        if ecriture.status_code not in (200, 201):
            logging.error(f"ERREUR ECRITURE GITHUB ({repo}/{chemin}) : {ecriture.status_code} {ecriture.text[:300]}")
            return f"Échec de l'écriture (statut {ecriture.status_code}) : {chemin} sur {repo}"

        if mode != "branche_pr":
            return f"Modification poussée directement sur {branche_base} : {chemin} dans {repo}."

        pr = requests.post(
            f"https://api.github.com/repos/{repo}/pulls",
            timeout=10,
            headers=headers,
            json={
                "title": message_commit,
                "head": branche_cible,
                "base": branche_base,
                "body": "Pull Request créée automatiquement par Djiguignè AI.",
            },
        )
        if pr.status_code != 201:
            return (
                f"Modification poussée sur la branche {branche_cible}, mais la "
                f"Pull Request n'a pas pu être créée (statut {pr.status_code}). "
                f"Ouvre-la manuellement depuis GitHub."
            )
        return f"Pull Request créée : {pr.json().get('html_url')}"
    except Exception as e:
        logging.error(f"ERREUR modifier_fichier_depot_github ({repo}/{chemin}) : {e}")
        return "Erreur : impossible d'écrire sur ce dépôt, réessaie."
