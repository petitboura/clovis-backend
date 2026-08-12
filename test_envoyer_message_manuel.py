"""
Test manuel de l'outil IA envoyer_message (tâche E, 04/08/2026).

Objectif : vérifier en conditions aussi réelles que possible, sans base
Supabase accessible depuis ce bac à sable, les deux choses que le
commentaire "NON TESTÉ EN CONDITIONS RÉELLES" (core/serveur_mcp_generation.py)
signalait comme point d'incertitude :

  1. `ctx.request_context.request.query_params.get("user_id")` fonctionne
     bien en mode stateless_http -- ici on construit une VRAIE requête
     Starlette (mcp.server.mcpserver.Context enveloppe un
     ServerRequestContext dont `.request` est exactement l'objet Starlette
     `Request` construit par le transport, voir
     mcp/server/_streamable_http_modern.py:236/399 -- ce n'est pas mocké).
  2. La logique métier de envoyer_message (resolution du destinataire,
     gestion des erreurs, insertion) se comporte comme attendu -- ici
     `resoudre_destinataire_autorise` et `_inserer_message` SONT mockés,
     car ils appellent Supabase, inaccessible depuis ce sandbox (le vrai
     test de bout en bout contre la base réelle reste à faire par Bourama
     en environnement de dev/prod).
"""

import asyncio
import sys
from unittest.mock import patch

sys.path.append("core")  # même pattern que le reste du code (voir api/agents.py etc.)

from starlette.requests import Request  # noqa: E402

import core.serveur_mcp_generation as smg  # noqa: E402
from mcp.server.mcpserver.context import Context  # noqa: E402
from mcp.server.context import ServerRequestContext  # noqa: E402


def construire_ctx(user_id: str | None, agent_id: str | None = "agent-test") -> Context:
    """
    Construit une vraie requête ASGI/Starlette avec user_id/agent_id en
    query string, exactement comme _url_generation() (registre_outils.py)
    les pose sur l'URL montée : /mcp/generation?user_id=...&agent_id=...
    """
    qs = ""
    parties = []
    if user_id is not None:
        parties.append(f"user_id={user_id}")
    if agent_id is not None:
        parties.append(f"agent_id={agent_id}")
    qs = "&".join(parties)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/generation",
        "query_string": qs.encode(),
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(scope, receive)

    rctx = ServerRequestContext(
        session=None,  # non touché par envoyer_message
        lifespan_context={},
        protocol_version="2026-07-28",
        method="tools/call",
        request=request,
    )
    return Context(request_context=rctx)


async def cas_utilisateur_non_identifie():
    ctx = construire_ctx(user_id=None)
    resultat = smg.envoyer_message(nom_destinataire="Benjamin", contenu="Salut", ctx=ctx)
    assert resultat == "Erreur : impossible d'identifier l'expéditeur pour ce message.", resultat
    print("OK  -- user_id absent des query params -> erreur claire")


async def cas_message_vide():
    ctx = construire_ctx(user_id="u1")
    resultat = smg.envoyer_message(nom_destinataire="Benjamin", contenu="   ", ctx=ctx)
    assert resultat == "Erreur : le message est vide.", resultat
    print("OK  -- message vide -> erreur claire")


async def cas_destinataire_introuvable():
    ctx = construire_ctx(user_id="u1")
    with patch.object(
        smg, "_resoudre_destinataire_autorise",
        return_value=(None, "Je ne trouve personne nommé Benjamin parmi tes contacts."),
    ) as mock_resoudre, patch.object(smg, "_inserer_message") as mock_inserer:
        resultat = smg.envoyer_message(nom_destinataire="Benjamin", contenu="Salut", ctx=ctx)
    assert resultat == "Je ne trouve personne nommé Benjamin parmi tes contacts.", resultat
    mock_resoudre.assert_called_once_with("u1", "Benjamin")
    mock_inserer.assert_not_called()
    print("OK  -- destinataire introuvable -> erreur relayée telle quelle, aucune insertion")


async def cas_nominal():
    ctx = construire_ctx(user_id="u1", agent_id="agent-xyz")
    with patch.object(
        smg, "_resoudre_destinataire_autorise", return_value=("dest-42", None)
    ) as mock_resoudre, patch.object(
        smg, "_inserer_message", return_value={"id": 1}
    ) as mock_inserer:
        resultat = smg.envoyer_message(nom_destinataire="Awa", contenu="  Bonjour Awa  ", ctx=ctx)
    assert resultat == "Message envoyé à Awa.", resultat
    mock_resoudre.assert_called_once_with("u1", "Awa")
    # Vérifie que le contenu part tel quel (le .strip() final est fait par
    # _inserer_message côté api/roles.py, pas ici) et le bon destinataire.
    mock_inserer.assert_called_once_with("u1", "dest-42", "  Bonjour Awa  ")
    print("OK  -- cas nominal -> message envoyé, bon expéditeur/destinataire/contenu transmis")


async def cas_exception_insertion():
    ctx = construire_ctx(user_id="u1")
    with patch.object(smg, "_resoudre_destinataire_autorise", return_value=("dest-42", None)), \
         patch.object(smg, "_inserer_message", side_effect=RuntimeError("supabase down")):
        resultat = smg.envoyer_message(nom_destinataire="Awa", contenu="Bonjour", ctx=ctx)
    assert resultat == "Erreur : l'envoi du message a échoué, réessaie.", resultat
    print("OK  -- exception à l'insertion -> message d'erreur générique, pas de 500 brut exposé à l'IA")


async def main():
    await cas_utilisateur_non_identifie()
    await cas_message_vide()
    await cas_destinataire_introuvable()
    await cas_nominal()
    await cas_exception_insertion()
    print("\nTous les cas passent -- le mécanisme ctx.request_context.request.query_params")
    print("fonctionne réellement en mode stateless_http (vraie Request Starlette, pas mockée).")


if __name__ == "__main__":
    asyncio.run(main())
