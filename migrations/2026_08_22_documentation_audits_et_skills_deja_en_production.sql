-- Migration de RATTRAPAGE (22/08/2026, suite audit du dépôt demandé par
-- Bourama). Ces tables et colonnes existent DÉJÀ en production (vérifié
-- directement dans Supabase : audits_chapitre a 65 lignes, audits_matiere
-- 8 lignes, audits_programme 1 ligne, comportements_etudiants a bien
-- skill_md/nom/depuis_audit remplis) mais n'avaient jamais été commit
-- comme migration -- probablement appliquées à la main ou via un autre
-- outil. Ce fichier ne fait que documenter l'état réel déjà en place,
-- avec des "if not exists" partout : aucun changement de comportement
-- attendu en l'appliquant sur la prod actuelle, mais indispensable pour
-- qu'un nouvel environnement (staging, restauration) reparte du même état.
--
-- Colonnes manquantes sur comportements_etudiants (skill_md/nom générés
-- par _generer_skill, voir core/comportements_etudiants.py ; depuis_audit
-- posé par _synchroniser_skill_audit, voir core/audit_programme.py) :
alter table comportements_etudiants add column if not exists skill_md text;
alter table comportements_etudiants add column if not exists nom text;
alter table comportements_etudiants add column if not exists depuis_audit boolean not null default false;

-- Audit d'un chapitre (auditer_chapitre, core/audit_programme.py) : un
-- seul audit par chapitre (upsert sur chapitre_id), lots jsonb pour le
-- chunking par lot, hash_contenu pour détecter si une ré-exécution est
-- nécessaire.
create table if not exists audits_chapitre (
  id uuid primary key default gen_random_uuid(),
  chapitre_id uuid not null unique references chapitres(id) on delete cascade,
  proprietaire_id uuid not null,
  texte text not null default '',
  lots jsonb not null default '[]',
  hash_contenu text,
  derniere_execution timestamptz,
  updated_at timestamptz not null default now()
);

-- Audit d'une matière (auditer_matiere) : un seul audit par matière,
-- avec ses propres chunks pour la recherche vectorielle (voir table
-- suivante).
create table if not exists audits_matiere (
  id uuid primary key default gen_random_uuid(),
  matiere_id uuid not null unique references matieres(id) on delete cascade,
  proprietaire_id uuid not null references auth.users(id) on delete cascade,
  texte text not null,
  derniere_execution timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  hash_source text
);

-- OBSOLÈTE (confirmé par Bourama, 22/08) : reliquat d'une ancienne
-- version du pipeline d'audit de matière, qui découpait le texte en
-- chunks pour une recherche vectorielle (RAG). Depuis, le texte d'un
-- audit est directement transformé en skill via _generer_skill (voir
-- core/comportements_etudiants.py), plus besoin de chunks/embedding.
-- Table gardée (31 lignes existantes, pas de suppression demandée) mais
-- plus aucun code actuel n'écrit ni ne lit dedans -- ne pas s'en servir
-- comme référence pour du nouveau code.
create table if not exists audits_matiere_chunks (
  id uuid primary key default gen_random_uuid(),
  audit_id uuid not null references audits_matiere(id) on delete cascade,
  proprietaire_id uuid not null references auth.users(id) on delete cascade,
  contenu text not null,
  embedding vector(768) not null,
  created_at timestamptz not null default now()
);
create index if not exists idx_audits_matiere_chunks_audit on audits_matiere_chunks(audit_id);

-- Audit d'un programme entier (auditer_programme) : un seul audit par
-- programme.
create table if not exists audits_programme (
  id uuid primary key default gen_random_uuid(),
  programme_id uuid not null unique references programmes(id) on delete cascade,
  proprietaire_id uuid not null,
  texte text not null default '',
  hash_source text,
  derniere_execution timestamptz,
  updated_at timestamptz not null default now()
);
