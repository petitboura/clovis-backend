-- Partage de dossiers precis via un code (02/09/2026, demande Bourama :
-- remplacer le partage "toute la bibliotheque" (colonne booleenne
-- partage_bibliotheque sur codes_partage) par un partage cible, dossier
-- par dossier -- un code peut desormais partager PLUSIEURS dossiers a
-- la fois (many-to-many, meme principe que codes_partage_comportements
-- deja en place). Partager un dossier partage aussi tous ses
-- sous-dossiers (confirme par Bourama).

create table if not exists codes_partage_dossiers (
  code_id uuid not null references codes_partage(id) on delete cascade,
  dossier_id uuid not null references dossiers_bibliotheque(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (code_id, dossier_id)
);
create index if not exists idx_codes_partage_dossiers_code on codes_partage_dossiers(code_id);
create index if not exists idx_codes_partage_dossiers_dossier on codes_partage_dossiers(dossier_id);

-- Dossier "miroir" cree automatiquement chez chaque receveur, avec le
-- meme nom (et la meme hierarchie de sous-dossiers) que le dossier
-- source chez le proprietaire. Un seul miroir par (dossier source,
-- receveur) -- reutilise a chaque nouvel ajout, jamais recree.
create table if not exists miroirs_dossiers_partages (
  dossier_source_id uuid not null references dossiers_bibliotheque(id) on delete cascade,
  receveur_id uuid not null,
  dossier_miroir_id uuid not null references dossiers_bibliotheque(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (dossier_source_id, receveur_id)
);
create index if not exists idx_miroirs_dossiers_receveur on miroirs_dossiers_partages(receveur_id);

alter table codes_partage drop column if exists partage_bibliotheque;
