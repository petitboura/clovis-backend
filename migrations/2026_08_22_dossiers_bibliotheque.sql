-- Dossiers/sous-dossiers dans la bibliotheque personnelle (22/08/2026,
-- demande explicite de Bourama : dossiers qui peuvent avoir des
-- sous-dossiers, superposes aux vues par type deja existantes
-- (Images/Audio/Documents/...) : si un dossier contient une image ET
-- un audio, ce meme dossier doit apparaitre a la fois dans l'onglet
-- Images et dans l'onglet Audio.
--
-- Sans rapport avec bibliotheque_emplacements_programme (classement
-- dans le Programme, matiere/chapitre) : deux systemes distincts, pas
-- touche ici.
--
-- Un fichier peut etre range dans plusieurs dossiers a la fois (confirme
-- par Bourama), d'ou la table de liaison many-to-many plutot qu'une
-- simple colonne dossier_id sur fichiers_uploades. Un dossier peut
-- melanger librement plusieurs types de fichiers (confirme aussi).

create table if not exists dossiers_bibliotheque (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  nom text not null,
  dossier_parent_id uuid references dossiers_bibliotheque(id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists idx_dossiers_bib_user on dossiers_bibliotheque(user_id);
create index if not exists idx_dossiers_bib_parent on dossiers_bibliotheque(dossier_parent_id);

-- Liaison many-to-many fichier <-> dossier. ON DELETE CASCADE des deux
-- cotes : si le dossier est supprime, la ligne de liaison disparait
-- (le fichier lui-meme n'est PAS supprime ici, voir la logique
-- applicative dans api/dossiers_bibliotheque.py qui, avant de supprimer
-- le dossier, supprime explicitement les fichiers qui n'auraient plus
-- aucun autre rattachement, confirme par Bourama : "le fichier est
-- garde uniquement si il est rattache a plusieurs dossiers").
create table if not exists fichiers_dossiers_bibliotheque (
  fichier_id uuid not null references fichiers_uploades(id) on delete cascade,
  dossier_id uuid not null references dossiers_bibliotheque(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (fichier_id, dossier_id)
);
create index if not exists idx_fichiers_dossiers_fichier on fichiers_dossiers_bibliotheque(fichier_id);
create index if not exists idx_fichiers_dossiers_dossier on fichiers_dossiers_bibliotheque(dossier_id);
