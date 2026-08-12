"""
Journal d'audit des actions structurelles/sensibles (creation, modification,
suppression d'agents, de posts, de comptes...) pour pouvoir repondre a
"qui a fait quoi, quand" en cas de litige ou de bug signale.

Ecrit dans la table Supabase `journal_actions` (RLS activee sans policy :
seul le service role, utilise ici via `supabase`, peut y ecrire/lire).

Volontairement HORS SCOPE de ce journal (voir discussion du 2026-07-21) :
- les messages de chat (deja dans historique_conversations) ;
- les commentaires/likes de posts (deja dans leurs propres tables) ;
- les notifications (consequence d'une action deja tracee ailleurs) ;
- les 👍/👎 sur les reponses (feedback_messages, pas un audit trail).

Usage :

    from api.journal import journaliser

    journaliser(
        user_id=utilisateur.id,
        action="agent.supprime",
        cible_type="agent",
        cible_id=agent_id,
        details={"nom": agent["nom"]},
        request=request,
    )

`journaliser` n'echoue jamais bruyamment : une erreur d'ecriture du
journal ne doit jamais faire echouer l'action metier elle-meme (on log
l'erreur et on continue), exactement comme le reste du logging du
projet (voir core/main.py `_sauvegarder_echange`).
"""

import logging

from api.auth import supabase


def _ip_client(request):
    """
    Extrait l'IP du client, en tenant compte du fait que Railway est
    derriere un proxy (X-Forwarded-For contient l'IP reelle en premiere
    position ; request.client.host donnerait sinon l'IP du proxy).
    """
    if request is None:
        return None
    entete = request.headers.get("x-forwarded-for")
    if entete:
        return entete.split(",")[0].strip()
    return request.client.host if request.client else None


def journaliser(action, user_id=None, cible_type=None, cible_id=None, details=None, request=None):
    """
    Insere une ligne dans journal_actions. Ne leve jamais d'exception :
    un souci de journalisation ne doit pas casser l'action metier en
    cours (creation d'agent, suppression de post, etc.).
    """
    try:
        supabase.table("journal_actions").insert({
            "user_id": user_id,
            "action": action,
            "cible_type": cible_type,
            "cible_id": cible_id,
            "details": details,
            "ip": _ip_client(request),
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR journal_actions (action={action}, user_id={user_id}) : {e}")
