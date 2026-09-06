-- 06/09/2026, Partie 3 du chantier "confiance pedagogique" : declare
-- gerer_avancement_notions et consulter_avancement_notion comme
-- disponibles cote plateforme, meme mecanisme que les outils
-- precedents (voir migrations/2026_08_13_outil_consulter_programme.sql
-- et migrations/2026_09_04_outil_chercher_dossiers_designes.sql) --
-- sans ces lignes, les outils existent dans le code
-- (core/outils_avancement_notions.py) mais ne sont jamais proposes au
-- modele/routeur (voir core/mcp_tools.py::_outils_generation_disponibles).

insert into registre_outils_plateforme (nom_outil, categorie, nom_serveur, disponible, updated_at)
values
  ('gerer_avancement_notions', 1, 'generation', true, now()),
  ('consulter_avancement_notion', 1, 'generation', true, now())
on conflict (nom_outil) do update set disponible = true, updated_at = now();
