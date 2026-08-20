-- Section "Notion-like" (Partie 2), lot 2/5 -- LaTeX + pages carrefour,
-- 2026-08-20, demande Bourama.
--
-- LaTeX : pas de migration nécessaire -- blocs.type est déjà un texte
-- libre (voir migrations/2026_08_20_pages_blocs_notion.sql), le nouveau
-- type "equation" est géré côté code (TYPES_BLOCS_CONNUS, api/pages_notion.py
-- et core/pages_notion_llm.py).
--
-- Pages carrefour : une page carrefour ne contient pas de contenu propre,
-- elle pointe vers un ou plusieurs éléments ailleurs dans l'app
-- (programme / matière / chapitre / document -- système existant, voir
-- api/contenu_programme.py). Référence polymorphe (type_cible + cible_id),
-- même principe que classement_transversal_items (api/programmes.py) et
-- documents_bibliotheque_emplacements (core/bibliotheque_programme.py) --
-- pas de vraie foreign key SQL possible puisque cible_id peut pointer
-- vers plusieurs tables différentes selon type_cible.

alter table pages add column if not exists est_carrefour boolean not null default false;

create table if not exists pages_carrefour_references (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references pages(id) on delete cascade,
  type_cible text not null,  -- "programme" | "matiere" | "chapitre" | "document"
  cible_id uuid not null,
  ordre int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists idx_pages_carrefour_page on pages_carrefour_references(page_id);
