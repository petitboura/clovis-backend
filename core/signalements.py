"""
Signalements élève -> prof (refonte du 10/09/2026, demande Bourama --
remplace intégralement l'ancien système "corrections_pedagogiques" :
plus de type A/B, plus de génération automatique de comportement/skill,
plus de cascade de supervision, plus de neutralisation automatique.
Le système ne touche JAMAIS aux comportements de l'élève et n'agit
JAMAIS tout seul.

Système ENTIÈREMENT SÉPARÉ du like/dislike existant (voir
api/feedback.py, table feedback_messages) et sans rapport avec
api/signalements.py (droit d'auteur bibliothèque publique) malgré le
nom -- il n'y avait pas de collision de table (feedback_messages vs
signalements ci-dessous), gardé volontairement pour coller au mot que
Bourama utilise.

Principe général :
- Un signalement peut porter sur N'IMPORTE QUOI (notion fausse,
  comportement mal réglé, ressenti d'une IA mal configurée par le
  prof) -- un seul type, plus de distinction A/B.
- L'élève choisit CE QU'IL PARTAGE (question / réponse / conversation),
  capturés automatiquement comme avant, mais visibles au prof
  seulement si l'élève l'autorise (visible_question/visible_reponse/
  visible_conversation). Champ libre "probleme_observe", optionnel.
- Le prof peut demander l'accès à ce qui manque (demande_prof_*) ;
  l'élève voit une notification et coche oui/non par élément, sans
  ressaisir de texte (voir confirmer_visibilite ci-dessous).
- Le traitement se fait dans une conversation dédiée où le prof discute
  librement avec le LLM (voir core/outils_signalements.py) -- ni
  obligation de longueur, ni bouton "valider" obligatoire. Le champ
  correction_texte sert de note de synthèse, modifiable à tout moment
  pendant la discussion, jamais transformée en comportement.
- Rattachement à une matière (codes_partage) et/ou une notion précise
  (notions, rattachée à codes_partage via code_id -- PAS la table
  matieres/chapitres, qui appartient à un autre chantier), les deux
  combinables, pour que la consultation par le LLM (core/
  outils_signalements.py, consultation) reste ciblée et économique.
"""

import logging
import os

from supabase import create_client

from core.mode_actif_conversation import obtenir_mode_actif as _obtenir_mode_actif
from core.notifications import creer_notification as _creer_notification


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)

# Nombre de messages de contexte capturés AVANT la réponse signalée
# (snapshot pris une fois pour toutes à la création, le prof voit la
# conversation telle qu'elle était au moment du signalement, pas une
# relecture en direct qui pourrait avoir bougé depuis).
_LIMITE_CONTEXTE = 6

CHAMPS_VISIBILITE = ("question", "reponse", "conversation")


def _ligne_publique(ligne: dict) -> dict:
    return {
        "id": ligne["id"],
        "agent_id": ligne["agent_id"],
        "etudiant_id": ligne["etudiant_id"],
        "prof_id": ligne.get("prof_id"),
        "conversation_id": ligne.get("conversation_id"),
        "question_texte": ligne.get("question_texte") or "",
        "reponse_texte": ligne.get("reponse_texte") or "",
        "contexte_conversation": ligne.get("contexte_conversation") or [],
        "probleme_observe": ligne.get("probleme_observe"),
        "visible_question": ligne.get("visible_question", True),
        "visible_reponse": ligne.get("visible_reponse", True),
        "visible_conversation": ligne.get("visible_conversation", False),
        "demande_prof_question": ligne.get("demande_prof_question", False),
        "demande_prof_reponse": ligne.get("demande_prof_reponse", False),
        "demande_prof_conversation": ligne.get("demande_prof_conversation", False),
        "code_id": ligne.get("code_id"),
        "notion_id": ligne.get("notion_id"),
        "statut": ligne.get("statut", "nouveau"),
        "correction_texte": ligne.get("correction_texte"),
        "conversation_discussion_id": ligne.get("conversation_discussion_id"),
        "created_at": ligne.get("created_at"),
        "updated_at": ligne.get("updated_at"),
    }


def resoudre_prof_actif(etudiant_id: str, conversation_id: str | None = None) -> str | None:
    """Inchangé par rapport à l'ancien système (aucun lien avec les
    skills) : prof (propriétaire de code) auquel rattacher un
    signalement, via le mode actif de la conversation en priorité, sinon
    le rattachement actif le plus ancien de l'élève."""
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


def creer_signalement(
    agent_id: str,
    etudiant_id: str,
    conversation_id: str | None,
    question_message_id: int | None,
    reponse_message_id: int | None,
    question_texte: str,
    reponse_texte: str,
    probleme_observe: str | None,
    visible_question: bool,
    visible_reponse: bool,
    visible_conversation: bool,
) -> dict | None:
    """Crée un signalement. Le prof est résolu automatiquement (l'élève
    ne choisit pas "à qui"). probleme_observe est optionnel."""
    prof_id = resoudre_prof_actif(etudiant_id, conversation_id)
    contexte = _capturer_contexte(conversation_id, reponse_message_id)

    ligne_a_inserer = {
        "agent_id": agent_id,
        "etudiant_id": etudiant_id,
        "prof_id": prof_id,
        "conversation_id": conversation_id,
        "question_message_id": question_message_id,
        "reponse_message_id": reponse_message_id,
        "question_texte": (question_texte or "").strip(),
        "reponse_texte": (reponse_texte or "").strip(),
        "contexte_conversation": contexte,
        "probleme_observe": (probleme_observe or "").strip() or None,
        "visible_question": bool(visible_question),
        "visible_reponse": bool(visible_reponse),
        "visible_conversation": bool(visible_conversation),
    }
    try:
        res = supabase.table("signalements").insert(ligne_a_inserer).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création signalement, etudiant={etudiant_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def lister_signalements_prof(prof_id: str, statut: str | None = None) -> list[dict]:
    requete = supabase.table("signalements").select("*").eq("prof_id", prof_id)
    if statut:
        requete = requete.eq("statut", statut)
    try:
        res = requete.order("created_at").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste signalements du prof {prof_id}) : {e}")
        return []
    return [_ligne_publique(l) for l in (res.data or [])]


def lister_signalements_etudiant(etudiant_id: str) -> list[dict]:
    try:
        res = (
            supabase.table("signalements")
            .select("*")
            .eq("etudiant_id", etudiant_id)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste signalements de l'élève {etudiant_id}) : {e}")
        return []
    return [_ligne_publique(l) for l in (res.data or [])]


def obtenir_signalement(signalement_id: str) -> dict | None:
    try:
        res = supabase.table("signalements").select("*").eq("id", signalement_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture signalement {signalement_id}) : {e}")
        return None
    if not res or not res.data:
        return None
    return _ligne_publique(res.data)


def _signalement_du_prof(signalement_id: str, prof_id: str) -> dict | None:
    try:
        res = (
            supabase.table("signalements")
            .select("*")
            .eq("id", signalement_id)
            .eq("prof_id", prof_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture signalement {signalement_id} pour prof {prof_id}) : {e}")
        return None
    return res.data if res and res.data else None


def demander_visibilite(signalement_id: str, prof_id: str, champs: list[str]) -> dict | None:
    """Le prof demande l'accès à un ou plusieurs éléments non partagés
    (question/reponse/conversation). Notifie l'élève, qui confirme
    ensuite champ par champ (confirmer_visibilite) sans rien ressaisir."""
    ligne = _signalement_du_prof(signalement_id, prof_id)
    if not ligne:
        return None
    champs_valides = [c for c in champs if c in CHAMPS_VISIBILITE and not ligne.get(f"visible_{c}", False)]
    if not champs_valides:
        return _ligne_publique(ligne)
    patch = {f"demande_prof_{c}": True for c in champs_valides}
    try:
        res = supabase.table("signalements").update(patch).eq("id", signalement_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (demande visibilité {signalement_id}) : {e}")
        return None
    if not res.data:
        return None
    try:
        _creer_notification(
            ligne["etudiant_id"],
            "signalement_demande_visibilite",
            "Ton prof demande à voir plus de détails sur un signalement",
            "Il manque des éléments pour qu'il puisse t'aider, confirme ce que tu veux bien partager.",
            lien=f"/signalements/{signalement_id}",
        )
    except Exception as e:
        logging.error(f"ERREUR notification demande visibilité {signalement_id} : {e}")
    return _ligne_publique(res.data[0])


def confirmer_visibilite(signalement_id: str, etudiant_id: str, reponses: dict[str, bool]) -> dict | None:
    """L'élève répond oui/non à chaque élément demandé par le prof.
    Un refus (False) efface juste la demande sans jamais rendre visible
    ce que l'élève ne veut pas partager."""
    try:
        res_lecture = (
            supabase.table("signalements")
            .select("*")
            .eq("id", signalement_id)
            .eq("etudiant_id", etudiant_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture signalement {signalement_id} pour élève {etudiant_id}) : {e}")
        return None
    ligne = res_lecture.data if res_lecture and res_lecture.data else None
    if not ligne:
        return None

    patch = {}
    for champ, accorde in (reponses or {}).items():
        if champ not in CHAMPS_VISIBILITE:
            continue
        if not ligne.get(f"demande_prof_{champ}", False):
            continue
        patch[f"demande_prof_{champ}"] = False
        if accorde:
            patch[f"visible_{champ}"] = True
    if not patch:
        return _ligne_publique(ligne)

    try:
        res = supabase.table("signalements").update(patch).eq("id", signalement_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (confirmation visibilité {signalement_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def ouvrir_discussion(signalement_id: str, prof_id: str, conversation_discussion_id: str) -> dict | None:
    """Rattache la conversation dédiée où le prof discute avec le LLM.
    Appelée par le frontend à la création de cette conversation (voir
    ListeCorrectionsProf.tsx), avant tout message -- aucune magie côté
    MCP, l'id est ensuite un paramètre explicite des outils du LLM
    (voir core/outils_signalements.py)."""
    ligne = _signalement_du_prof(signalement_id, prof_id)
    if not ligne:
        return None
    try:
        res = (
            supabase.table("signalements")
            .update({"conversation_discussion_id": conversation_discussion_id, "statut": "discute"})
            .eq("id", signalement_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (ouverture discussion {signalement_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def enregistrer_note(signalement_id: str, prof_id: str, note: str) -> dict | None:
    """Met à jour la note de synthèse (correction_texte) depuis la
    discussion prof/LLM. Appelable autant de fois que nécessaire, à
    aucun moment obligatoire, jamais transformée en comportement."""
    ligne = _signalement_du_prof(signalement_id, prof_id)
    if not ligne:
        return None
    try:
        res = (
            supabase.table("signalements")
            .update({"correction_texte": (note or "").strip() or None, "statut": "discute"})
            .eq("id", signalement_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement note {signalement_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def rattacher_signalement(signalement_id: str, prof_id: str, code_id: str | None, notion_id: str | None) -> dict | None:
    """Rattache le signalement à une matière (code_id) et/ou une notion
    précise, les deux combinables (portée large ou fine, au cas par
    cas). Ni l'un ni l'autre requis."""
    ligne = _signalement_du_prof(signalement_id, prof_id)
    if not ligne:
        return None
    try:
        res = (
            supabase.table("signalements")
            .update({"code_id": code_id, "notion_id": notion_id})
            .eq("id", signalement_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (rattachement signalement {signalement_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def dupliquer_signalement(signalement_id: str, prof_id: str) -> dict | None:
    ligne = _signalement_du_prof(signalement_id, prof_id)
    if not ligne:
        return None
    nouvelle_ligne = {
        "agent_id": ligne["agent_id"],
        "etudiant_id": ligne["etudiant_id"],
        "prof_id": prof_id,
        "conversation_id": ligne.get("conversation_id"),
        "question_message_id": ligne.get("question_message_id"),
        "reponse_message_id": ligne.get("reponse_message_id"),
        "question_texte": ligne.get("question_texte") or "",
        "reponse_texte": ligne.get("reponse_texte") or "",
        "contexte_conversation": ligne.get("contexte_conversation") or [],
        "probleme_observe": ligne.get("probleme_observe"),
        "visible_question": ligne.get("visible_question", True),
        "visible_reponse": ligne.get("visible_reponse", True),
        "visible_conversation": ligne.get("visible_conversation", False),
        "code_id": ligne.get("code_id"),
        "notion_id": ligne.get("notion_id"),
        "correction_texte": ligne.get("correction_texte"),
        "statut": "nouveau",
    }
    try:
        res = supabase.table("signalements").insert(nouvelle_ligne).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (duplication signalement {signalement_id}) : {e}")
        return None
    if not res.data:
        return None
    return _ligne_publique(res.data[0])


def supprimer_signalement(signalement_id: str, prof_id: str) -> bool:
    try:
        res = supabase.table("signalements").delete().eq("id", signalement_id).eq("prof_id", prof_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression signalement {signalement_id}) : {e}")
        return False
    return bool(res.data)


def consulter_par_notion(agent_id: str, etudiant_id: str, code_id: str | None, notion_id: str | None) -> list[dict]:
    """Signalements pertinents pour la matière/notion active d'une
    conversation, pour injection auto ou consultation à la demande par
    le LLM (voir core/outils_signalements.py). Uniquement ceux avec une
    note (correction_texte) -- un signalement sans note n'a rien
    d'utile à apporter à la conversation."""
    requete = (
        supabase.table("signalements")
        .select("id, question_texte, reponse_texte, probleme_observe, correction_texte, code_id, notion_id")
        .eq("etudiant_id", etudiant_id)
        .eq("agent_id", agent_id)
        .not_.is_("correction_texte", "null")
    )
    if notion_id:
        requete = requete.eq("notion_id", notion_id)
    elif code_id:
        requete = requete.eq("code_id", code_id)
    else:
        return []
    try:
        res = requete.order("updated_at", desc=True).limit(20).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (consultation signalements notion={notion_id} code={code_id}) : {e}")
        return []
    return res.data or []
