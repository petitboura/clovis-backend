-- Section "Mes comportements" (06/08/2026, demande Bourama) : un texte
-- libre écrit par l'ÉTUDIANT lui-même, qui s'ajoute EN PLUS du
-- system_prompt déjà résolu pour le message -- que ce soit le
-- généraliste de base, celui d'un enseignant (matière débloquée), ou le
-- prompt forcé via "Sans enseignant". Jamais un remplacement.
--
-- Affichage de la section pilotée par nous (comme
-- agents.bouton_sans_enseignant) -- pas encore lié à une IA "parents" ni
-- au niveau d'étude. Pour l'instant : Nitrux uniquement, défaut false
-- pour tout le reste.
alter table agents add column if not exists section_mes_comportements boolean not null default false;

create table if not exists comportements_etudiants (
  id uuid primary key default gen_random_uuid(),
  agent_id text not null references agents(id) on delete cascade,
  etudiant_id uuid not null references auth.users(id) on delete cascade,
  texte text not null default '',
  updated_at timestamptz not null default now(),
  unique (agent_id, etudiant_id)
);

create index if not exists idx_comportements_etudiants_etudiant on comportements_etudiants(agent_id, etudiant_id);
