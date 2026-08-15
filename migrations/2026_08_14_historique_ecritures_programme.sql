-- Historique des ecritures MCP sur programme/comportement (14/08, demande
-- Bourama : "il faut que l'IA sache le modifier" + pouvoir annuler la
-- derniere ecriture). Une ligne par ajout/modification faite par l'IA (les
-- suppressions ne passent PAS par ici : elles demandent deja une
-- confirmation explicite avant execution, voir OUTILS_SENSIBLES dans
-- registre_outils.py, donc pas besoin d'un filet d'annulation en plus).
--
-- `avant` est NULL pour une creation (annuler = supprimer la ligne creee).
-- `avant` contient l'etat JSON precedent pour une modification (annuler =
-- restaurer ces champs). `type_cible` + `cible_id` desigent la ligne
-- affectee dans sa propre table (programmes/matieres/chapitres/
-- documents_programme/exercices_programme/examens_programme/
-- comportements_etudiants).
create table if not exists historique_ecritures_programme (
  id uuid primary key default gen_random_uuid(),
  proprietaire_id uuid not null references auth.users(id) on delete cascade,
  type_cible text not null check (type_cible in ('programme', 'matiere', 'chapitre', 'document', 'exercice', 'examen', 'comportement')),
  cible_id uuid not null,
  action text not null check (action in ('cree', 'modifie')),
  avant jsonb,
  annule boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists idx_historique_ecritures_proprietaire on historique_ecritures_programme(proprietaire_id, created_at desc);
