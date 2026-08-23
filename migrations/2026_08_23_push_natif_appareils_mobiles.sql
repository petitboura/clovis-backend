-- Ajoutee le 23/08/2026, Bourama : Lot 3 Partie 3 (app mobile) --
-- Notifications & rappels.
--
-- Separee de `abonnements_push` (notifications push navigateur/PWA,
-- schema endpoint+p256dh+auth du Web Push standard) car un token
-- natif FCM (Android) ou APNs (iOS) n'a rien a voir avec ce schema :
-- juste une chaine opaque fournie par le SDK Firebase/Apple, a
-- renouveler de temps en temps par le telephone lui-meme.
--
-- Un meme utilisateur peut avoir plusieurs tokens (plusieurs
-- telephones, ou un token renouvele sans que l'ancien ait ete
-- explicitement desinscrit) -- pas de contrainte unique sur user_id.
-- Le token lui-meme est unique : si le meme token revient (meme
-- appareil, re-enregistrement), on ecrase la ligne existante plutot
-- que d'en dupliquer une.

create table if not exists appareils_mobiles_push_tokens (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    plateforme text not null check (plateforme in ('android', 'ios')),
    token text not null unique,
    cree_le timestamptz not null default now(),
    mis_a_jour_le timestamptz not null default now()
);

create index if not exists idx_appareils_mobiles_push_tokens_user
    on appareils_mobiles_push_tokens (user_id);

alter table appareils_mobiles_push_tokens enable row level security;

-- Aucune policy "utilisateur peut lire/ecrire ses propres tokens" :
-- cette table n'est jamais touchee directement par le frontend/l'app
-- avec la cle anon, uniquement par le backend (cle service_role, RLS
-- non applique) via api/appareils_mobiles.py -- meme modele que les
-- autres tables ecrites exclusivement cote serveur dans ce depot.
