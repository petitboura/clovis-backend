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

AUTHENTIFICATION : ce serveur agit comme "Resource Server" OAuth 2.1
(RFC 9728), verifiant chaque jeton via core/mcp_auth_public.py (voir ce
fichier pour le detail complet -- verificateur/AuthSettings partages
avec core/serveur_mcp_espace.py, pour ne pas dupliquer de logique de
securite entre les deux serveurs publics).

ETAT ACTUEL :
- Authentification : branchee (voir ci-dessus).
- Outils : uniquement des outils de test (`ping`, `qui_suis_je`) --
  aucun outil metier ici, ils vivent dans core/serveur_mcp_espace.py
  (bibliotheque/memoire/comportements/historique), monte separement.
- `description` : redigee ci-dessous a partir de ce que le connecteur
  permet reellement (Partie 3 = serveur_mcp_espace.py).

Monte en sous-application ASGI dans api/main.py, exactement comme
core/serveur_mcp_generation.py et core/serveur_mcp_github.py, sur le
chemin "/mcp/public".
"""

from mcp.server.mcpserver import Context, Icon, MCPServer as FastMCP

from core.mcp_auth_public import (
    VerificateurJetonSupabase,
    construire_auth_settings,
    user_id_depuis_contexte as _user_id_depuis_contexte,
)

mcp_public = FastMCP(
    name="clovis_public",
    title="Clovis",
    description=(
        "Assistant pedagogique Clovis : consultez et gerez votre "
        "bibliotheque de documents, votre memoire, vos comportements "
        "personnalises et votre historique de conversation, directement "
        "depuis Claude."
    ),
    website_url="https://clovis-ai.vercel.app/",
    icons=[
        Icon(
            src="https://clovis-ai.vercel.app/icone-512.png",
            mime_type="image/png",
            sizes=["512x512"],
        ),
    ],
    token_verifier=VerificateurJetonSupabase(),
    auth=construire_auth_settings("/mcp/public"),
)


@mcp_public.tool()
def ping() -> str:
    """Outil de test : confirme que le serveur MCP public de Clovis répond.

    Aucune donnée utilisateur -- sert uniquement à valider que la connexion
    (client MCP externe -> ce serveur) fonctionne de bout en bout, avant
    authentification. Reste utile comme sonde de santé.
    """
    return "pong depuis Clovis"


@mcp_public.tool()
def qui_suis_je(ctx: Context) -> str:
    """Outil de test AUTHENTIFIÉ : confirme que la vérification du jeton
    OAuth fonctionne de bout en bout (Claude connecté -> Supabase ->
    jeton vérifié ici -> identité récupérée), sans toucher à aucune
    donnée métier. Les vrais outils authentifiés vivent dans
    core/serveur_mcp_espace.py.
    """
    user_id = _user_id_depuis_contexte(ctx)
    if not user_id:
        return "Aucune identité vérifiée -- authentification manquante ou invalide."
    return f"Authentifié avec succès sur Clovis (id utilisateur : {user_id})."
