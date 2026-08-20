-- Section "Notion-like" (Partie 2), lot 3/5 -- bases de révision +
-- gestion de tâches, 2026-08-20, demande Bourama.
--
-- Un seul mécanisme générique de "base de données" sert aux deux usages
-- (fiches de révision ET tâches/devoirs) -- pas deux systèmes séparés,
-- voir chantier-programme-etudiant.md partie 2 : "peut réutiliser le
-- même mécanisme". Une base est rattachée à UNE page (voir migration
-- 2026_08_20_pages_blocs_notion.sql) via un bloc de type "base_donnees"
-- dont le contenu référence son id -- pas de table de blocs spéciale.
--
-- Volontairement simple, comme le reste de cette section : pas de
-- relations entre bases, pas de formules calculées, pas de filtres/tris
-- avancés (voir chantier-programme-etudiant.md partie 2, fonctionnalités
-- écartées).
--
-- Les 4 vues (liste/tableau/calendrier/kanban) ne sont PAS stockées
-- séparément : elles se calculent côté frontend (lot 5) à partir des
-- mêmes propriétés/éléments/valeurs -- changer de vue ne duplique rien.
-- `vue_par_defaut` retient juste la dernière vue choisie par confort.

create table if not exists bases_donnees (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references pages(id) on delete cascade,
  titre text not null default '',
  vue_par_defaut text not null default 'tableau',  -- liste | tableau | calendrier | kanban, pas de contrainte stricte -- valeur de confort, jamais bloquante
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_bases_donnees_page on bases_donnees(page_id);

-- Colonnes personnalisables de la base (ex: "Statut", "Priorité",
-- "Date de révision", "Fait"). `options` sert aux types à choix (ex:
-- statut) : liste de {label, couleur}. La priorité d'une tâche est une
-- propriété de type "statut" comme une autre, pas une colonne à part.
create table if not exists bases_donnees_proprietes (
  id uuid primary key default gen_random_uuid(),
  base_id uuid not null references bases_donnees(id) on delete cascade,
  nom text not null,
  type text not null default 'texte',  -- texte | nombre | date | statut | case_a_cocher
  options jsonb not null default '[]'::jsonb,
  ordre int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists idx_bases_donnees_proprietes_base on bases_donnees_proprietes(base_id);

-- Un élément de la base (une fiche de révision, une tâche...).
-- `parent_element_id` permet les sous-tâches (gestion de tâches) --
-- sans lien de parenté pour une fiche de révision classique.
create table if not exists bases_donnees_elements (
  id uuid primary key default gen_random_uuid(),
  base_id uuid not null references bases_donnees(id) on delete cascade,
  parent_element_id uuid references bases_donnees_elements(id) on delete cascade,
  ordre int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_bases_donnees_elements_base on bases_donnees_elements(base_id);
create index if not exists idx_bases_donnees_elements_parent on bases_donnees_elements(parent_element_id);

-- Valeur d'UN élément pour UNE propriété -- une ligne par case remplie,
-- pas une colonne par propriété (les propriétés sont dynamiques,
-- ajoutées librement par l'étudiant).
create table if not exists bases_donnees_valeurs (
  id uuid primary key default gen_random_uuid(),
  element_id uuid not null references bases_donnees_elements(id) on delete cascade,
  propriete_id uuid not null references bases_donnees_proprietes(id) on delete cascade,
  valeur jsonb not null default 'null'::jsonb,
  unique (element_id, propriete_id)
);
create index if not exists idx_bases_donnees_valeurs_element on bases_donnees_valeurs(element_id);
