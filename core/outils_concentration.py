"""
Outils MCP liés à la section Concentration côté téléphone de l'étudiant
(Contrôle de session DND/volume, Temps d'écran) -- créé le 09/09/2026
(Bourama, connexion de cette section à l'IA), fichier séparé de
core/outils_mobile.py (dédié aux dossiers désignés) pour ne pas le
faire grossir plus, même logique de découpage par responsabilité déjà
en place sur ce dépôt.

Contrôle de session : Android UNIQUEMENT pour l'instant (décision
Bourama, 09/09/2026) -- le plugin iOS (ControleSessionPlugin.swift)
n'a pas les mêmes méthodes (bug connu, pas encore corrigé, voir
composants clovis-frontend/EspaceControleSession.tsx). Passe par le
même système d'actions à distance que gerer_dossier_telephone (fire-
and-forget, core/actions_appareil_mobile.py), mais dans un outil séparé
car sans lien avec les dossiers désignés (pas de "dossier_nom" à
résoudre, pas de ciblage multi-appareil pour l'instant -- diffusion
large comme le comportement par défaut de creer_action).

Depuis le 09/09/2026, une session est bornée dans le temps (l'étudiant
ou l'IA donne une durée) : l'auto-arrêt est géré par une alarme système
côté téléphone (ControleSessionPlugin.kt/ControleSessionAlarmReceiver.kt),
pas par ce backend -- ce fichier ne fait que déclencher le démarrage/
arrêt, il ne surveille rien lui-même.
"""

import logging

from core.actions_appareil_mobile import creer_action as _creer_action_mobile
from core.actions_appareil_mobile import attendre_resultat_action as _attendre_resultat_action_mobile
from core.usage_appareil_mobile import lire_usage as _lire_usage

from core.outils_generation_commun import mcp_generation, Context


DUREE_MIN_SESSION_MINUTES = 1
DUREE_MAX_SESSION_MINUTES = 480  # 8h, garde-fou large plutôt qu'une vraie limite produit tranchée avec Bourama


@mcp_generation.tool()
def gerer_session_concentration(action: str, ctx: Context, duree_minutes: int = 0) -> str:
    """
    Démarre ou arrête une session de concentration sur le téléphone
    Android de l'étudiant (coupe la sonnerie/les notifications, active
    Ne pas déranger). ANDROID UNIQUEMENT pour l'instant : sur iPhone,
    renvoie une erreur explicite plutôt que de tenter l'action (bug
    connu côté iOS, pas encore corrigé).

    `action` doit être l'une de :
    - "demarrer" : démarre une session pour `duree_minutes` (entier,
      obligatoire, entre 1 et 480). La session s'arrête automatiquement
      toute seule à la fin de cette durée, même si l'étudiant ferme
      l'app entre-temps -- ne propose donc JAMAIS de "vérifier" ou
      "relancer" la session avant la fin, elle se termine d'elle-même.
      Si l'étudiant n'a pas donné de durée précise ("un moment",
      "un peu"), demande-lui combien de temps avant d'appeler cet
      outil, ne devine jamais une durée.
    - "arreter" : arrête immédiatement la session en cours et restaure
      le téléphone à son état d'avant. `duree_minutes` est ignoré.

    Comme gerer_dossier_telephone, cet appel ATTEND jusqu'à ~10s la
    confirmation réelle du téléphone avant de répondre. Si la
    confirmation n'arrive pas à temps (app fermée/hors ligne),
    informe l'étudiant que l'action est en attente, ne dis PAS qu'elle
    a réussi.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    if action == "demarrer":
        if not isinstance(duree_minutes, int) or not (DUREE_MIN_SESSION_MINUTES <= duree_minutes <= DUREE_MAX_SESSION_MINUTES):
            return f"Erreur : duree_minutes doit être un entier entre {DUREE_MIN_SESSION_MINUTES} et {DUREE_MAX_SESSION_MINUTES}."
        type_action = "controle_session_demarrer"
        parametres = {"duree_minutes": duree_minutes}
    elif action == "arreter":
        type_action = "controle_session_arreter"
        parametres = {}
    else:
        return "Erreur : action inconnue. Actions valides : demarrer, arreter."

    try:
        action_id = _creer_action_mobile(user_id, type_action, parametres)
    except Exception as e:
        logging.error(f"ERREUR gerer_session_concentration ({type_action}) : {e}")
        return "Erreur : impossible de programmer cette action, réessaie."

    action_terminee = _attendre_resultat_action_mobile(action_id, user_id)
    if action_terminee is None:
        return (
            f"Action \"{type_action}\" envoyée au téléphone de l'étudiant, mais "
            "pas encore confirmée (app peut-être fermée ou en arrière-plan) : "
            "informe l'étudiant que l'action est en attente, ne dis PAS qu'elle "
            "a réussi."
        )
    if action_terminee.get("statut") == "echouee":
        resultat = action_terminee.get("resultat") or "raison inconnue"
        if "Permission" in resultat:
            return (
                f"Échec : {resultat} L'étudiant doit d'abord accorder la "
                "permission dans l'écran Concentration > Contrôle de session."
            )
        return f"Échec de l'action \"{type_action}\" sur le téléphone : {resultat}."
    return action_terminee.get("resultat") or f"Action \"{type_action}\" exécutée avec succès sur le téléphone."


@mcp_generation.tool()
def lire_temps_ecran(ctx: Context, jours: int = 7) -> str:
    """
    Lit le temps d'écran par app de l'étudiant sur les `jours` derniers
    jours (7 par défaut), déjà synchronisé depuis son téléphone Android
    (Temps d'écran n'existe pas encore sur iPhone, voir Contrôle de
    session pour la même limite côté iOS). Utilise ceci pour analyser
    ou commenter les habitudes d'usage de l'étudiant, PAS pour agir
    dessus (aucune action possible via cet outil, lecture seule).

    Si l'étudiant n'a jamais ouvert l'écran Temps d'écran sur son
    téléphone (ou n'a pas encore accordé la permission), aucune donnée
    n'existe encore : le dis clairement plutôt que de supposer un
    usage nul.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : impossible d'identifier l'utilisateur."

    from datetime import datetime, timedelta, timezone

    jusqua = datetime.now(timezone.utc).date().isoformat()
    depuis = (datetime.now(timezone.utc).date() - timedelta(days=jours)).isoformat()

    try:
        lignes = _lire_usage(user_id, depuis, jusqua)
    except Exception as e:
        logging.error(f"ERREUR lire_temps_ecran : {e}")
        return "Erreur : impossible de lire le temps d'écran, réessaie."

    if not lignes:
        return (
            "Aucune donnée de temps d'écran pour cette période : soit l'étudiant "
            "n'a pas encore ouvert l'écran Temps d'écran sur son téléphone Android, "
            "soit il n'a pas accordé la permission d'accès à l'usage des apps."
        )

    par_app: dict[str, int] = {}
    for ligne in lignes:
        par_app[ligne["nom_app"]] = par_app.get(ligne["nom_app"], 0) + ligne["duree_secondes"]

    total_secondes = sum(par_app.values())
    lignes_formatees = []
    for nom_app, secondes in sorted(par_app.items(), key=lambda x: x[1], reverse=True):
        h, m = divmod(secondes // 60, 60)
        duree = f"{h} h {m:02d}" if h else f"{m} min"
        lignes_formatees.append(f"- {nom_app} : {duree}")

    h_total, m_total = divmod(total_secondes // 60, 60)
    total_fmt = f"{h_total} h {m_total:02d}" if h_total else f"{m_total} min"

    return (
        f"Temps d'écran des {jours} derniers jours (total {total_fmt}), par app :\n"
        + "\n".join(lignes_formatees)
    )
