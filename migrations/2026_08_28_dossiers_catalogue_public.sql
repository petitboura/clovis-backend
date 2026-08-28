-- Dossiers dans le catalogue public (28/08/2026, demande Bourama :
-- "dossier à contribution libre ou privée choisi par le créateur au
-- moment de publier le dossier").
--
-- Différence avec dossiers_bibliotheque (perso) : un dossier public
-- n'appartient pas exclusivement à un user_id pour tout -- son
-- créateur (cree_par) choisit à la création un `statut` :
--   - 'contribution_libre' : n'importe quel utilisateur connecté peut
--     y ranger/retirer un document du catalogue.
--   - 'privee' : seul le créateur peut y ranger/retirer des documents.
-- Renommer/supprimer le dossier reste réservé au créateur dans les
-- deux cas (même principe que dossiers_bibliotheque).
--
-- Contrairement à supprimer_dossier (perso), supprimer un dossier
-- public NE supprime JAMAIS les documents qu'il contenait : ce sont
-- des ressources partagées par toute la communauté, pas la propriété
-- du dossier -- seule la liaison disparaît (ON DELETE CASCADE).

create table if not exists dossiers_catalogue_public (
  id uuid primary key default gen_random_uuid(),
  cree_par uuid references auth.users(id) on delete set null,
  nom text not null,
  statut text not null default 'contribution_libre' check (statut in ('contribution_libre', 'privee')),
  dossier_parent_id uuid references dossiers_catalogue_public(id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists idx_dossiers_catalogue_public_parent on dossiers_catalogue_public(dossier_parent_id);

create table if not exists fichiers_dossiers_catalogue_public (
  fichier_id uuid not null references bibliotheque_publique(id) on delete cascade,
  dossier_id uuid not null references dossiers_catalogue_public(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (fichier_id, dossier_id)
);
create index if not exists idx_fichiers_dossiers_catalogue_public_fichier on fichiers_dossiers_catalogue_public(fichier_id);
create index if not exists idx_fichiers_dossiers_catalogue_public_dossier on fichiers_dossiers_catalogue_public(dossier_id);
