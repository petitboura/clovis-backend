-- Section "Notion-like" (Partie 2), 2026-08-20, demande Bourama -- chantier
-- distinct de la structure programme (Partie 1), lot 1/5.
--
-- Une page appartient directement à un utilisateur (proprietaire_id), peut
-- avoir une page parente (sous-pages imbriquées à volonté). Un bloc
-- appartient à une page, porte un type (texte, titre, liste, etc.) et son
-- contenu en JSON pour rester flexible aux blocs spéciaux ajoutés par les
-- lots suivants (équation LaTeX -- lot 2, vue de base de données -- lot 3).
--
-- Pas de RLS ici, même choix que migrations/2026_08_12_programme_structure.sql :
-- vérification de propriété faite côté application (api/pages_notion.py,
-- core/serveur_mcp_espace.py, core/serveur_mcp_generation.py), pas en base.

create table if not exists pages (
  id uuid primary key default gen_random_uuid(),
  proprietaire_id uuid not null references auth.users(id) on delete cascade,
  parent_id uuid references pages(id) on delete cascade,
  titre text not null default '',
  ordre int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_pages_proprietaire on pages(proprietaire_id);
create index if not exists idx_pages_parent on pages(parent_id);

create table if not exists blocs (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references pages(id) on delete cascade,
  type text not null default 'texte',  -- texte libre, pas de liste fermée -- voir TYPES_BLOCS_CONNUS dans api/pages_notion.py pour les types actuellement gérés côté affichage
  contenu jsonb not null default '{}'::jsonb,
  ordre int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_blocs_page on blocs(page_id);
