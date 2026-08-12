"""
Logique pure de création d'agent : génération d'id, extraction d'id
Notion, composition du system prompt. Zéro dépendance à Streamlit —
importable aussi bien depuis l'ancien formulaire Streamlit que depuis
api/agents.py (endpoint FastAPI), pour que les
deux ne dupliquent jamais cette logique.

Extrait de l'ancien formulaire Streamlit au moment de construire l'API,
sans changement de comportement.
"""

import re


def generer_id_depuis_nom(nom):
    """
    Transforme "Coach fitness" en "coach-fitness". Doit rester unique dans
    la table `agents` (clé primaire texte) — la vérification d'unicité se
    fait à l'appel (Supabase), pas ici.
    """
    slug = nom.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def extraire_id_notion(lien_ou_id):
    """
    Accepte soit un lien Notion complet (https://www.notion.so/Titre-xxxxx),
    soit l'ID brut (avec ou sans tirets), et retourne toujours l'ID au
    format UUID standard attendu par indexers/index_notion.py (déjà
    multi-agent, lit agents.notion_page_id pour chaque agent).
    Retourne None si rien d'exploitable n'est trouvé (champ optionnel).

    2026-07-17 (bug remonté par Bourama, "le lien Notion ne fonctionne
    pas") : tout lien copié via le bouton "Copier le lien" de Notion se
    termine par une query string (ex: "?pvs=4"), dont les chiffres sont
    eux-mêmes des caractères hexadécimaux valides. Sans les retirer
    D'ABORD, ils se retrouvaient collés après le véritable ID et
    décalaient la fenêtre des "32 derniers caractères hexadécimaux" --
    l'ID extrait et stocké en base était corrompu (décalé de 1-2
    caractères) pour quasiment tout lien copié normalement depuis
    Notion, d'où l'échec silencieux de l'appel à l'API Notion ensuite.
    """
    if not lien_ou_id or not lien_ou_id.strip():
        return None
    lien_sans_requete = lien_ou_id.strip().split("?")[0]
    hex_seul = re.sub(r"[^a-f0-9]", "", lien_sans_requete.lower())
    if len(hex_seul) < 32:
        return None
    brut = hex_seul[-32:]
    return f"{brut[0:8]}-{brut[8:12]}-{brut[12:16]}-{brut[16:20]}-{brut[20:32]}"


def composer_system_prompt(
    ton, posture_generale, limites_globales, lignes_comportement,
    type_connaissance, description_connaissance,
    nom="", description_publique="",
):
    """
    Assemble les champs structurés des points 1, 2 et 4 du cadre de
    conception en UN SEUL texte brut (jamais de JSON) : c'est ce texte,
    et rien d'autre, qui est envoyé au LLM comme system prompt (voir
    core/configuration.py, colonne agents.system_prompt).

    `lignes_comportement` : liste de tuples (type_requete, comportement),
    les lignes vides (l'un des deux champs vide) sont ignorées.

    `nom` et `description_publique` (2026-07-12, bug remonté par
    Bourama : "le nom que tu donnes à ton agent doit être automatiquement
    dans le system prompt, la description publique aussi") : injectés
    automatiquement en tête de prompt -- l'agent doit savoir qui il est
    censé être et comment il se présente publiquement, sans que le
    créateur ait à ressaisir cette information une seconde fois dans un
    champ séparé. Paramètres optionnels (chaîne vide = rien n'est ajouté)
    pour ne pas casser les appels existants qui ne les fournissent pas
    encore.

    `type_connaissance` optionnel (2026-07-12, même remontée : le champ
    "Nature de la connaissance" est retiré du formulaire Next.js) : la
    ligne correspondante n'apparaît dans le prompt QUE si une valeur est
    fournie, pour ne pas afficher une classification vide de sens à
    l'agent. Le formulaire Streamlit (l'ancien formulaire Streamlit), qui
    garde ce champ pour l'instant, continue de fonctionner à l'identique.

    Reste modifiable tel quel ensuite par le créateur depuis "Mes
    agents" — composition automatique à la création, texte libre
    éditable après coup.
    """
    parties = []

    bloc_identite = []
    if nom.strip():
        bloc_identite.append(f"Tu es {nom.strip()}.")
    if description_publique.strip():
        bloc_identite.append(f"Description publique (vue par les visiteurs) : {description_publique.strip()}")
    bloc_identite.append(f"Ton : {ton}.")
    if posture_generale.strip():
        bloc_identite.append(f"Posture générale : {posture_generale.strip()}.")
    if limites_globales.strip():
        bloc_identite.append(
            f"Limites globales, à ne jamais franchir : {limites_globales.strip()}"
        )
    parties.append("## Identité\n" + "\n".join(bloc_identite))

    lignes_utiles = [
        (t.strip(), c.strip()) for t, c in lignes_comportement if t.strip() and c.strip()
    ]
    if lignes_utiles:
        bloc_comportement = "\n".join(f"- {t} : {c}" for t, c in lignes_utiles)
        parties.append("## Comportement selon le type de requête\n" + bloc_comportement)

    bloc_connaissance = []
    if type_connaissance.strip():
        bloc_connaissance.append(f"Nature de la connaissance : {type_connaissance.strip()}.")
    if description_connaissance.strip():
        bloc_connaissance.append(f"Contenu : {description_connaissance.strip()}")
    if bloc_connaissance:
        parties.append("## Base de connaissance\n" + "\n".join(bloc_connaissance))

    return "\n\n".join(parties)
