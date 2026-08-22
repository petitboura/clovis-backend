-- 22/08/2026, demande Bourama : "rendre la section bibliothèque plus
-- sérieuse, notamment la version publique" -- suite du guide Notion
-- "Guide pour droit d'auteur" (Phase 1 : CGU + politique copyright +
-- formulaire de signalement + procédure de retrait), appliquée aux DEUX
-- surfaces publiques identifiées à l'audit :
--   1) bibliotheque_publique (catalogue partagé, 21/08)
--   2) documents classés dans un emplacement de programme couvert par un
--      plugin contribution_libre (20/08, voir emplacement_couvert_par_
--      plugin_public dans core/bibliotheque_programme.py)
--
-- Mode de modération choisi par Bourama : publication immédiate (pas de
-- validation a priori), retrait a posteriori sur signalement traité par
-- un admin (_est_admin, voir api/permissions_hierarchie.py).

-- 1) Statut + traçabilité du retrait sur bibliotheque_publique. Un
-- retrait passe désormais par ce statut plutôt que par une suppression
-- définitive (garde une trace pour l'admin, contrairement au DELETE
-- self-service existant sur /api/bibliotheque-publique/{id} qui reste
-- inchangé pour le contributeur d'origine).
alter table bibliotheque_publique
  add column if not exists statut text not null default 'publie';
alter table bibliotheque_publique
  add constraint if not exists bibliotheque_publique_statut_valide
  check (statut in ('publie', 'retire'));
alter table bibliotheque_publique add column if not exists retire_motif text;
alter table bibliotheque_publique add column if not exists retire_le timestamptz;

create index if not exists idx_bibliotheque_publique_statut on bibliotheque_publique(statut);

-- 2) Signalements -- couvre les deux surfaces ci-dessus avec une seule
-- table (type_signalement distingue laquelle). Toujours une seule des
-- deux paires de colonnes cible renseignée selon le type. `lien_document`
-- est un libellé de repli affiché à l'admin même si l'entrée ciblée a
-- depuis été retirée/supprimée (garde le signalement lisible dans tous
-- les cas).
create table if not exists signalements_bibliotheque (
  id uuid primary key default gen_random_uuid(),
  type_signalement text not null check (type_signalement in ('bibliotheque_publique', 'document_programme')),

  -- Cible si type_signalement = 'bibliotheque_publique'.
  bibliotheque_publique_id uuid references bibliotheque_publique(id) on delete cascade,

  -- Cible si type_signalement = 'document_programme' : le document ET
  -- l'emplacement précis (many-to-many, voir bibliotheque_emplacements_
  -- programme) -- le retrait ne déclasse QUE cet emplacement-là, jamais
  -- le fichier lui-même ni ses autres classements.
  fichier_id uuid references fichiers_uploades(id) on delete cascade,
  type_emplacement text,
  emplacement_id uuid,

  lien_document text not null,
  motif text not null,
  plaignant_nom text not null,
  plaignant_email text not null,
  plaignant_organisation text,
  declaration_honneur boolean not null default false,

  statut text not null default 'en_attente' check (statut in ('en_attente', 'traite')),
  action text check (action in ('retire', 'rejete')),
  notes_admin text,
  traite_par uuid references auth.users(id) on delete set null,
  traite_le timestamptz,

  created_at timestamptz not null default now()
);

create index if not exists idx_signalements_bibliotheque_statut on signalements_bibliotheque(statut);
create index if not exists idx_signalements_bibliotheque_created_at on signalements_bibliotheque(created_at desc);

-- 3) Contenu légal (CGU + politique de copyright) -- éditable sans
-- déploiement de code, même principe que agents_administrateurs :
-- table peuplée/modifiable directement par Bourama via le dashboard
-- Supabase (repli volontaire, pas de source Notion pour ce lot -- déjà
-- utilisée pour le system prompt, pas branchée ici pour rester simple).
create table if not exists contenu_legal (
  cle text primary key check (cle in ('cgu', 'copyright')),
  titre text not null,
  contenu_markdown text not null,
  updated_at timestamptz not null default now()
);

insert into contenu_legal (cle, titre, contenu_markdown) values
(
  'cgu',
  'Conditions d''utilisation — Bibliothèque publique',
  E'## Ce que tu peux publier\n\nLa bibliothèque publique permet à n''importe quel utilisateur connecté d''ajouter un document (nom, description, fichier) visible par tout le monde.\n\nEn ajoutant un document, tu garantis que :\n- tu détiens les droits sur ce document, ou tu as l''autorisation explicite de le partager ;\n- ce document ne viole aucun droit d''auteur ni aucune propriété intellectuelle d''un tiers.\n\n## Ce qui est interdit\n\nIl est interdit d''uploader des livres, manuels, articles ou tout autre contenu protégé sans l''autorisation de son auteur ou éditeur.\n\n## Modération\n\nNous nous réservons le droit de retirer tout contenu, sans préavis, et de suspendre ou bannir un compte en cas d''infraction répétée. Voir la [politique de copyright](/copyright) pour la procédure de signalement.'
),
(
  'copyright',
  'Politique de droit d''auteur',
  E'## Signaler un contenu\n\nSi tu es l''auteur, l''éditeur, ou l''ayant droit d''un contenu publié sans autorisation dans la bibliothèque publique ou dans un programme partagé de Clovis, tu peux le signaler via le formulaire disponible sur chaque document.\n\n## Procédure\n\n1. Réception du signalement.\n2. Vérification par un administrateur.\n3. Si l''infraction est confirmée : retrait du document et information du contributeur.\n\n## Récidive\n\n1er incident : avertissement et retrait. 2e incident : suspension temporaire. 3e incident : bannissement définitif.\n\n## Contact\n\nPour toute question, utilise le formulaire de signalement présent sur chaque document concerné.'
)
on conflict (cle) do nothing;
