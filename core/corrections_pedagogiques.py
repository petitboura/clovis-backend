"""
Corrections pédagogiques élève -> prof (Point 2, Partie 4 du chantier
"confiance pédagogique", 06/09/2026, demande Bourama).

Système ENTIÈREMENT SÉPARÉ du like/dislike existant (voir
core/../api/feedback.py, table feedback_messages) et sans rapport avec
api/signalements.py (droit d'auteur bibliothèque publique), nom
volontairement différent pour ne jamais confondre les deux.

Deux types, distincts dès la création (jamais de reclassement a
posteriori, décision actée par Bourama) :
- type "A" : correctif de fond sur une notion/méthode, le prof
  corrige dans le chat (voir core/outils_corrections_pedagogiques.py et
  core/outils_corrections_espace.py), la correction est généralisée par
  LLM (core/generalisation_correction_pedagogique.py) puis rattachée au
  système de comportements/skills déjà existant
  (core/comportements_etudiants.py), la correction devient un
  comportement comme un autre une fois généralisée.
- type "B" : comportement général mal configuré, pas de flux de
  correction ici, la donnée est simplement capturée avec son champ
  statut_cascade prêt à l'emploi pour la Partie 10 (pas encore
  construite, ce fichier ne fait qu'écrire/lire la ligne).

Résolution du prof (resoudre_prof_actif ci-dessous) : un élève n'a
jamais plus d'un prof actif en pratique à ce stade du produit (confirmé
par Bourama, 06/09), on prend le rattachement actif le plus ancien
s'il y en avait malgré tout plusieurs, jamais une erreur bloquante.

Gestion complète pour le prof (désactiver/dupliquer/éditer/
supprimer/déplacer une correction de type A, "contrôle total demandé") :
toutes les fonctions ci-dessous vérifient la propriété via
corrections_pedagogiques.prof_id, PAS via l'appartenance étudiant
utilisée partout ailleurs dans core/comportements_etudiants.py (le prof
n'est jamais le propriétaire du comportement au sens de cette table-là,
d'où la vérification dédiée ici avant toute écriture directe sur
comportements_etudiants).
"""

import logging
import os

from supabase import create_client

from core.comportements_etudiants import ajouter_comportement as _ajouter_comportement
from core.generalisation_correction_pedagogique import (
    generaliser_correction_pedagogique as _generaliser_correction_pedagogique,
)
from core.mode_actif_conversation import obtenir_mode_actif as _obtenir_mode_actif
from core.notifications import creer_notification as _creer_notification


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

TYPES_VALIDES = {"A", "B"}

# Nombre de messages de contexte capturés AVANT la réponse signalée
# (snapshot pris une fois pour toutes à la création, le prof voit la
# conversation telle qu'elle était au moment du signalement, pas une
# relecture en direct qui pourrait avoir bougé depuis).
_LIMITE_CONTEXTE = 6


def _ligne_publique(ligne: dict) -> dict:
    return {
        "id": ligne["id"],
        "agent_id": ligne["agent_id"],
        "etudiant_id": ligne["etudiant_id"],
        "prof_id": ligne.get("prof_id"),
        "type": ligne["type"],
        "conversation_id": ligne.get("conversation_id"),
        "question_texte": ligne.get("question_texte") or "",
        "reponse_texte": ligne.get("reponse_texte") or "",
        "contexte_conversation": ligne.get("contexte_conversation") or [],
        "statut": ligne.get("statut", "nouveau"),
        "correction_texte": ligne.get("correction_texte"),
        "comportement_id": ligne.get("comportement_id"),
        # Rempli par _enrichir_comportement_actif (lister_corrections_prof/
        # obtenir_correction) -- None tant que non enrichi (ex: juste après
        # creer_correction, avant tout comportement généré) ou si la
        # correction n'a pas encore de comportement_id (type B, ou type A
        # pas encore traité).
        "comportement_actif": ligne.get("comportement_actif"),
        "notion_id": ligne.get("notion_id"),
        "statut_cascade": ligne.get("statut_cascade"),
        "created_at": ligne.get("created_at"),
        "updated_at": ligne.get("updated_at"),
    }


def _enrichir_comportement_actif(lignes: list[dict]) -> list[dict]:
    """Complète chaque ligne déjà passée par _ligne_publique avec l'état
    actif/inactif RÉEL du comportement généré (Partie 5, 06/09/2026) --
    corrections_pedagogiques ne duplique pas cette colonne, nécessaire
    pour que le frontend affiche un bouton activer/désactiver cohérent
    avec l'état réel plutôt que de le deviner. Un seul aller-retour
    Supabase pour toute la liste (IN sur les ids), jamais un appel par
    ligne."""
    ids = [l["comportement_id"] for l in lignes if l.get("comportement_id")]
    if not ids:
        return lignes
    try:
        res = supabase.table("comportements_etudiants").select("id, actif").in_("id", ids).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture actif comportements des corrections) : {e}")
        return lignes
    actifs_par_id = {c["id"]: c.get("actif", True) for c in (res.data or [])}
    for l in lignes:
        if l.get("comportement_id"):
            l["comportement_actif"] = actifs_par_id.get(l["comportement_id"])
    return lignes


def resoudre_prof_actif(etudiant_id: str, conversation_id: str | None = None) -> str | None:
    """Prof (propriétaire de code) auquel rattacher un signalement.

    Utilise en priorité le mode actif choisi par l'élève pour CETTE
    conversation (core/mode_actif_conversation.py, Partie 6, poussé en
    parallèle le 06/09, exactement le mécanisme anticipé par sa propre
    docstring : "pour la résolution du bon scope de comportement"). Un
    élève peut avoir plusieurs codes rattachés, mais un seul est actif
    pour une conversation donnée (confirmé par Bourama, 06/09) : il n'y
    a donc jamais d'ambiguïté une fois le mode actif connu.

    Repli (aucune conversation_id fournie, ou aucun mode actif choisi
    pour cette conversation) : prend le rattachement actif le plus
    ancien de cet élève, tous codes confondus, ne bloque jamais un
    signalement pour cette raison, mais journalise le cas s'il y a
    plusieurs rattachements possibles (état ambigu, à ne pas deviner
    silencieusement)."""
    if conversation_id:
        try:
            mode = _obtenir_mode_actif(conversation_id, etudiant_id)
        except Exception as e:
            logging.error(f"ERREUR lecture mode actif ({conversation_id}, {etudiant_id}) : {e}")
            mode = None
        if mode and mode.get("rattachement_id"):
            try:
                res_mode = (
                    supabase.table("rattachements_codes")
                    .select("codes_partage!inner(proprietaire_id)")
                    .eq("id", mode["rattachement_id"])
                    .maybe_single()
                    .execute()
                )
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (résolution du rattachement actif {mode['rattachement_id']}) : {e}")
                res_mode = None
            if res_mode and res_mode.data and res_mode.data.get("codes_partage"):
                return res_mode.data["codes_partage"]["proprietaire_id"]

    try:
        res = (
            supabase.table("rattachements_codes")
            .select("created_at, codes_partage!inner(proprietaire_id, actif)")
            .eq("receveur_id", etudiant_id)
            .eq("codes_partage.actif", True)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (résolution prof actif de {etudiant_id}) : {e}")
        return None

    lignes = [l for l in (res.data or []) if l.get("codes_partage")]
    if not lignes:
        return None
    if len(lignes) > 1:
        logging.warning(
            f"Élève {etudiant_id} rattaché à plusieurs profs actifs sans mode actif choisi pour "
            f"cette conversation ({len(lignes)}), prise du plus ancien rattachement."
        )
    return lignes[0]["codes_partage"]["proprietaire_id"]


def _capturer_contexte(conversation_id: str | None, avant_message_id: int | None) -> list[dict]:
    """Snapshot des derniers messages de la conversation avant le
    message signalé, vide si conversation_id/avant_message_id
    manquants (signalement fait en dehors d'un fil identifié)."""
    if not conversation_id or not avant_message_id:
        return []
    try:
        res = (
            supabase.table("historique_conversations")
            .select("role, content")
            .eq("conversation_id", conversation_id)
            .lt("id", avant_message_id)
            .order("id", desc=True)
            .limit(_LIMITE_CONTEXTE)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (capture contexte conversation {conversation_id}) : {e}")
        return []
    return list(reversed(res.data or []))


def creer_correction(
    agent_id: str,
    etudiant_id: str,
    type_: str,
    conversation_id: str | None,
    question_message_id: int | None,
    reponse_message_id: int | None,
    question_texte: str,
    reponse_texte: str,
) -> dict | None:
    """Crée un signalement type A ou B. Le prof est résolu automatiquement
    (resoudre_prof_actif), jamais fourni par l'appelant (l'élève ne
    choisit pas "à qui" il signale, il n'y a qu'un prof actif possible).
    """
    type_ = (type_ or "").strip().upper()
    if type_ not in TYPES_VALIDES:
        raise ValueError("TYPE_INVALIDE")

    prof_id = resoudre_prof_actif(etudiant_id, conversation_id)
    contexte = _capturer_contexte(conversation_id, reponse_message_id)

    ligne_a_inserer = {
        "agent_id": agent_id,
        "etudiant_id": etudiant_id,
        "prof_id": prof_id,
        "type": type_,
        "conversation_id": conversation_id,
        "question_message_id": question_message_id,
        "reponse_message_id": reponse_message_id,
        "question_texte": (question_texte or "").strip(),
        "reponse_texte": (reponse_texte or "").strip(),
        "contexte_conversation": contexte,
    }
    try:
        res = supabase.table("corrections_pedagogiques").insert(ligne_a_inserer).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création correction pédagogique, etudiant={etudiant_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def lister_corrections_prof(prof_id: str, type_: str | None = None, statut: str | None = None) -> list[dict]:
    """Corrections reçues par ce prof (via ses codes), plus récentes en
    dernier. Filtre optionnel par type ("A"/"B") et par statut
    ("nouveau"/"traite", type A uniquement en pratique)."""
    requete = supabase.table("corrections_pedagogiques").select("*").eq("prof_id", prof_id)
    if type_:
        requete = requete.eq("type", type_.strip().upper())
    if statut:
        requete = requete.eq("statut", statut)
    try:
        res = requete.order("created_at").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste corrections du prof {prof_id}) : {e}")
        return []
    return _enrichir_comportement_actif([_ligne_publique(l) for l in (res.data or [])])


def lister_corrections_etudiant(etudiant_id: str, type_: str | None = None) -> list[dict]:
    """Corrections envoyées par CET élève (pour qu'il retrouve ses
    propres signalements, indépendamment de qui les traite)."""
    requete = supabase.table("corrections_pedagogiques").select("*").eq("etudiant_id", etudiant_id)
    if type_:
        requete = requete.eq("type", type_.strip().upper())
    try:
        res = requete.order("created_at").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste corrections de l'élève {etudiant_id}) : {e}")
        return []
    return [_ligne_publique(l) for l in (res.data or [])]


def obtenir_correction(correction_id: str) -> dict | None:
    try:
        res = (
            supabase.table("corrections_pedagogiques")
            .select("*")
            .eq("id", correction_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture correction {correction_id}) : {e}")
        return None
    if not res or not res.data:
        return None
    return _enrichir_comportement_actif([_ligne_publique(res.data)])[0]


def _correction_du_prof(correction_id: str, prof_id: str) -> dict | None:
    """Ligne BRUTE (pas _ligne_publique) si cette correction appartient
    bien à ce prof et est de type A, None sinon (introuvable, pas à ce
    prof, ou type B qui n'a pas de flux de correction par le prof)."""
    try:
        res = (
            supabase.table("corrections_pedagogiques")
            .select("*")
            .eq("id", correction_id)
            .eq("prof_id", prof_id)
            .eq("type", "A")
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture correction {correction_id} pour prof {prof_id}) : {e}")
        return None
    return res.data if res and res.data else None


def enregistrer_correction_prof(
    correction_id: str,
    prof_id: str,
    question: str,
    reponse_fautive: str,
    contexte_conversation: str,
    correction_texte: str,
) -> dict | None:
    """Enregistre la correction dictée par le prof dans le chat (voir
    core/outils_corrections_pedagogiques.py / outils_corrections_espace.py) :
    généralise la correction par LLM, crée le comportement correspondant
    chez l'ÉLÈVE (etudiant_id de la correction, pas le prof, c'est
    l'élève qui reçoit la règle), marque la correction "traite".

    question/reponse_fautive/contexte_conversation sont ce que le prof a
    sous les yeux au moment de corriger (voir Partie 5, conversation
    pré-remplie), écrasent la capture faite à la création par l'élève,
    pour rester alignés avec ce que la génération LLM a réellement reçu.
    """
    ligne = _correction_du_prof(correction_id, prof_id)
    if not ligne:
        return None

    regle_generalisee = _generaliser_correction_pedagogique(
        question=question, reponse_fautive=reponse_fautive,
        contexte=contexte_conversation, correction_prof=correction_texte,
    )

    comportement = _ajouter_comportement(ligne["agent_id"], ligne["etudiant_id"], regle_generalisee)

    try:
        res = (
            supabase.table("corrections_pedagogiques")
            .update({
                "question_texte": (question or ligne.get("question_texte") or "").strip(),
                "reponse_texte": (reponse_fautive or ligne.get("reponse_texte") or "").strip(),
                "contexte_conversation": [{"role": "note_prof", "content": contexte_conversation or ""}]
                if contexte_conversation else ligne.get("contexte_conversation") or [],
                "correction_texte": (correction_texte or "").strip(),
                "comportement_id": comportement["id"],
                "statut": "traite",
            })
            .eq("id", correction_id)
            .eq("prof_id", prof_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement correction {correction_id}) : {e}")
        return None
    if not res.data:
        return None

    # Notification élève (Partie 5, 06/09/2026) : best-effort, une
    # notification manquée ne doit jamais faire échouer l'enregistrement
    # de la correction elle-même (même principe que partout ailleurs où
    # creer_notification est appelée, voir core/notifications.py).
    # correction_texte tronqué (200 caractères) : pas de page dédiée
    # élève pour relire la correction en entier pour l'instant, la
    # notification elle-même porte donc l'essentiel du contenu utile.
    try:
        apercu = (correction_texte or "").strip()
        if len(apercu) > 200:
            apercu = apercu[:200].rstrip() + "…"
        _creer_notification(
            ligne["etudiant_id"],
            "correction_traitee",
            "Ta correction a été traitée",
            apercu or None,
        )
    except Exception as e:
        logging.error(f"ERREUR notification correction traitée {correction_id} : {e}")

    return _enrichir_comportement_actif([_ligne_publique(res.data[0])])[0]


def desactiver_activer_correction(correction_id: str, prof_id: str, actif: bool) -> dict | None:
    """Active/désactive le comportement généré par cette correction :
    écriture DIRECTE sur comportements_etudiants (jamais via
    core.comportements_etudiants.activer_desactiver_comportement, qui
    vérifie une propriété étudiant que le prof n'a pas) après
    vérification que cette correction appartient bien à ce prof."""
    ligne = _correction_du_prof(correction_id, prof_id)
    if not ligne or not ligne.get("comportement_id"):
        return None
    try:
        res = (
            supabase.table("comportements_etudiants")
            .update({"actif": actif})
            .eq("id", ligne["comportement_id"])
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (activation comportement correction {correction_id}) : {e}")
        return None
    if not res.data:
        return None
    return obtenir_correction(correction_id)


def editer_correction(correction_id: str, prof_id: str, correction_texte: str) -> dict | None:
    """Remplace le texte de correction du prof, régénère la règle
    généralisée et le comportement associé (même principe que
    core.comportements_etudiants.modifier_comportement : on régénère
    toujours depuis le texte source, jamais une édition partielle du
    skill déjà généré)."""
    ligne = _correction_du_prof(correction_id, prof_id)
    if not ligne or not ligne.get("comportement_id"):
        return None

    regle_generalisee = _generaliser_correction_pedagogique(
        question=ligne.get("question_texte") or "",
        reponse_fautive=ligne.get("reponse_texte") or "",
        contexte="",
        correction_prof=correction_texte,
    )

    from core.comportements_etudiants import modifier_comportement as _modifier_comportement
    comportement = _modifier_comportement(ligne["agent_id"], ligne["etudiant_id"], ligne["comportement_id"], regle_generalisee)
    if comportement is None:
        return None

    try:
        res = (
            supabase.table("corrections_pedagogiques")
            .update({"correction_texte": (correction_texte or "").strip()})
            .eq("id", correction_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (édition correction {correction_id}) : {e}")
        return None
    if not res.data:
        return None
    return _enrichir_comportement_actif([_ligne_publique(res.data[0])])[0]


def dupliquer_correction(correction_id: str, prof_id: str) -> dict | None:
    """Duplique une correction déjà traitée : nouveau comportement
    (copie indépendante, chez le même élève) + nouvelle ligne
    corrections_pedagogiques (statut "traite" d'emblée, mêmes
    question/réponse d'origine, pour que la copie apparaisse comme un
    élément séparé et gérable indépendamment de l'original)."""
    ligne = _correction_du_prof(correction_id, prof_id)
    if not ligne or not ligne.get("comportement_id"):
        return None

    source_comportement = (
        supabase.table("comportements_etudiants")
        .select("texte")
        .eq("id", ligne["comportement_id"])
        .maybe_single()
        .execute()
    )
    if not source_comportement or not source_comportement.data:
        return None

    copie = _ajouter_comportement(ligne["agent_id"], ligne["etudiant_id"], source_comportement.data["texte"])

    nouvelle_ligne = {
        "agent_id": ligne["agent_id"],
        "etudiant_id": ligne["etudiant_id"],
        "prof_id": prof_id,
        "type": "A",
        "conversation_id": ligne.get("conversation_id"),
        "question_message_id": ligne.get("question_message_id"),
        "reponse_message_id": ligne.get("reponse_message_id"),
        "question_texte": ligne.get("question_texte") or "",
        "reponse_texte": ligne.get("reponse_texte") or "",
        "contexte_conversation": ligne.get("contexte_conversation") or [],
        "statut": "traite",
        "correction_texte": ligne.get("correction_texte"),
        "comportement_id": copie["id"],
    }
    try:
        res = supabase.table("corrections_pedagogiques").insert(nouvelle_ligne).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (duplication correction {correction_id}) : {e}")
        return None
    if not res.data:
        return None
    return _enrichir_comportement_actif([_ligne_publique(res.data[0])])[0]


def supprimer_correction(correction_id: str, prof_id: str) -> bool:
    """Supprime définitivement la correction ET le comportement généré
    (la correction n'a pas d'existence utile sans son comportement, et
    inversement un comportement issu d'une correction supprimée ne doit
    pas rester actif silencieusement)."""
    ligne = _correction_du_prof(correction_id, prof_id)
    if not ligne:
        return False
    if ligne.get("comportement_id"):
        try:
            supabase.table("comportements_etudiants").delete().eq("id", ligne["comportement_id"]).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (suppression comportement de la correction {correction_id}) : {e}")
    try:
        res = supabase.table("corrections_pedagogiques").delete().eq("id", correction_id).eq("prof_id", prof_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression correction {correction_id}) : {e}")
        return False
    return bool(res.data)


def deplacer_correction(correction_id: str, prof_id: str, lien_type: str | None, lien_id: str | None) -> dict | None:
    """Change le rattachement (notion/chapitre/matière) du comportement
    généré par cette correction, voir core.comportements_etudiants
    pour la sémantique de lien_type/lien_id. Fonctionne en DÉGRADÉ tant
    que la Partie 1 (structure de notions) n'existe pas : lien_type/
    lien_id restent alors None/None (portée "général"), aucune erreur
    bloquante, voir dépendance documentée dans le plan de travail
    Partie 4."""
    ligne = _correction_du_prof(correction_id, prof_id)
    if not ligne or not ligne.get("comportement_id"):
        return None

    from core.comportements_etudiants import attacher_comportement as _attacher_comportement
    comportement = _attacher_comportement(ligne["agent_id"], ligne["etudiant_id"], ligne["comportement_id"], lien_type, lien_id)
    if comportement is None:
        return None
    return obtenir_correction(correction_id)
