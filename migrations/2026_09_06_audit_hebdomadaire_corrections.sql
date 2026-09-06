-- Partie 8 (Point 4, 06/09/2026) : audit synthétique hebdomadaire des
-- corrections pédagogiques (voir migration 2026_09_06_corrections_pedagogiques.sql
-- pour la table corrections_pedagogiques dont ce chantier ne fait que
-- lire les lignes, jamais les modifier).

-- Nouveau type de notification, réutilisation du centre déjà en place
-- (core/notifications.py) plutôt que d'en construire un nouveau (voir
-- constat d'audit n°6 du plan de travail).
alter table notifications drop constraint if exists notifications_type_check;
alter table notifications add constraint notifications_type_check check (
  type = any (array[
    'follow', 'comment', 'rating', 'categorie_manquante', 'agent_update',
    'feedback', 'nouvel_outil_disponible', 'outil_retire',
    'annonce_etablissement', 'message_direct', 'rappel_echu',
    'action_ia_terminee', 'document_recu_code', 'message_systeme',
    'audit_hebdomadaire_corrections'
  ])
);

-- Cadence de l'audit, par prof, persistée pour survivre à un redémarrage
-- du process (même principe que les rappels de core/notifications_push.py) :
-- sans cette table, un redémarrage juste avant l'échéance renverrait un
-- audit à un moment aléatoire au lieu de respecter une vraie cadence
-- hebdomadaire, et surtout on ne saurait jamais qu'un audit a déjà été
-- envoyé cette semaine si le process redémarre entre-temps.
create table if not exists audits_hebdomadaires_corrections (
  prof_id uuid primary key references auth.users(id) on delete cascade,
  envoye_le timestamptz not null default now()
);

comment on table audits_hebdomadaires_corrections is
  'Dernier envoi de l''audit synthétique hebdomadaire des corrections pédagogiques par prof (Partie 8) : une ligne par prof, mise à jour à chaque envoi.';
