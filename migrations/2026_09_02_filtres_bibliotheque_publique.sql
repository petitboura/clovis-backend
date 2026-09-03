-- 02/09/2026, demande Bourama : en plus du filtre par type déjà
-- existant, 3 nouveaux filtres pour la bibliothèque publique -- pays,
-- classe/niveau, catégorie -- cochables par le publieur au moment de
-- publier un dossier ou un fichier. Listes SÉPARÉES de la table
-- `categories` déjà utilisée pour les IA (décision explicite de
-- Bourama, pas de réutilisation). Champs optionnels à la publication.
--
-- Stockage : `nom` dénormalisé directement sur l'entrée (même
-- convention que plugins_programme.niveau, "dénormalisé... pour la
-- recherche"), PAS de clé étrangère stricte -- une valeur tapée qui
-- n'existe pas encore est simplement ajoutée à la liste au passage
-- (voir core/listes_bibliotheque_publique.py::normaliser_et_enregistrer),
-- jamais bloquante.

create table if not exists bibliotheque_publique_pays (
  id uuid primary key default gen_random_uuid(),
  nom text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists bibliotheque_publique_classes (
  id uuid primary key default gen_random_uuid(),
  nom text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists bibliotheque_publique_categories (
  id uuid primary key default gen_random_uuid(),
  nom text not null unique,
  created_at timestamptz not null default now()
);

alter table bibliotheque_publique add column if not exists pays text;
alter table bibliotheque_publique add column if not exists classe text;
alter table bibliotheque_publique add column if not exists categorie text;

alter table dossiers_catalogue_public add column if not exists pays text;
alter table dossiers_catalogue_public add column if not exists classe text;
alter table dossiers_catalogue_public add column if not exists categorie text;

create index if not exists idx_bibliotheque_publique_pays on bibliotheque_publique(pays);
create index if not exists idx_bibliotheque_publique_classe on bibliotheque_publique(classe);
create index if not exists idx_bibliotheque_publique_categorie on bibliotheque_publique(categorie);
create index if not exists idx_dossiers_catalogue_public_pays on dossiers_catalogue_public(pays);
create index if not exists idx_dossiers_catalogue_public_classe on dossiers_catalogue_public(classe);
create index if not exists idx_dossiers_catalogue_public_categorie on dossiers_catalogue_public(categorie);
