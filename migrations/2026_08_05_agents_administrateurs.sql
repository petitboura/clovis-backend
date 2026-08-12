-- Section "Administrer" de "Mon espace" (2026-08-05, demande Bourama).
--
-- Un créateur peut nommer quelqu'un administrateur d'une de ses IA :
-- cette personne voit alors directement la section "Administrer"
-- (accès à /dashboard/agents/{id}/admin) au lieu de "Mes IA" dans
-- "Mon espace".
--
-- Pas d'UI pour l'instant côté créateur pour désigner un administrateur
-- (décision Bourama, 2026-08-05) : la table est peuplée manuellement
-- par Bourama via le dashboard Supabase, comme `profiles.est_createur`.
--
-- (agent_id, user_id) en clé primaire : un agent peut avoir plusieurs
-- administrateurs, une personne peut administrer plusieurs agents.
create table if not exists agents_administrateurs (
  agent_id text not null references agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (agent_id, user_id)
);

create index if not exists idx_agents_administrateurs_user_id on agents_administrateurs(user_id);
