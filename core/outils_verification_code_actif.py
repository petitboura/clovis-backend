"""
Outil MCP unique de vérification "mode cours" (12/09/2026, demande
explicite Bourama).

Contexte : trois systèmes différents injectent déjà automatiquement du
contenu lié au code actif d'un élève dans le prompt système à chaque
message (comportements/skills reçus, consignes/règles du Programme par
notion, notes du prof sur les signalements). Bourama juge que trop de
choses dépendent de cette seule injection (retards de cache possibles,
notion non trouvée par la recherche sémantique, etc.) -- doctrine
"double sécurité" : l'injection automatique reste en place PARTOUT, cet
outil est un filet de sécurité que le modèle peut appeler lui-même s'il
pense que ce qui a été injecté est vide, incomplet ou périmé.

UN SEUL outil avec plusieurs actions (demande explicite Bourama : "un
outil et des actions", pas plusieurs outils séparés) :
- absorbe consulter_avancement_notion (outils_avancement_notions.py,
  09/09/2026) -> action "programme"
- absorbe consulter_signalements_pertinents (outils_signalements.py,
  10/09/2026) -> action "signalements"
- ajoute l'action neuve "comportements", qui n'avait encore aucun filet
  de sécurité (c'est le point de départ de toute cette discussion : un
  bug de cache faisait que la modification du TEXTE d'un comportement
  déjà attaché à un code mettait jusqu'à 2 minutes à apparaître côté
  élève -- bug corrigé séparément dans core/comportements_etudiants.py,
  mais ce filet de sécurité reste utile en plus).

Les deux outils absorbés ne sont PAS supprimés de leur fichier d'origine
dans ce premier temps (pour ne rien casser ailleurs s'ils sont
référencés ou testés séparément) -- seulement retirés de l'enregistrement
MCP (voir core/serveur_mcp_generation.py) pour que le modèle ne voie plus
qu'un seul outil.

Convention d'import : "core.X" partout, comme core/outils_signalements.py
et core/outils_avancement_notions.py (déjà en prod, chargés par
core/serveur_mcp_generation.py) -- pas les imports "plats" utilisés par
core/main.py lui-même. Le paramètre `ignorer_cache` de
lister_comportements_recus saute désormais AUSSI l'écriture du cache
(pas seulement la lecture), donc aucune dépendance à la cohérence entre
les deux instances de core/codes_partage.py qui coexistent dans le
process (une chargée bare par core/main.py, une chargée "core.codes_partage"
par ce fichier et par core/outils_avancement_notions.py) -- vérifié
avant d'écrire, jamais supposé.
"""

import logging

from core.avancement_notions_ia import resoudre_code_actif_eleve as _resoudre_code_actif_eleve
from core.avancement_notions_ia import consulter_progres_notion_pour_eleve as _consulter_progres_notion_pour_eleve
from core.avancement_notions_ia import toutes_notions_code as _toutes_notions_code
from core.codes_partage import lister_comportements_recus as _lister_comportements_recus
from core.mode_actif_conversation import rattachement_actif_pour_prompt as _rattachement_actif_pour_prompt
from core.signalements import consulter_par_notion as _consulter_par_notion
from core.outils_generation_commun import mcp_generation, Context


@mcp_generation.tool()
def verifier_consignes_code_actif(action: str, ctx: Context, nom_notion: str = "") -> str:
    """
    Outil OBLIGATOIRE dès que le mode cours est actif (12/09/2026,
    demande explicite Bourama -- ce n'est plus une option laissée à ton
    jugement). Trois choses liées au code actif de cet élève sont
    injectées automatiquement dans ton prompt système à chaque message
    (les comportements/skills reçus, les notions pertinentes du
    Programme avec leur consigne/règle, et les notes du prof sur les
    signalements), mais tant que le mode cours est actif, tu dois quand
    même appeler cet outil pour les trois actions ci-dessous avant de
    répondre, systématiquement, même si les blocs injectés te semblent
    déjà complets.

    `action` doit être l'une de :
    - "comportements" : relit à l'instant, sans dépendre du cache,
      la liste des comportements/skills reçus via le code actif de cet
      élève (id, nom, description courte -- le texte complet de l'un
      d'eux se lit ensuite avec l'outil consulter_comportement, comme
      d'habitude). Aucun paramètre supplémentaire.
    - "programme" : statut, règle et consigne du prof. Si `nom_notion`
      est fourni (description la plus naturelle possible de la notion
      concernée, pas besoin d'un nom exact -- recherche sémantique,
      tolère reformulation/typo), renvoie le détail de cette notion
      précise. Si `nom_notion` est vide (cas de l'appel obligatoire
      générique, sans notion précise en tête), renvoie la liste de
      toutes les notions "à venir" du code actif qui ont une règle ou
      une consigne configurée par le prof.
    - "signalements" : notes du prof sur des signalements passés,
      pertinentes pour le code actif de cet élève (toutes notions
      confondues). Aucun paramètre supplémentaire.
    """
    etudiant_id = ctx.request_context.request.query_params.get("user_id")
    if not etudiant_id:
        return "Erreur : impossible d'identifier l'élève."
    conversation_id = ctx.request_context.request.query_params.get("conversation_id")
    rattachement_id_actif = _rattachement_actif_pour_prompt(conversation_id, etudiant_id)

    if action == "comportements":
        try:
            recus = _lister_comportements_recus(etudiant_id, rattachement_id_actif, ignorer_cache=True)
        except Exception as e:
            logging.error(f"ERREUR verifier_consignes_code_actif (comportements) : {e}")
            return "Erreur : impossible de relire les comportements reçus, réessaie."
        if not recus:
            return "Aucun comportement/skill reçu via le code actif de cet élève en ce moment."
        lignes = [f"- [id: {c['id']}] {c['nom']} -- {c['description']}" for c in recus]
        return "Comportements reçus, à l'instant présent :\n" + "\n".join(lignes)

    if action == "programme":
        nom_notion = (nom_notion or "").strip()
        if not nom_notion:
            code = _resoudre_code_actif_eleve(etudiant_id, rattachement_id_actif)
            if code is None:
                return "Cet élève n'est rattaché à aucun code avec une structure de notions."
            if isinstance(code, list):
                return "Cet élève est rattaché à plusieurs codes, impossible de savoir lequel concerne cette question. Réponds avec ton jugement habituel."
            try:
                notions = _toutes_notions_code(code["id"])
            except Exception as e:
                logging.error(f"ERREUR verifier_consignes_code_actif (programme, liste générale) : {e}")
                return "Aucune donnée de programme disponible, réponds avec ton jugement habituel."
            pertinentes = [n for n in notions if n.get("statut") == "a_venir" and (n.get("regle_comportement") or n.get("consigne_llm"))]
            if not pertinentes:
                return "Aucune notion \"à venir\" avec une règle ou une consigne configurée par le prof en ce moment."
            lignes = []
            for n in pertinentes:
                regle = f" règle=\"{n['regle_comportement']}\"" if n.get("regle_comportement") else ""
                consigne = f" consigne=\"{n['consigne_llm']}\"" if n.get("consigne_llm") else ""
                lignes.append(f"- \"{n['nom']}\" (à venir) --{regle}{consigne}")
            return "Notions à venir avec règle/consigne du prof :\n" + "\n".join(lignes)
        try:
            resultat = _consulter_progres_notion_pour_eleve(etudiant_id, nom_notion, rattachement_id_actif)
        except Exception as e:
            logging.error(f"ERREUR verifier_consignes_code_actif (programme) : {e}")
            return "Aucune donnée de programme disponible, réponds avec ton jugement habituel."
        erreur = resultat.get("erreur")
        if erreur == "aucun_code":
            return "Cet élève n'est rattaché à aucun code avec une structure de notions."
        if erreur == "code_ambigu":
            return (
                "Cet élève est rattaché à plusieurs codes, impossible de savoir lequel "
                "concerne cette question. Réponds avec ton jugement habituel."
            )
        if erreur == "notion_introuvable":
            return f"Aucune notion suffisamment proche de \"{nom_notion}\" trouvée dans le programme de cet élève."
        nom_trouve = resultat.get("nom_trouve", nom_notion)
        statut = resultat["statut"]
        regle = resultat.get("regle")
        consigne = resultat.get("consigne")
        suffixe_consigne = f" Consigne à respecter pour cette notion : \"{consigne}\"." if consigne else ""
        if statut != "a_venir":
            return f"Notion \"{nom_trouve}\" : statut \"{statut}\" (déjà vue en classe), réponds normalement.{suffixe_consigne}"
        if regle:
            return f"Notion \"{nom_trouve}\" : pas encore vue en classe (statut \"a_venir\"), règle configurée : \"{regle}\".{suffixe_consigne}"
        return f"Notion \"{nom_trouve}\" : pas encore vue en classe (statut \"a_venir\"), aucune règle configurée, aide l'élève en le signalant.{suffixe_consigne}"

    if action == "signalements":
        agent_id = ctx.request_context.request.query_params.get("agent_id")
        if not agent_id:
            return "Erreur : impossible d'identifier l'agent."
        code = _resoudre_code_actif_eleve(etudiant_id, rattachement_id_actif)
        if code is None:
            return "Cet élève n'est rattaché à aucune matière -- aucune note à consulter."
        if isinstance(code, list):
            return "Cet élève est rattaché à plusieurs matières sans mode actif choisi, impossible de savoir laquelle consulter."
        try:
            lignes = _consulter_par_notion(agent_id, etudiant_id, code["id"], None)
        except Exception as e:
            logging.error(f"ERREUR verifier_consignes_code_actif (signalements) : {e}")
            return "Erreur : impossible de relire les signalements, réessaie."
        if not lignes:
            return "Aucune note du prof disponible pour cette matière."
        parties = []
        for l in lignes:
            probleme = f" (problème observé : \"{l['probleme_observe']}\")" if l.get("probleme_observe") else ""
            parties.append(f"- {l['correction_texte']}{probleme}")
        return "\n".join(parties)

    return "Erreur : action inconnue, utilise \"comportements\", \"programme\" ou \"signalements\"."
