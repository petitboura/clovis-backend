-- 21/08/2026, 3 demandes Bourama regroupées dans une même migration :
--
-- 1) "ajoute activer et désactiver aux comportements" : un comportement
--    inactif reste visible/modifiable dans "Mes comportements" mais
--    n'est plus jamais proposé au petit routeur (choisir_comportements_
--    pertinents dans core/main.py) -- désactiver != supprimer.
alter table comportements_etudiants add column if not exists actif boolean not null default true;

-- 2) "les comportements aussi, je veux un onglet public... quelqu'un
--    peut l'uploader et l'activer" : même mécanique que
--    plugins_programme/plugin_telechargements (2026_08_12), mais pour un
--    comportement individuel plutôt qu'un programme entier. Publier
--    prend un instantané (nom, description, texte, skill_md) du
--    comportement source -- l'original de l'auteur n'est jamais touché
--    ni lié après coup (même philosophie que _cloner_programme : une
--    copie indépendante). "Activer" un comportement public crée une
--    ligne comportements_etudiants normale (actif=true) chez l'utilisateur
--    qui active, pour l'agent "Mon espace" (AGENT_ID_ESPACE).
create table if not exists comportements_publics (
  id uuid primary key default gen_random_uuid(),
  auteur_id uuid not null references auth.users(id) on delete cascade,
  nom text not null,
  description text not null default '',
  texte text not null,
  skill_md text not null default '',
  activations_count int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists idx_comportements_publics_auteur on comportements_publics(auteur_id);

create table if not exists comportement_public_activations (
  id uuid primary key default gen_random_uuid(),
  comportement_public_id uuid not null references comportements_publics(id) on delete cascade,
  active_par uuid not null references auth.users(id) on delete cascade,
  comportement_etudiant_id uuid references comportements_etudiants(id) on delete set null,
  created_at timestamptz not null default now(),
  unique (comportement_public_id, active_par)  -- une activation compte une fois par utilisateur
);
create index if not exists idx_comportement_public_activations_source on comportement_public_activations(comportement_public_id);

-- 3) "un bibliothèque publique dans la section bibliothèque, tout le
--    monde peut y ajouter des documents, juste en le décrivant et en
--    donnant un nom" : catalogue partagé léger, DISTINCT de
--    fichiers_uploades (pas d'upload de fichier réel ici, juste un
--    nom + une description, éventuellement un lien) -- volontairement
--    pas branché sur consulter_bibliotheque/le RAG : c'est un catalogue
--    consultable par les humains dans l'appli, pas une source injectée
--    automatiquement dans les conversations de tout le monde (décision
--    prise faute d'instruction contraire de Bourama, à ajuster s'il le
--    demande).
create table if not exists bibliotheque_publique (
  id uuid primary key default gen_random_uuid(),
  ajoute_par uuid not null references auth.users(id) on delete cascade,
  nom text not null,
  description text not null default '',
  lien text,
  created_at timestamptz not null default now()
);
create index if not exists idx_bibliotheque_publique_ajoute_par on bibliotheque_publique(ajoute_par);
