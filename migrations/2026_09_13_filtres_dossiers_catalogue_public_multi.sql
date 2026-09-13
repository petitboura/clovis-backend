-- 13/09/2026, demande Bourama : un DOSSIER du catalogue public (pas un
-- fichier, qui garde une seule valeur par filtre, inchangé) doit pouvoir
-- avoir PLUSIEURS valeurs pour chacun de ses 5 filtres (pays, niveau,
-- categorie, classe, specialite), au lieu d'une seule aujourd'hui.
-- Modifiable après coup en plus (nouvelle route PATCH .../filtres, voir
-- api/dossiers_catalogue_public.py), alors que ces filtres n'étaient pas
-- du tout modifiables jusqu'ici.
--
-- Chaque colonne text -> text[] sur dossiers_catalogue_public
-- UNIQUEMENT. La table bibliotheque_publique (fichiers) n'est PAS
-- concernée par ce chantier -- ses colonnes pays/niveau/categorie/
-- classe/specialite restent des valeurs uniques (text), inchangées.
--
-- Conversion sans perte : une valeur existante 'Mali' devient ['Mali'],
-- une valeur vide/NULL devient un tableau vide.

alter table dossiers_catalogue_public
  alter column pays type text[] using (case when pays is null or pays = '' then array[]::text[] else array[pays] end);

alter table dossiers_catalogue_public
  alter column niveau type text[] using (case when niveau is null or niveau = '' then array[]::text[] else array[niveau] end);

alter table dossiers_catalogue_public
  alter column categorie type text[] using (case when categorie is null or categorie = '' then array[]::text[] else array[categorie] end);

alter table dossiers_catalogue_public
  alter column classe type text[] using (case when classe is null or classe = '' then array[]::text[] else array[classe] end);

alter table dossiers_catalogue_public
  alter column specialite type text[] using (case when specialite is null or specialite = '' then array[]::text[] else array[specialite] end);

alter table dossiers_catalogue_public alter column pays set default array[]::text[];
alter table dossiers_catalogue_public alter column niveau set default array[]::text[];
alter table dossiers_catalogue_public alter column categorie set default array[]::text[];
alter table dossiers_catalogue_public alter column classe set default array[]::text[];
alter table dossiers_catalogue_public alter column specialite set default array[]::text[];

-- Les anciens index simples ne sont plus utiles sur un tableau (une
-- recherche "contient cette valeur" a besoin d'un index GIN, pas
-- b-tree) -- remplacés en conséquence.
drop index if exists idx_dossiers_catalogue_public_pays;
drop index if exists idx_dossiers_catalogue_public_classe;
drop index if exists idx_dossiers_catalogue_public_categorie;

create index if not exists idx_dossiers_catalogue_public_pays_gin on dossiers_catalogue_public using gin (pays);
create index if not exists idx_dossiers_catalogue_public_niveau_gin on dossiers_catalogue_public using gin (niveau);
create index if not exists idx_dossiers_catalogue_public_categorie_gin on dossiers_catalogue_public using gin (categorie);
create index if not exists idx_dossiers_catalogue_public_classe_gin on dossiers_catalogue_public using gin (classe);
create index if not exists idx_dossiers_catalogue_public_specialite_gin on dossiers_catalogue_public using gin (specialite);
