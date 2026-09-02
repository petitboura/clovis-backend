-- Miroirs des dossiers publics attachés à la bibliothèque perso
-- (02/09/2026, correction de la même journée -- voir core/dossiers_
-- publics_attaches.py : la première mouture était une simple ligne de
-- liaison, jugée insuffisante par Bourama : "il doit y être comme si
-- c'est toi qui l'a mis", donc une vraie copie physique).
--
-- Même principe que miroirs_dossiers_partages (migrations/2026_09_02_
-- partage_dossiers_bibliotheque.sql) pour le partage privé entre
-- utilisateurs, mais avec une source publique -- table séparée car la
-- contrainte de clé étrangère de dossier_source_id doit pointer vers
-- dossiers_catalogue_public ici, pas dossiers_bibliotheque.
--
-- Un seul miroir par (dossier public source, receveur) -- réutilisé à
-- chaque nouvel ajout (voir propager_fichier_public_range_dossier),
-- jamais recréé.

create table if not exists miroirs_dossiers_publics (
  dossier_source_id uuid not null references dossiers_catalogue_public(id) on delete cascade,
  receveur_id uuid not null,
  dossier_miroir_id uuid not null references dossiers_bibliotheque(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (dossier_source_id, receveur_id)
);
create index if not exists idx_miroirs_dossiers_publics_receveur on miroirs_dossiers_publics(receveur_id);
