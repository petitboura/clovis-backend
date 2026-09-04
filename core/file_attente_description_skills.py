"""
File d'attente de génération de description manquante pour les skills
importés (04/09/2026, demande Bourama : bug remonté où l'import en masse
d'un pack de skills externe donnait des descriptions cassées type
"input_format: json" -- voir le correctif de la regex dans
importer_comportement_depuis_skill_md, core/comportements_etudiants.py).

Un skill importé (.md, individuel ou par dossier -- même pipeline, voir
POST /api/agents/{agent_id}/mes-comportements/importer) dont le
frontmatter n'a pas de champ `description` non vide arrive avec
statut_description="en_attente". Ce module tourne en arrière-plan (boucle
asyncio démarrée dans api/main.py, voir _boucle_description_skills) et
génère, pour chaque skill en attente, une vraie description à partir du
contenu du skill_md -- SANS jamais modifier texte ni skill_md eux-mêmes
(gardés tels quels, règle du 25/08/2026 sur l'import).

Même schéma/conventions que core/file_attente_vectorisation.py
(statut/tentatives/erreur/derniere_tentative), pour rester cohérent avec
ce qui existe déjà côté bibliothèque -- y compris la robustesse au
redémarrage (remettre_en_attente_bloques) et le réessai automatique à
froid après échec (relancer_echecs_a_froid).

Portée volontairement limitée aux imports à partir de ce correctif : les
skills déjà importés avant, avec une mauvaise description déjà en base,
ne sont PAS repris automatiquement (demande explicite Bourama, 04/09) --
ce module ne traite que statut_description="en_attente", jamais mis en
masse sur l'existant.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from groq import Groq
from supabase import create_client

MAX_TENTATIVES = 3
TAILLE_LOT = 5  # skills traités par passage -- garde-fou pour qu'un passage ne tourne jamais indéfiniment avant de rendre la main à la boucle appelante.

# Même logique que COOLDOWN_AUTO_REESSAI/MAX_TENTATIVES_AUTO dans
# file_attente_vectorisation.py : laisser une chance à une panne passagère
# (rate limit Groq, coupure réseau) de se résorber avant de retenter,
# plafonné pour ne jamais retenter indéfiniment un skill réellement
# impossible à décrire.
COOLDOWN_AUTO_REESSAI = timedelta(minutes=15)
MAX_TENTATIVES_AUTO = MAX_TENTATIVES + 3

# Même modèle "costaud" que la génération de skill complet
# (_generer_skill, core/comportements_etudiants.py) -- écrire une
# description utile au petit routeur mérite le même soin, et ce traitement
# tourne en arrière-plan (jamais dans le chemin critique d'une réponse),
# donc pas de raison de sacrifier la qualité pour la vitesse ici.
MODELE_DESCRIPTION = "openai/gpt-oss-120b"


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def _generer_description_depuis_skill_md(skill_md: str) -> str:
    """Lit le skill_md TEL QUEL (jamais modifié) et en tire une seule
    phrase de description, même exigence que dans _generer_skill : à la
    troisième personne, qui dit CE QUE fait le skill ET QUAND l'appliquer,
    avec des mots concrets qui déclenchent son usage -- c'est cette
    description, et seulement elle, qui alimente le petit routeur
    (choisir_comportements_pertinents)."""
    client = Groq(api_key=_get_secret("GROQ_API_KEY"), max_retries=0, timeout=20.0)
    completion = client.chat.completions.create(
        model=MODELE_DESCRIPTION,
        messages=[{
            "role": "user",
            "content": (
                "Voici un skill (fichier SKILL.md) importé par un étudiant, "
                "dont le frontmatter n'a pas de description utilisable. "
                "Lis son contenu et écris UNE SEULE phrase de description, "
                "à la troisième personne, qui dit CE QUE fait ce skill ET "
                "QUAND l'utiliser, avec des mots concrets qui déclenchent "
                "son usage (max 500 caractères). Réponds UNIQUEMENT avec "
                "cette phrase, rien d'autre autour, pas de guillemets.\n\n"
                f"Contenu du skill :\n{skill_md}"
            ),
        }],
        max_completion_tokens=200,
        timeout=20.0,
    )
    description = (completion.choices[0].message.content or "").strip().strip('"')
    if not description:
        raise ValueError("réponse vide du modèle")
    return description


def remettre_en_attente_bloques() -> None:
    """Appelée une fois au démarrage du process (voir _lifespan,
    api/main.py) -- tout skill resté "en_cours" suite à un
    redémarrage/crash précédent repart tout seul, jamais besoin
    d'intervention manuelle. Même rôle que son équivalent dans
    file_attente_vectorisation.py."""
    try:
        supabase.table("comportements_etudiants").update({"statut_description": "en_attente"}).eq(
            "statut_description", "en_cours"
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR remise en attente au démarrage (description skills) : {e}")


def _traiter_lot() -> int:
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("id, skill_md, tentatives_description")
            .eq("statut_description", "en_attente")
            .limit(TAILLE_LOT)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture file d'attente description skills) : {e}")
        return 0

    lignes = res.data or []
    for ligne in lignes:
        comportement_id = ligne["id"]
        supabase.table("comportements_etudiants").update({"statut_description": "en_cours"}).eq(
            "id", comportement_id
        ).execute()
        try:
            description = _generer_description_depuis_skill_md(ligne.get("skill_md") or "")
            supabase.table("comportements_etudiants").update(
                {
                    "description": description,
                    "statut_description": "pret",
                    "erreur_description": None,
                }
            ).eq("id", comportement_id).execute()
        except Exception as e:
            tentatives = (ligne.get("tentatives_description") or 0) + 1
            statut = "echec" if tentatives >= MAX_TENTATIVES else "en_attente"
            logging.error(f"ERREUR génération description skill {comportement_id} (tentative {tentatives}) : {e}")
            supabase.table("comportements_etudiants").update(
                {
                    "statut_description": statut,
                    "tentatives_description": tentatives,
                    "erreur_description": str(e)[:500],
                    "derniere_tentative_description_a": datetime.now(timezone.utc).isoformat(),
                }
            ).eq("id", comportement_id).execute()
    return len(lignes)


def traiter_file_attente_une_fois() -> int:
    return _traiter_lot()


def relancer_echecs_a_froid() -> int:
    """Repasse un skill "echec" à "en_attente" après le cooldown, tant que
    MAX_TENTATIVES_AUTO n'est pas dépassé -- même mécanisme que pour la
    bibliothèque (voir docstring du module)."""
    limite = (datetime.now(timezone.utc) - COOLDOWN_AUTO_REESSAI).isoformat()
    try:
        res = (
            supabase.table("comportements_etudiants")
            .update({"statut_description": "en_attente"})
            .eq("statut_description", "echec")
            .lt("tentatives_description", MAX_TENTATIVES_AUTO)
            .lt("derniere_tentative_description_a", limite)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR réessai à froid description skills : {e}")
        return 0
    return len(res.data or [])
