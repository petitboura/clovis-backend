# Instructions permanentes pour Claude sur ce dépôt

## Jamais de tirets doubles ("--") dans un texte AFFICHÉ

Bourama déteste totalement les "--" (substitut d'em-dash) dans tout texte
qu'un utilisateur ou lui-même voit à l'écran. Concrètement, jamais dans :
- les messages d'erreur renvoyés par l'API (`erreur_api`, exceptions, etc.)
- le texte que les outils MCP renvoient à l'IA pour être relayé en chat
  (core/serveur_mcp_espace.py, core/serveur_mcp_generation.py)
- tout label, nom, description généré dynamiquement et stocké en base
  pour être affiché (skills, comportements, plugins...)

Ça reste totalement acceptable dans :
- le code lui-même (commentaires, docstrings) -- jamais vu par l'utilisateur
- les séparateurs markdown intentionnels type `"\n\n---\n\n"` (ligne
  horizontale), ce n'est pas un em-dash, c'est un délimiteur de section

À la place d'un em-dash dans un texte affiché : une virgule, un point, une
parenthèse, ou reformuler la phrase. Exemple concret corrigé le 20/08/2026 :
`f"(Source : {nom} -- {url})"` → `f"(Source : {nom}, {url})"`.

Cette règle est un standing instruction : la vérifier avant de livrer tout
texte destiné à être affiché, sans que Bourama ait à la répéter.
