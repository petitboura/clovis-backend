"""
Serveur MCP PUBLIC de Clovis -- destine a etre ajoute comme connecteur
personnalise dans un client MCP externe (Claude), pas un serveur interne
consomme par l'agent Clovis lui-meme (voir core/serveur_mcp_generation.py
et core/serveur_mcp_github.py pour ceux-la).

Difference cle avec les serveurs internes : celui-ci EST vu directement
par un humain (selecteur de connecteurs dans Claude.ai) -- d'ou les
metadonnees title/description/website_url/icons, absentes des serveurs
internes (qui ne declarent que `name`).

REGLE PRODUIT NON NEGOCIABLE (voir README clovis-frontend) : ce
connecteur est celui de CLOVIS, jamais de "Djiguigne". Le title, la
description, l'icone et le website_url ne doivent jamais laisser
transparaitre l'ecosysteme derriere -- voir le nom technique interne
ci-dessous ("clovis_public"), qui lui n'est jamais vu par l'utilisateur.

Limite connue cote Claude.ai au moment de l'ecriture : un connecteur
personnalise ajoute par URL affiche une icone generique quel que soit ce
que le serveur envoie ; seuls les connecteurs du repertoire officiel
Anthropic ont droit a leur icone. Les metadonnees ci-dessous restent
neanmoins la bonne chose a preparer cote code (le protocole les attend,
et rien n'empeche qu'elles servent plus tard, y compris pour d'autres
clients MCP).

ETAT ACTUEL -- squelette minimal seulement :
- Aucun outil metier pour l'instant (bibliotheque / memoire /
  comportements / historique -- chantier separe, pas encore construit).
- Aucune authentification externe pour l'instant (chantier separe, pas
  encore construite) -- ce serveur n'est donc pas a exposer publiquement
  tel quel avant que l'authentification soit en place.
- `description` volontairement laissee vide : a rediger une fois les
  outils ci-dessus construits, pour refleter ce que le connecteur permet
  reellement de faire.

Monte en sous-application ASGI dans api/main.py, exactement comme
core/serveur_mcp_generation.py et core/serveur_mcp_github.py.
"""

from mcp.server.mcpserver import Icon, MCPServer as FastMCP

mcp_public = FastMCP(
    name="clovis_public",
    title="Clovis",
    description="",  # TODO: a rediger une fois les outils "Mon espace" construits
    website_url="https://classgpt-frontend.vercel.app/",
    icons=[
        Icon(
            src="https://classgpt-frontend.vercel.app/icone-512.png",
            mime_type="image/png",
            sizes=["512x512"],
        ),
    ],
)
