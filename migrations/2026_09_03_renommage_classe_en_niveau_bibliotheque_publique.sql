-- 03/09/2026, demande Bourama : renommer "classe" en "niveau" partout
-- (code + base de données) pour le chantier filtres bibliothèque
-- publique (voir migrations/2026_09_02_filtres_bibliotheque_publique.sql).
-- Renommage pur (table/colonnes/index), aucune perte de données.
-- Appliquée directement sur le projet Supabase "Djiguigne AI" (production).

alter table bibliotheque_publique_classes rename to bibliotheque_publique_niveaux;

alter table bibliotheque_publique rename column classe to niveau;
alter table dossiers_catalogue_public rename column classe to niveau;

alter index if exists idx_bibliotheque_publique_classe rename to idx_bibliotheque_publique_niveau;
alter index if exists idx_dossiers_catalogue_public_classe rename to idx_dossiers_catalogue_public_niveau;
