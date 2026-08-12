-- Hiérarchie de rôles (nous/établissement/enseignant/étudiant), demande
-- Bourama 2026-08-04. Chaque enseignant et étudiant choisit son
-- rattachement à l'inscription (menu déroulant), pas d'invitation.
-- Voir api/roles.py côté backend. Appliquée directement via Supabase MCP
-- le 2026-08-04, ce fichier n'est qu'une trace versionnée (même
-- convention que 2026_08_02_modeles_premium.sql).

alter table public.profiles
  add column if not exists role text
    check (role in ('admin', 'etablissement', 'enseignant', 'etudiant')),
  add column if not exists etablissement_id uuid references public.profiles(user_id),
  add column if not exists enseignant_id uuid references public.profiles(user_id);

comment on column public.profiles.role is
  'Rôle hiérarchique (2026-08-04) : admin (nous) / etablissement / enseignant / etudiant. NULL = compte "classique" hors hiérarchie (créateur d''agent générique, comportement inchangé, tous les comptes existants avant cette migration).';
comment on column public.profiles.etablissement_id is
  'Rempli uniquement si role=enseignant : profiles.user_id de l''établissement choisi à l''inscription.';
comment on column public.profiles.enseignant_id is
  'Rempli uniquement si role=etudiant : profiles.user_id de l''enseignant choisi à l''inscription.';

create table if not exists public.messages_directs (
  id bigint generated always as identity primary key,
  expediteur_id uuid not null references auth.users(id),
  destinataire_id uuid not null references auth.users(id),
  contenu text not null,
  reponse_a bigint references public.messages_directs(id),
  lu boolean not null default false,
  created_at timestamptz not null default now()
);
comment on table public.messages_directs is
  'Messages courts établissement <-> enseignant (vice versa), 2026-08-04. Pas un chat persistant : chaque message peut avoir au plus une réponse (reponse_a), affiché comme notification + réponse rapide côté frontend.';
alter table public.messages_directs enable row level security;

alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check
  check (type = any (array[
    'follow','comment','rating','categorie_manquante','agent_update',
    'feedback','nouvel_outil_disponible','outil_retire',
    'annonce_etablissement','message_direct'
  ]));

alter table public.notifications
  add column if not exists message_id bigint references public.messages_directs(id);
comment on column public.notifications.message_id is
  'Rempli uniquement pour type=message_direct : référence vers messages_directs.id concerné.';

-- === Suite appliquée le même jour : triggers + fan-out annonces ===
-- (migration Supabase séparée "roles_hierarchie_triggers_annonces", même
-- convention que follows/agent_comments/agent_ratings : les LIGNES de
-- notifications sont créées par trigger, jamais insérées à la main dans
-- l'API -- voir docstring api/notifications.py.)

create or replace function public.notifier_message_direct()
returns trigger as $$
begin
  insert into public.notifications (user_id, type, acteur_id, message_id)
  values (new.destinataire_id, 'message_direct', new.expediteur_id, new.id);
  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists trg_notifier_message_direct on public.messages_directs;
create trigger trg_notifier_message_direct
  after insert on public.messages_directs
  for each row execute function public.notifier_message_direct();

create table if not exists public.annonces_etablissement (
  id bigint generated always as identity primary key,
  etablissement_id uuid not null references auth.users(id),
  contenu text not null,
  created_at timestamptz not null default now()
);
comment on table public.annonces_etablissement is
  'Annonce diffusée par un établissement à tous ses rattachés (enseignants + étudiants de ces enseignants), 2026-08-04. Une ligne ici = un fan-out de notifications type=annonce_etablissement via trigger.';
alter table public.annonces_etablissement enable row level security;

alter table public.notifications
  add column if not exists annonce_id bigint references public.annonces_etablissement(id);
comment on column public.notifications.annonce_id is
  'Rempli uniquement pour type=annonce_etablissement : référence vers annonces_etablissement.id concernée.';

create or replace function public.notifier_annonce_etablissement()
returns trigger as $$
begin
  insert into public.notifications (user_id, type, acteur_id, annonce_id)
  select user_id, 'annonce_etablissement', new.etablissement_id, new.id
  from public.profiles
  where etablissement_id = new.etablissement_id and role = 'enseignant';

  insert into public.notifications (user_id, type, acteur_id, annonce_id)
  select etu.user_id, 'annonce_etablissement', new.etablissement_id, new.id
  from public.profiles etu
  join public.profiles ens on ens.user_id = etu.enseignant_id
  where ens.etablissement_id = new.etablissement_id and etu.role = 'etudiant';

  return new;
end;
$$ language plpgsql security definer;

drop trigger if exists trg_notifier_annonce_etablissement on public.annonces_etablissement;
create trigger trg_notifier_annonce_etablissement
  after insert on public.annonces_etablissement
  for each row execute function public.notifier_annonce_etablissement();

-- === Ajout le même jour (suite de la demande) : IA modèles admin ===
-- (migrations Supabase séparées "agents_publiable_et_compte_admin(_v2)"
-- et "creer_ia_modeles_etablissement_enseignant")
--
-- alter table public.agents add column if not exists publiable boolean
--   not null default true;
-- -- False = agent gardé privé dans l'espace de son créateur (jamais
-- -- dans /api/feed, /api/search, /api/creators). Voir api/main.py,
-- -- api/search.py, api/creators.py, api/profiles.py (filtré comme
-- -- `actif`, sauf pour le propriétaire qui voit toujours tout).
--
-- update public.profiles set role = 'admin'
--   where user_id = '44f90ccc-09aa-45e2-9cf2-f5251733e05e'; -- Bourama (petitboura26@gmail.com)
--
-- insert into public.agents (id, nom, system_prompt, ui_config,
--   knowledge_source, owner_id, description, publiable) values
--   ('ia-etablissement-modele', 'IA Établissement (modèle)', ..., false),
--   ('ia-enseignant-modele', 'IA Enseignant (modèle)', ..., false);
-- -- Les 2 IA de démonstration demandées par Bourama ("crée en deux, pour
-- -- l'établissement et pour les enseignants, pour les étudiants ne crée
-- -- rien"), owner_id = son propre compte admin, visibles dans son "Mon
-- -- espace" (bouton Tester ajouté sur AgentCard.tsx côté frontend) mais
-- -- jamais dans le feed/recherche/portfolio public.
