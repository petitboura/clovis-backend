-- Fondations du système établissement (Partie 9 du chantier confiance
-- pédagogique, demande Bourama 06/09/2026). Voir core/etablissements.py
-- et api/etablissements.py côté backend.
--
-- Décision prise avec Bourama avant d'écrire une ligne de cette partie
-- (constat d'audit n°1 du plan de travail) : nouvelles tables, complètement
-- séparées de l'ancien système profiles.role / etablissement_id /
-- enseignant_id (migration 2026_08_04_roles_hierarchie.sql), qui reste
-- intouché ici (toujours utilisé par api/permissions_hierarchie.py dans
-- api/agents.py). api/roles.py et api/invitations_clovis.py, qui portaient
-- l'ancienne sémantique de rôle fixe et n'étaient déjà branchés sur aucune
-- route active, sont déplacés en zone désactivée (voir
-- _desactive_roles_hierarchie/LISEZ_MOI_NE_JAMAIS_REUTILISER.md).

create table if not exists public.etablissements (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null unique references public.profiles(user_id),
  nom text not null,
  description text,
  site_web text,
  contact text,
  actif boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on table public.etablissements is
  'Profil établissement (Partie 9, 06/09/2026) : un profiles.user_id peut se déclarer établissement, devient visible publiquement. Distinct de l''ancien profiles.role=etablissement (migration 2026-08-04, non réutilisé).';
comment on column public.etablissements.actif is
  'False = établissement retiré de la section publique (soft delete), sans supprimer ses rattachements/publications existants.';
alter table public.etablissements enable row level security;

create table if not exists public.etablissements_rattachements (
  id uuid primary key default gen_random_uuid(),
  etablissement_id uuid not null references public.etablissements(id) on delete cascade,
  utilisateur_id uuid not null references public.profiles(user_id),
  etat text not null check (etat in ('suivi', 'demande_en_attente', 'accepte')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (etablissement_id, utilisateur_id)
);
comment on table public.etablissements_rattachements is
  'Une ligne par (établissement, utilisateur), Partie 9. etat=suivi (immédiat, contenu public + notif) / demande_en_attente (action "se connecter", en attente de validation) / accepte (rattachement réel validé par l''établissement, contenu privé + compte pour la cascade Partie 10).';
alter table public.etablissements_rattachements enable row level security;

create index if not exists idx_etablissements_rattachements_etablissement
  on public.etablissements_rattachements(etablissement_id, etat);
create index if not exists idx_etablissements_rattachements_utilisateur
  on public.etablissements_rattachements(utilisateur_id, etat);

create table if not exists public.etablissements_publications (
  id uuid primary key default gen_random_uuid(),
  etablissement_id uuid not null references public.etablissements(id) on delete cascade,
  auteur_id uuid not null references public.profiles(user_id),
  titre text,
  contenu text not null,
  visibilite text not null default 'publique' check (visibilite in ('publique', 'privee')),
  statut text not null default 'en_attente' check (statut in ('en_attente', 'valide', 'refuse')),
  created_at timestamptz not null default now(),
  valide_le timestamptz,
  valide_par uuid references public.profiles(user_id)
);
comment on table public.etablissements_publications is
  'Publication au nom d''un établissement (Partie 9). auteur_id = établissement lui-même (statut=valide immédiat) ou une personne en rattachement accepté (statut=en_attente, doit être validée par l''établissement avant diffusion).';
alter table public.etablissements_publications enable row level security;

create index if not exists idx_etablissements_publications_etablissement
  on public.etablissements_publications(etablissement_id, statut, visibilite, created_at desc);

-- Nouveaux types de notification pour le système établissement, réutilisant
-- le centre de notifications déjà en place (core/notifications.py) plutôt
-- que d'en construire un nouveau (constat d'audit n°6 du plan de travail).
alter table public.notifications drop constraint if exists notifications_type_check;
alter table public.notifications add constraint notifications_type_check
  check (type = any (array[
    'follow','comment','rating','categorie_manquante','agent_update',
    'feedback','nouvel_outil_disponible','outil_retire',
    'annonce_etablissement','message_direct',
    'rappel_echu','action_ia_terminee','document_recu_code','message_systeme',
    'etablissement_publication','etablissement_demande_connexion','etablissement_demande_acceptee'
  ]));
