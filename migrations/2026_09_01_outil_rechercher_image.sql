-- Recherche d'image (01/09/2026, demande Bourama) : déclare le nouvel
-- outil rechercher_image comme disponible côté plateforme, même
-- mécanisme que consulter_programme (2026_08_13) -- sans cette ligne,
-- l'outil existe dans le code (core/serveur_mcp_generation.py) mais
-- n'est jamais proposé au modèle (voir
-- core/mcp_tools.py::lister_outils_autorises_pour_agent, catégorie 1 =
-- "generation").
-- Appliquée directement en base le 01/09 (voir historique migrations),
-- ce fichier sert de trace/rejouabilité.

insert into registre_outils_plateforme (nom_outil, categorie, nom_serveur, disponible, updated_at)
values ('rechercher_image', 1, 'generation', true, now())
on conflict (nom_outil) do update set disponible = true, updated_at = now();
