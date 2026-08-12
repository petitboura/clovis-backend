-- Prompt personnalisé par utilisateur, pour des agents "partagés" où
-- un même agent (ex: Stirux pour les enseignants, Lirinus pour les
-- établissements) doit répondre différemment selon la personne connectée,
-- sans que ce ne soit un agent différent par utilisateur (2026-08-06,
-- demande Bourama).
--
-- Contrairement au system_prompt de base (stable, un seul par agent),
-- ceci ne varie PAS par message : une fois écrit, il reste le même
-- jusqu'à la prochaine modification par l'utilisateur. Le cache Groq
-- reste donc pleinement valable, pour un même utilisateur, entre ses
-- messages -- seul le préfixe "propre à cet utilisateur" diffère d'un
-- utilisateur à l'autre pour un même agent.
--
-- Pas d'UI pour l'instant côté utilisateur pour écrire ce prompt
-- (décision Bourama, 2026-08-06) : la table est peuplée manuellement,
-- comme agents_administrateurs et profiles.est_createur.
--
-- (agent_id, user_id) en clé primaire : un agent peut avoir un prompt
-- différent par utilisateur, une personne peut avoir un prompt
-- personnalisé sur plusieurs agents.
create table if not exists agents_prompts_utilisateur (
  agent_id text not null references agents(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  system_prompt text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (agent_id, user_id)
);

create index if not exists idx_agents_prompts_utilisateur_user_id on agents_prompts_utilisateur(user_id);

comment on table agents_prompts_utilisateur is
    'Surcharge du system_prompt de base, par (agent, utilisateur). Si une ligne existe pour la paire courante, elle remplace entièrement system_prompt dans _construire_system_prompt (core/main.py) -- pas de fusion des deux. Pensé pour des agents partagés (Stirux, Lirinus) où chaque utilisateur a sa propre version, sans dupliquer l''agent.';
