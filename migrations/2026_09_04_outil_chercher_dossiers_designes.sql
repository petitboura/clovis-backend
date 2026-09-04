-- 04/09/2026, suite du chantier vectorisation en masse du dossier
-- désigné (voir migrations/2026_09_04_dossiers_designes_vectorisation.sql) :
-- déclare le nouvel outil chercher_dossiers_designes comme disponible
-- côté plateforme, même mécanisme que consulter_programme (voir
-- migrations/2026_08_13_outil_consulter_programme.sql) -- sans cette
-- ligne, l'outil existe dans le code (core/serveur_mcp_generation.py)
-- mais n'est jamais proposé au modèle/routeur (voir
-- core/mcp_tools.py::_outils_generation_disponibles).
-- Appliquée directement en base le 04/09 (voir historique migrations),
-- ce fichier sert de trace/rejouabilité.

insert into registre_outils_plateforme (nom_outil, categorie, nom_serveur, disponible, updated_at)
values ('chercher_dossiers_designes', 1, 'generation', true, now())
on conflict (nom_outil) do update set disponible = true, updated_at = now();
