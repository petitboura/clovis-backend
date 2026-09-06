-- 06/09/2026, Partie 1 du chantier "confiance pedagogique" (voir
-- clovis-plan-travail-10-parties.md) : nouvel objet de donnees pour
-- representer les notions d'un programme et leur statut d'avancement,
-- INDEPENDANT de l'ancien systeme "Programme" supprime le 28/08/2026
-- (voir migrations/2026_08_28_suppression_programme.sql) -- aucune de
-- ses tables (programmes/matieres/chapitres) n'est reutilisee ni
-- reintroduite ici, nommage volontairement different pour ne creer
-- aucune confusion avec l'ancien systeme.
--
-- Arborescence LIBRE (pas de niveaux figes type matiere/chapitre) :
-- une seule table auto-referencee, un prof peut fusionner/renommer/
-- reordonner sans contrainte de profondeur. Rattachee a un CODE
-- (codes_partage), pas a un role -- coherent avec le systeme "Mes
-- codes" deja en place (voir core/codes_partage.py).
create table if not exists notions (
  id uuid primary key default gen_random_uuid(),
  code_id uuid not null references codes_partage(id) on delete cascade,
  notion_parent_id uuid references notions(id) on delete cascade,
  nom text not null,
  statut text not null default 'a_venir' check (statut in ('a_venir', 'en_cours', 'acquis')),
  -- Ordre au sein d'un meme parent (et d'un meme code) -- gere par
  -- l'application (core/programme_notions.py:reordonner_notions), pas
  -- de contrainte d'unicite ici (des trous ou doublons transitoires ne
  -- genent pas, seul l'ordre relatif compte a la lecture).
  ordre integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_notions_code on notions(code_id);
create index if not exists idx_notions_parent on notions(notion_parent_id);
