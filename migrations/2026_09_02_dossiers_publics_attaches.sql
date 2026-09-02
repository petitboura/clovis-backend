-- Attachement d'un dossier du catalogue public à la bibliothèque
-- personnelle d'un utilisateur (02/09/2026, demande Bourama : "ajouter
-- un dossier public à sa bibliothèque perso, peu importe la
-- contribution, et pouvoir librement le nourrir depuis sa bibliothèque
-- personnelle").
--
-- Principe confirmé par Bourama : attacher est libre pour N'IMPORTE
-- QUEL dossier public, quel que soit son statut (contribution_libre ou
-- privee), même créé par quelqu'un d'autre -- ce n'est qu'un
-- raccourci/vue, aucun droit supplémentaire. Le droit d'y AJOUTER un
-- document (nourrir) continue de suivre exactement les règles déjà en
-- place dans dossiers_catalogue_public.peut_ajouter_contenu -- pas de
-- nouvelle règle de permission ici.
--
-- Un dossier attaché n'est jamais copié : c'est une simple référence
-- (user_id, dossier_public_id). Le supprimer ne touche jamais au
-- dossier public lui-même ni à son contenu.

create table if not exists dossiers_publics_attaches (
  user_id uuid not null references auth.users(id) on delete cascade,
  dossier_public_id uuid not null references dossiers_catalogue_public(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, dossier_public_id)
);
create index if not exists idx_dossiers_publics_attaches_user on dossiers_publics_attaches(user_id);
create index if not exists idx_dossiers_publics_attaches_dossier on dossiers_publics_attaches(dossier_public_id);
