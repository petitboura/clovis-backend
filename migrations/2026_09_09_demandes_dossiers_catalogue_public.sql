-- Confirmation contributeurs (09/09/2026, demande Bourama) : dans un
-- dossier à contribution libre, un contributeur (pas forcement le
-- createur) peut desormais demander a deplacer ou supprimer un fichier
-- ou un sous-dossier, en plus d'ajouter (deja possible, immediat,
-- inchange). Rien ne change reellement tant que la bonne personne n'a
-- pas confirme :
--   - pour un fichier : le createur du dossier qui le contient
--     actuellement (dossier_id ci-dessous).
--   - pour un sous-dossier : le createur du sous-dossier lui-meme
--     (dossier_id ci-dessous pointe alors directement sur ce
--     sous-dossier).
-- Cette personne peut aussi refuser explicitement (statut 'refusee').
-- Ne concerne jamais le dossier racine (contribution_libre) lui-meme,
-- ni ranger/ajouter (voir core/dossiers_catalogue_public.py).

create table if not exists demandes_dossiers_catalogue_public (
  id uuid primary key default gen_random_uuid(),
  action text not null check (action in ('deplacer_fichier', 'supprimer_fichier', 'deplacer_dossier', 'supprimer_dossier')),
  fichier_id uuid references bibliotheque_publique(id) on delete cascade,
  dossier_id uuid not null references dossiers_catalogue_public(id) on delete cascade,
  dossier_destination_id uuid references dossiers_catalogue_public(id) on delete cascade,
  demandeur_id uuid references auth.users(id) on delete set null,
  createur_id uuid references auth.users(id) on delete set null,
  statut text not null default 'en_attente' check (statut in ('en_attente', 'confirmee', 'refusee')),
  created_at timestamptz not null default now(),
  traite_at timestamptz
);
create index if not exists idx_demandes_dossiers_catalogue_public_createur on demandes_dossiers_catalogue_public(createur_id, statut);
create index if not exists idx_demandes_dossiers_catalogue_public_dossier on demandes_dossiers_catalogue_public(dossier_id);

-- Nouveaux types de notification (voir core/notifications.py) : la
-- contrainte de la table notifications doit lister explicitement
-- chaque type autorise.
alter table notifications drop constraint if exists notifications_type_check;
alter table notifications add constraint notifications_type_check check (
  type = any (array[
    'follow', 'comment', 'rating', 'categorie_manquante', 'agent_update',
    'feedback', 'nouvel_outil_disponible', 'outil_retire', 'annonce_etablissement',
    'message_direct', 'rappel_echu', 'action_ia_terminee', 'document_recu_code',
    'message_systeme', 'etablissement_publication', 'etablissement_demande_connexion',
    'etablissement_demande_acceptee', 'audit_hebdomadaire_corrections', 'correction_traitee',
    'nouvelle_version_disponible', 'cascade_supervision_j2', 'cascade_supervision_j5',
    'cascade_supervision_equipe_clovis', 'cascade_supervision_resolue',
    'demande_confirmation_dossier_public', 'demande_dossier_public_traitee'
  ]::text[])
);
