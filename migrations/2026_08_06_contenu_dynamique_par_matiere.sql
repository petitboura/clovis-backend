-- Contenu dynamique par matière (agent "Nitrux", 2026-08-06, demande
-- Bourama) -- pensé générique/réutilisable pour d'autres agents du même
-- genre plus tard, donc pas de préfixe "nitrux_" dans les noms.
--
-- Indépendant de l'ancien système établissement/enseignant/étudiant
-- (profiles.role/enseignant_id, désactivé) : ici, n'importe quel compte
-- connecté peut écrire du contenu pour une matière (devenir "enseignant"
-- pour cette matière précise sur cet agent), et n'importe quel compte
-- peut entrer un code pour débloquer ce contenu ("étudiant").

-- Un flag par agent : seuls les agents marqués ici recalculent leur
-- system_prompt dynamiquement à chaque message (voir
-- core/contenu_dynamique_matiere.py) -- tous les autres agents
-- continuent avec get_system_prompt() / le cache habituel, aucune
-- régression.
alter table agents add column if not exists contenu_dynamique_par_matiere boolean not null default false;

-- Contenu écrit par un "enseignant" pour une matière précise, sur un
-- agent précis : un system_prompt + un code unique généré à la création,
-- partagé aux étudiants. Un enseignant peut couvrir plusieurs matières
-- (plusieurs lignes), mais une seule ligne par (agent, enseignant,
-- matière) -- réécrire la même matière met à jour le system_prompt sans
-- changer le code déjà partagé.
create table if not exists contenus_par_matiere (
  id uuid primary key default gen_random_uuid(),
  agent_id text not null references agents(id) on delete cascade,
  enseignant_id uuid not null references auth.users(id) on delete cascade,
  matiere text not null,
  system_prompt text not null,
  code text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (agent_id, enseignant_id, matiere)
);

create index if not exists idx_contenus_par_matiere_agent on contenus_par_matiere(agent_id);
create index if not exists idx_contenus_par_matiere_enseignant on contenus_par_matiere(agent_id, enseignant_id);

-- Rattachement d'un étudiant à un contenu (donc à un enseignant, pour une
-- matière précise) via un code entré. Un étudiant peut accumuler
-- plusieurs rattachements (une matière différente à chaque fois, ou même
-- plusieurs enseignants pour LA MÊME matière -- dans ce cas un seul des
-- deux est "actif" à la fois, l'étudiant choisit/bascule via un bouton
-- dans le chat, voir PATCH .../rattachements/{contenu_id}/activer).
create table if not exists rattachements_par_matiere (
  id uuid primary key default gen_random_uuid(),
  agent_id text not null references agents(id) on delete cascade,
  etudiant_id uuid not null references auth.users(id) on delete cascade,
  contenu_id uuid not null references contenus_par_matiere(id) on delete cascade,
  matiere text not null,
  actif boolean not null default true,
  created_at timestamptz not null default now(),
  unique (etudiant_id, contenu_id)
);

create index if not exists idx_rattachements_par_matiere_etudiant on rattachements_par_matiere(agent_id, etudiant_id);

-- Un seul rattachement actif par (étudiant, agent, matière) à la fois --
-- empêche d'avoir deux enseignants actifs en même temps pour une même
-- matière, indépendamment de combien de rattachements inactifs existent.
create unique index if not exists idx_rattachement_actif_unique
  on rattachements_par_matiere(etudiant_id, agent_id, matiere)
  where actif;
