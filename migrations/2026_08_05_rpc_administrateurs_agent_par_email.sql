-- Section "Administrateurs" sur /dashboard/agents/{id}/modifier (vitrine,
-- dépôt djiguigne-ai), demande Bourama 2026-08-05 : le créateur ajoute un
-- administrateur en entrant son email, sans confirmation. auth.users n'est
-- pas exposé via REST (schema "auth"), donc deux fonctions RPC security
-- definer pour passer par le service role du backend :
--   - email_vers_user_id : email -> user_id (ou NULL si aucun compte)
--   - lister_administrateurs_agent : (user_id, email) des admins actuels
--     d'un agent, pour affichage dans la section.
--
-- Appliquée directement via Supabase MCP le 2026-08-05, ce fichier n'est
-- qu'une trace versionnée (même convention que les migrations précédentes).

create or replace function public.email_vers_user_id(p_email text)
returns uuid
language sql
security definer
set search_path = auth, public
as $$
  select id from auth.users where lower(email) = lower(trim(p_email)) limit 1;
$$;

create or replace function public.lister_administrateurs_agent(p_agent_id text)
returns table(user_id uuid, email text)
language sql
security definer
set search_path = auth, public
as $$
  select a.user_id, u.email
  from public.agents_administrateurs a
  join auth.users u on u.id = a.user_id
  where a.agent_id = p_agent_id
  order by a.created_at asc;
$$;
