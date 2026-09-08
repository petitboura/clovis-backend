-- 07/09/2026, demande Bourama : permettre à l'auteur d'un skill public de
-- le retirer du catalogue. Meme convention que bibliotheque_publique
-- (statut publie/retire) plutot qu'une suppression definitive, pour ne
-- jamais casser la contrainte de cle etrangere avec
-- comportement_public_activations (les copies deja activees restent
-- des copies independantes chez leurs utilisateurs, non affectees).
alter table public.comportements_publics
  add column statut text not null default 'publie' check (statut in ('publie', 'retire')),
  add column retire_le timestamptz;
