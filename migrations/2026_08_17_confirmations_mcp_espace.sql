-- Confirmations en attente pour l'outil MCP externe discuter_avec_clovis
-- (17/08/2026, demande Bourama : "renvoyer la demande à Claude" quand
-- Clovis veut utiliser un outil sensible pendant qu'il répond à un
-- message envoyé par Claude via /mcp/espace).
--
-- etat_reprise (voir _evenement_confirmation, core/main.py) contient des
-- secrets en clair dans table_routage (cle API Tavily partagee, jetons
-- Notion/GitHub par utilisateur) -- il ne doit JAMAIS transiter par un
-- parametre d'outil MCP vers un client externe. Cette table le garde
-- entierement cote serveur : seul un id court + un resume lisible
-- (message/nom_outil/arguments) sont renvoyes a Claude, jamais la ligne
-- elle-meme. Voir core/confirmations_mcp.py.
--
-- Duree de vie courte et volontaire (voir expire_a) : une confirmation
-- non traitee est un etat d'agent Groq en memoire (messages_agent,
-- outils_mcp...) qui n'a aucune raison de rester valide au-dela de
-- quelques minutes.
create table if not exists confirmations_mcp_espace (
  id uuid primary key default gen_random_uuid(),
  proprietaire_id uuid not null references auth.users(id) on delete cascade,
  nom_outil text not null,
  message text not null,
  arguments jsonb not null default '{}'::jsonb,
  etat_reprise jsonb not null,
  created_at timestamptz not null default now(),
  expire_a timestamptz not null default (now() + interval '15 minutes')
);
create index if not exists idx_confirmations_mcp_espace_proprietaire on confirmations_mcp_espace(proprietaire_id);
