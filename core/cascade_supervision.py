"""
Cascade de supervision (Point 6, Partie 10 - dernière partie du chantier
"confiance pédagogique", 07/09/2026). Ne concerne QUE les signalements
de type B (comportement général mal configuré, voir
core/corrections_pedagogiques.py) - le type A reste géré par son
propre flux de correction (Partie 4/5), jamais touché ici.

Construite en dernier comme prévu par le plan de travail : les Parties
1 à 9 fonctionnent déjà en autonomie complète sans qu'aucune ligne de
ce fichier n'existe, ce module ne fait qu'AJOUTER un comportement
optionnel au-dessus (déclenché depuis core/corrections_pedagogiques.py
au moment de la création d'un signalement B, best-effort, ne bloque
jamais l'enregistrement du signalement lui-même).

Vue d'ensemble du modèle retenu (voir migrations/2026_09_07_cascade_supervision.sql
pour le détail commenté du schéma, notamment la note sur pourquoi
'neutralise' n'est PAS utilisé comme étape de la progression
temporelle) :

1. DÉTECTION - verifier_accumulation(prof_id, agent_id) : compte les
   signalements B "libres" (cascade_id encore null) de ce (prof, agent)
   sur une fenêtre glissante. Sous le seuil : rien ne se passe, chaque
   signalement reste isolé (statut_cascade='en_attente', valeur déjà
   existante depuis la Partie 4). Au seuil : declencher_cascade() crée
   la cascade, y rattache tous les signalements du groupe, et déclenche
   IMMÉDIATEMENT la neutralisation automatique (indépendante du
   calendrier qui suit).

2. NEUTRALISATION - tentative_neutralisation() : cherche, PARMI les
   comportements du prof réellement attachés à un de ses codes actifs
   pour cet agent (donc réellement "vivants" pour ses élèves, voir
   core/codes_partage.py), lequel correspond le mieux au contenu des
   signalements accumulés (LLM, même registre que
   core/generalisation_correction_pedagogique.py). Désactive UNIQUEMENT
   ce comportement précis (jamais tout le compte du prof) via la
   fonction déjà existante core.comportements_etudiants.activer_desactiver_comportement.
   Best-effort strict : si le LLM ne trouve pas de candidat clair, la
   cascade suit son cours sans neutralisation plutôt que de désactiver
   au hasard.

3. PROGRESSION - verifier_progression_cascades(), appelée
   périodiquement par le planificateur (voir api/main.py, même principe
   que core/audit_hebdomadaire_corrections.py) : fait avancer chaque
   cascade non résolue de J2 (prof notifié) vers J5 (établissement
   notifié, UNIQUEMENT si un rattachement accepté commun existe --
   sinon on saute directement à l'étape suivante sans notifier
   d'établissement, mais sans jamais raccourcir le délai J5, règle
   explicite de la vision) vers équipe Clovis (rôle profiles.role='admin',
   même mécanisme que api/signalements.py::_est_admin).

4. RÉSOLUTION - résoudre_cascade() : action manuelle (prof,
   établissement à l'étape J5, ou équipe Clovis), jamais automatique --
   la cascade ne "détecte" pas elle-même qu'un problème est réglé (rien
   dans le schéma actuel ne permet de le déduire pour un type B, qui n'a
   pas de flux de correction comme le type A), c'est un humain qui
   confirme. Réactiver le comportement neutralisé, si souhaité, se fait
   avec l'outil déjà existant (activer_desactiver_comportement), pas
   reconstruit ici.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from groq import Groq
from supabase import create_client

from core.comportements_etudiants import (
    get_secret,
    activer_desactiver_comportement as _activer_desactiver_comportement,
)
from core.notifications import creer_notification as _creer_notification

logging.basicConfig(level=logging.INFO)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

# Choix par défaut, faute de décision explicite de Bourama sur ces deux
# nombres (question posée en même temps que la livraison de cette
# partie) : 3 signalements B en 14 jours sur le même (prof, agent)
# déclenchent une cascade. Ajustable sans migration, ce sont de simples
# constantes Python.
SEUIL_DECLENCHEMENT = 3
FENETRE_DETECTION = timedelta(days=14)

MODELE_NEUTRALISATION = "openai/gpt-oss-120b"


# ---------------------------------------------------------------------------
# 1. Détection
# ---------------------------------------------------------------------------

def verifier_accumulation(prof_id: str, agent_id: str) -> dict | None:
    """Appelée juste après la création d'un signalement B (voir
    core/corrections_pedagogiques.py::creer_correction). Compte les
    signalements B pas encore rattachés à une cascade
    (cascade_id is null) pour ce (prof, agent), créés dans la fenêtre de
    détection. Si le seuil est atteint, déclenche la cascade et renvoie
    la ligne créée ; sinon renvoie None sans rien modifier (le
    signalement reste isolé, à sa valeur par défaut 'en_attente')."""
    if not prof_id:
        return None

    depuis = (datetime.now(timezone.utc) - FENETRE_DETECTION).isoformat()
    try:
        res = (
            supabase.table("corrections_pedagogiques")
            .select("id, etudiant_id, question_texte, reponse_texte, created_at")
            .eq("prof_id", prof_id)
            .eq("agent_id", agent_id)
            .eq("type", "B")
            .is_("cascade_id", "null")
            .gte("created_at", depuis)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification accumulation cascade {prof_id}/{agent_id}) : {e}")
        return None

    lignes = res.data or []
    if len(lignes) < SEUIL_DECLENCHEMENT:
        return None

    return declencher_cascade(prof_id, agent_id, lignes)


def declencher_cascade(prof_id: str, agent_id: str, lignes: list[dict]) -> dict | None:
    """Crée la cascade, y rattache tous les signalements accumulés
    (cascade_id + statut_cascade='j2_prof' sur chaque ligne), notifie le
    prof, et tente la neutralisation immédiate. Jour 0 de la cascade."""
    try:
        res = supabase.table("cascades_supervision").insert({
            "prof_id": prof_id,
            "agent_id": agent_id,
            "compteur_signalements": len(lignes),
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création cascade {prof_id}/{agent_id}) : {e}")
        return None
    if not res.data:
        return None
    cascade = res.data[0]

    ids = [l["id"] for l in lignes]
    try:
        supabase.table("corrections_pedagogiques").update({
            "cascade_id": cascade["id"],
            "statut_cascade": "j2_prof",
        }).in_("id", ids).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (rattachement signalements à la cascade {cascade['id']}) : {e}")

    try:
        _creer_notification(
            prof_id,
            "cascade_supervision_j2",
            "Plusieurs élèves signalent le même problème",
            f"{len(lignes)} signalements indiquent un comportement mal configuré. "
            "Tu as 2 jours pour agir avant que l'établissement (si tu en as un) soit notifié.",
            lien="/bureau/cascade",
        )
    except Exception as e:
        logging.error(f"ERREUR notification déclenchement cascade {cascade['id']} : {e}")

    try:
        tentative_neutralisation(cascade, lignes)
    except Exception as e:
        logging.error(f"ERREUR neutralisation automatique cascade {cascade['id']} : {e}")

    return cascade


# ---------------------------------------------------------------------------
# 2. Neutralisation automatique et immédiate
# ---------------------------------------------------------------------------

def _comportements_vivants_du_prof(prof_id: str, agent_id: str) -> list[dict]:
    """Comportements du prof réellement attachés à au moins un de ses
    codes actifs pour cet agent (donc effectivement appliqués à ses
    élèves aujourd'hui) - pas simplement tous ses comportements. Requête
    directe sur les tables (pas d'ajout à core/codes_partage.py, déjà à
    plus de 860 lignes, règle transversale du plan de travail)."""
    try:
        codes = (
            supabase.table("codes_partage")
            .select("id")
            .eq("proprietaire_id", prof_id)
            .eq("actif", True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (codes actifs du prof {prof_id}) : {e}")
        return []
    code_ids = [c["id"] for c in (codes.data or [])]
    if not code_ids:
        return []

    try:
        liaisons = (
            supabase.table("codes_partage_comportements")
            .select("comportement_id")
            .in_("code_id", code_ids)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comportements liés aux codes {code_ids}) : {e}")
        return []
    comportement_ids = list({l["comportement_id"] for l in (liaisons.data or [])})
    if not comportement_ids:
        return []

    try:
        comportements = (
            supabase.table("comportements_etudiants")
            .select("id, nom, texte")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", prof_id)
            .eq("actif", True)
            .in_("id", comportement_ids)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportements candidats {comportement_ids}) : {e}")
        return []
    return comportements.data or []


def _identifier_regle_contestee(candidats: list[dict], lignes: list[dict]) -> str | None:
    """LLM : parmi les comportements candidats (id/nom/texte), lequel
    correspond le mieux aux signalements accumulés ? Renvoie l'id
    choisi, ou None si aucun candidat ne ressort clairement - fail-safe
    strict, jamais d'id inventé (toute sortie qui ne correspond pas
    exactement à un id candidat est traitée comme "aucun")."""
    if not candidats:
        return None

    liste_candidats = "\n".join(f"- id={c['id']} | {c.get('nom') or '(sans nom)'} : {c.get('texte') or ''}" for c in candidats)
    liste_signalements = "\n".join(
        f"- Question : {l.get('question_texte') or ''}\n  Réponse jugée mal configurée : {l.get('reponse_texte') or ''}"
        for l in lignes
    )

    prompt = (
        "Plusieurs élèves ont signalé que le comportement général d'un "
        "assistant IA est mal configuré (ton, pédagogie, règle mal pensée). "
        "Voici la liste des comportements actuellement configurés par le "
        "professeur pour sa classe, et les signalements reçus. Identifie "
        "UNIQUEMENT si l'un de ces comportements est clairement la cause "
        "probable des signalements. Réponds avec UNIQUEMENT l'id du "
        "comportement concerné, rien d'autre. Si aucun ne ressort "
        "clairement comme responsable, réponds exactement AUCUN.\n\n"
        f"Comportements configurés :\n{liste_candidats}\n\n"
        f"Signalements reçus :\n{liste_signalements}"
    )

    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=20.0)
        completion = client.chat.completions.create(
            model=MODELE_NEUTRALISATION,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=50,
            timeout=20.0,
        )
        reponse = (completion.choices[0].message.content or "").strip()
    except Exception as e:
        logging.error(f"ERREUR LLM identification règle contestée : {e}")
        return None

    ids_valides = {c["id"] for c in candidats}
    return reponse if reponse in ids_valides else None


def tentative_neutralisation(cascade: dict, lignes: list[dict]) -> None:
    """Neutralise (désactive) le comportement identifié comme la cause
    probable, si un candidat clair est trouvé. Best-effort strict :
    aucune exception ne doit jamais empêcher la cascade elle-même de
    continuer (déclenchée depuis declencher_cascade dans un try/except)."""
    candidats = _comportements_vivants_du_prof(cascade["prof_id"], cascade["agent_id"])
    comportement_id = _identifier_regle_contestee(candidats, lignes)
    if not comportement_id:
        logging.info(f"Cascade {cascade['id']} : aucune règle candidate identifiée, pas de neutralisation automatique.")
        return

    resultat = _activer_desactiver_comportement(cascade["agent_id"], cascade["prof_id"], comportement_id, False)
    if resultat is None:
        logging.error(f"ERREUR neutralisation cascade {cascade['id']} : échec désactivation comportement {comportement_id}")
        return

    try:
        supabase.table("cascades_supervision").update({
            "comportement_neutralise_id": comportement_id,
            "neutralisee_le": datetime.now(timezone.utc).isoformat(),
        }).eq("id", cascade["id"]).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (enregistrement neutralisation cascade {cascade['id']}) : {e}")


# ---------------------------------------------------------------------------
# 3. Progression temporelle (J2 -> J5 -> équipe Clovis)
# ---------------------------------------------------------------------------

def _etablissement_commun_accepte(prof_id: str, etudiant_ids: list[str]) -> str | None:
    """Le "bloc qui permet la cascade établissement" du Point 6 : un
    établissement où le prof ET au moins un des élèves signalants sont
    tous les deux en rattachement 'accepte'. Renvoie l'id de ce premier
    établissement trouvé, ou None (cours privé, l'étape établissement
    est alors sautée)."""
    if not etudiant_ids:
        return None
    try:
        rattachements_prof = (
            supabase.table("etablissements_rattachements")
            .select("etablissement_id")
            .eq("utilisateur_id", prof_id)
            .eq("etat", "accepte")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (rattachements établissement du prof {prof_id}) : {e}")
        return None
    etablissements_prof = {r["etablissement_id"] for r in (rattachements_prof.data or [])}
    if not etablissements_prof:
        return None

    try:
        rattachements_eleves = (
            supabase.table("etablissements_rattachements")
            .select("etablissement_id")
            .in_("utilisateur_id", etudiant_ids)
            .eq("etat", "accepte")
            .in_("etablissement_id", list(etablissements_prof))
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (rattachements établissement des élèves {etudiant_ids}) : {e}")
        return None
    if rattachements_eleves.data:
        return rattachements_eleves.data[0]["etablissement_id"]
    return None


def _admins_equipe_clovis() -> list[str]:
    """Même définition que api/signalements.py::_est_admin
    (profiles.role='admin'), réutilisée pour notifier l'équipe Clovis à
    l'étape J5+."""
    try:
        res = supabase.table("profiles").select("user_id").eq("role", "admin").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste admins équipe Clovis) : {e}")
        return []
    return [p["user_id"] for p in (res.data or [])]


def _faire_progresser_une_cascade(cascade: dict) -> None:
    maintenant = datetime.now(timezone.utc)
    j2_le = datetime.fromisoformat(cascade["j2_le"].replace("Z", "+00:00"))
    j5_le = datetime.fromisoformat(cascade["j5_le"].replace("Z", "+00:00"))

    if cascade["statut"] == "j2_prof" and maintenant >= j2_le:
        try:
            lignes = (
                supabase.table("corrections_pedagogiques")
                .select("etudiant_id")
                .eq("cascade_id", cascade["id"])
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (élèves de la cascade {cascade['id']}) : {e}")
            lignes = None
        etudiant_ids = list({l["etudiant_id"] for l in (lignes.data or [])}) if lignes else []
        etablissement_id = _etablissement_commun_accepte(cascade["prof_id"], etudiant_ids)

        if etablissement_id:
            _mettre_a_jour_cascade(cascade["id"], "j5_etablissement", etablissement_id=etablissement_id)
            try:
                etablissement = supabase.table("etablissements").select("profile_id, nom").eq("id", etablissement_id).maybe_single().execute()
                if etablissement and etablissement.data:
                    _creer_notification(
                        etablissement.data["profile_id"],
                        "cascade_supervision_j5",
                        "Signalements accumulés chez un enseignant rattaché",
                        "Un enseignant rattaché à votre établissement accumule des signalements de comportement IA mal configuré, sans réponse depuis 2 jours.",
                        lien=f"/bureau/etablissement/cascades",
                    )
            except Exception as e:
                logging.error(f"ERREUR notification établissement cascade {cascade['id']} : {e}")
        # Si aucun établissement commun : on reste en 'j2_prof', le délai
        # J5 ci-dessous fera passer directement à 'equipe_clovis' sans
        # étape établissement, sans jamais raccourcir le délai.

    if cascade["statut"] in ("j2_prof", "j5_etablissement") and maintenant >= j5_le:
        _mettre_a_jour_cascade(cascade["id"], "equipe_clovis")
        for admin_id in _admins_equipe_clovis():
            try:
                _creer_notification(
                    admin_id,
                    "cascade_supervision_equipe_clovis",
                    "Cascade de supervision arrivée à l'équipe Clovis",
                    f"Un enseignant (agent {cascade['agent_id']}) accumule des signalements sans résolution depuis 5 jours.",
                    lien="/bureau/cascade",
                )
            except Exception as e:
                logging.error(f"ERREUR notification équipe Clovis cascade {cascade['id']} : {e}")


def _mettre_a_jour_cascade(cascade_id: str, statut: str, etablissement_id: str | None = None) -> None:
    patch = {"statut": statut}
    if etablissement_id:
        patch["etablissement_id"] = etablissement_id
    try:
        supabase.table("cascades_supervision").update(patch).eq("id", cascade_id).execute()
        supabase.table("corrections_pedagogiques").update({"statut_cascade": statut}).eq("cascade_id", cascade_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (progression cascade {cascade_id} -> {statut}) : {e}")


def verifier_progression_cascades() -> int:
    """Appelée périodiquement par le planificateur (voir api/main.py).
    Fait avancer chaque cascade non résolue dont un délai est échu.
    Renvoie le nombre de cascades examinées (pas seulement celles ayant
    changé d'état, même principe de journalisation que
    core/audit_hebdomadaire_corrections.py::verifier_audits_hebdomadaires)."""
    try:
        res = supabase.table("cascades_supervision").select("*").neq("statut", "resolu").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste cascades non résolues) : {e}")
        return 0
    cascades = res.data or []
    for cascade in cascades:
        try:
            _faire_progresser_une_cascade(cascade)
        except Exception as e:
            logging.error(f"ERREUR progression cascade {cascade['id']} : {e}")
    return len(cascades)


# ---------------------------------------------------------------------------
# 4. Listage (avec accès direct aux signalements concernés)
# ---------------------------------------------------------------------------

def _avec_signalements(cascades: list[dict]) -> list[dict]:
    """Ajoute à chaque cascade la liste des signalements B qui la
    composent - "accès direct à la correction concernée" (plan de
    travail, Partie 10). Le type B n'a aucune autre vue prof dans
    l'app (voir la note en tête de ListeCorrectionsProf.tsx côté
    frontend, dédiée entièrement à cette cascade), donc cet indicateur
    est le seul endroit où ce contenu est réellement consultable."""
    if not cascades:
        return []
    cascade_ids = [c["id"] for c in cascades]
    try:
        res = (
            supabase.table("corrections_pedagogiques")
            .select("id, cascade_id, question_texte, reponse_texte, created_at")
            .in_("cascade_id", cascade_ids)
            .order("created_at")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (signalements des cascades {cascade_ids}) : {e}")
        res = None
    par_cascade: dict[str, list[dict]] = {}
    for ligne in (res.data if res else []) or []:
        par_cascade.setdefault(ligne["cascade_id"], []).append({
            "question_texte": ligne["question_texte"],
            "reponse_texte": ligne["reponse_texte"],
            "created_at": ligne["created_at"],
        })
    for c in cascades:
        c["signalements"] = par_cascade.get(c["id"], [])
    return cascades


def lister_mes_cascades(prof_id: str) -> list[dict]:
    """Cascades concernant ce prof (indicateur Bureau, Partie 10)."""
    try:
        res = (
            supabase.table("cascades_supervision")
            .select("*")
            .eq("prof_id", prof_id)
            .order("declenchee_le", desc=True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste cascades du prof {prof_id}) : {e}")
        return []
    return _avec_signalements(res.data or [])


def lister_cascades_etablissement(etablissement_id: str) -> list[dict]:
    try:
        res = (
            supabase.table("cascades_supervision")
            .select("*")
            .eq("etablissement_id", etablissement_id)
            .order("declenchee_le", desc=True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste cascades établissement {etablissement_id}) : {e}")
        return []
    return _avec_signalements(res.data or [])


def lister_cascades_equipe_clovis() -> list[dict]:
    try:
        res = (
            supabase.table("cascades_supervision")
            .select("*")
            .eq("statut", "equipe_clovis")
            .order("declenchee_le", desc=True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste cascades équipe Clovis) : {e}")
        return []
    return _avec_signalements(res.data or [])


# ---------------------------------------------------------------------------
# 5. Résolution manuelle
# ---------------------------------------------------------------------------

def resoudre_cascade(cascade_id: str, acteur_id: str, note: str | None = None) -> dict | None:
    """Réservé au prof concerné, à l'établissement rattaché (une fois
    l'étape J5 atteinte), ou à l'équipe Clovis (profiles.role='admin').
    Ne réactive PAS automatiquement le comportement neutralisé (choix
    délibéré : marquer résolu peut vouloir dire "j'ai corrigé
    autrement", pas forcément "réactive tel quel") - si le prof veut le
    réactiver, il le fait via l'interface "Mes comportements" déjà
    existante (activer_desactiver_comportement)."""
    try:
        res = supabase.table("cascades_supervision").select("*").eq("id", cascade_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture cascade {cascade_id}) : {e}")
        return None
    cascade = res.data if res and res.data else None
    if not cascade:
        return None

    autorise = acteur_id == cascade["prof_id"] or acteur_id in _admins_equipe_clovis()
    if not autorise and cascade.get("etablissement_id"):
        try:
            etablissement = supabase.table("etablissements").select("profile_id").eq("id", cascade["etablissement_id"]).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification établissement cascade {cascade_id}) : {e}")
            etablissement = None
        if etablissement and etablissement.data and etablissement.data["profile_id"] == acteur_id:
            autorise = True
    if not autorise:
        return None

    try:
        res_maj = (
            supabase.table("cascades_supervision")
            .update({
                "statut": "resolu",
                "resolue_le": datetime.now(timezone.utc).isoformat(),
                "resolue_par": acteur_id,
                "note_resolution": (note or "").strip() or None,
            })
            .eq("id", cascade_id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (résolution cascade {cascade_id}) : {e}")
        return None
    if not res_maj.data:
        return None

    try:
        supabase.table("corrections_pedagogiques").update({"statut_cascade": "resolu"}).eq("cascade_id", cascade_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (statut_cascade resolu sur signalements de {cascade_id}) : {e}")

    try:
        _creer_notification(
            cascade["prof_id"],
            "cascade_supervision_resolue",
            "Cascade de supervision résolue",
            (note or "").strip() or None,
        )
    except Exception as e:
        logging.error(f"ERREUR notification résolution cascade {cascade_id} : {e}")

    return res_maj.data[0]
