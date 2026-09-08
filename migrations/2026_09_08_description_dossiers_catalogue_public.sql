-- 08/09/2026, demande Bourama : les dossiers du catalogue public
-- doivent suivre la même logique que les fichiers -- il leur manquait
-- la description (ils avaient déjà pays/niveau/catégorie/classe/
-- spécialité, voir migrations du 02-04/09). Optionnelle, comme pour un
-- fichier.

alter table dossiers_catalogue_public
  add column if not exists description text not null default '';
