-- "Mes comportements" : Bourama veut pouvoir en mettre PLUSIEURS, pas un
-- seul texte fourre-tout (06/08/2026) -- retire la contrainte unique
-- (agent_id, etudiant_id) posée dans la migration précédente, qui
-- limitait à une seule ligne. `created_at` sert à afficher la liste dans
-- l'ordre d'ajout côté frontend.
alter table comportements_etudiants drop constraint if exists comportements_etudiants_agent_id_etudiant_id_key;
alter table comportements_etudiants add column if not exists created_at timestamptz not null default now();
