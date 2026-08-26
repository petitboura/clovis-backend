-- 26/08/2026, Bourama : brancher le cerveau sur les dossiers designes
-- (suite du Lot 1A/1B, actions_appareil_mobile.py), connecter le chat
-- au canal d'actions mobile, capacite "dossiers".
--
-- Miroir cote backend de la liste des dossiers designes par l'etudiant
-- sur son telephone (SAF Android / security-scoped bookmarks iOS), pour
-- que l'agent sache QUELS NOMS de dossiers existent avant de creer une
-- action dessus (voir core/dossiers_designes_mobile.py). Contient
-- volontairement UNIQUEMENT le nom, jamais l'URI/bookmark reel : l'URI
-- est propre a l'appareil (SAF/bookmark), n'a aucun sens cote serveur,
-- et ne doit jamais etre manipulee par l'agent, le ciblage d'une
-- action se fait par nom, resolu en URI localement par l'app (voir
-- ActionsAppareilExecuteur.kt/.swift cote clovis-frontend).
--
-- Synchronisation en mode miroir complet (l'app envoie sa liste
-- complete a chaque changement + a l'ouverture, voir POST
-- /api/appareils-mobiles/dossiers) : contrairement a
-- appareils_mobiles_push_tokens (qui s'accumule), un dossier peut etre
-- retire par l'etudiant, donc pas de simple upsert, le backend
-- remplace l'ensemble de la liste pour (user_id, plateforme) a chaque
-- synchronisation plutot que de laisser des entrees perimees trainer.

create table if not exists dossiers_designes_mobile (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    plateforme text not null check (plateforme in ('android', 'ios')),
    nom text not null,
    maj_le timestamptz not null default now(),
    unique (user_id, plateforme, nom)
);

create index if not exists idx_dossiers_designes_mobile_user
    on dossiers_designes_mobile (user_id);

alter table dossiers_designes_mobile enable row level security;

-- Aucune policy : jamais touchee directement par le frontend avec la cle
-- anon, uniquement par le backend (cle service_role) via
-- api/appareils_mobiles.py, meme modele que appareils_mobiles_push_tokens
-- et actions_appareil_mobile.
