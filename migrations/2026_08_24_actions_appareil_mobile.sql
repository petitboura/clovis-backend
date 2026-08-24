-- 24/08/2026, Bourama : Lot 1A Partie 3 (app mobile), brancher le cerveau.
-- Table du canal de decision Clovis -> appareil : l'agent pose une action
-- ici, un push FCM/APNs (type="action", voir notifications_push.py)
-- reveille le telephone, qui vient chercher les details via
-- GET /api/appareils-mobiles/actions/{id} puis rapporte le resultat via
-- POST .../actions/{id}/resultat. Meme esprit que la table `rappels`
-- existante (planifier_rappel / traiter_rappels_echus), mais pour une
-- action a executer immediatement plutot qu'une notification programmee.

create table if not exists actions_appareil_mobile (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    type_action text not null,
    parametres jsonb not null default '{}'::jsonb,
    statut text not null default 'en_attente' check (statut in ('en_attente', 'executee', 'echouee')),
    resultat text,
    cree_le timestamptz not null default now(),
    execute_le timestamptz
);

create index if not exists idx_actions_appareil_mobile_user_statut
    on actions_appareil_mobile (user_id, statut);
