-- Connecteur Google Drive (01/09/2026, demande Bourama) : déclare le
-- serveur MCP google_drive comme disponible côté plateforme, même
-- mécanisme que serveur_github/serveur_notion (catégorie 2/3 selon
-- qu'il nécessite ou non un utilisateur connecté -- google_drive est
-- "par utilisateur" comme notion, donc catégorie 3). Sans cette ligne,
-- le serveur existe dans le code (core/registre_outils.py) mais
-- core/mcp_tools.py::_serveurs_disponibles() ne le retient jamais.
-- Appliquée directement en base le 01/09 (voir historique migrations),
-- ce fichier sert de trace/rejouabilité.

insert into registre_outils_plateforme (nom_outil, categorie, nom_serveur, disponible, updated_at)
values ('serveur_google_drive', 3, 'google_drive', true, now())
on conflict (nom_outil) do update set disponible = true, updated_at = now();
