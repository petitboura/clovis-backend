-- 04/09/2026, demande Bourama : vectorisation automatique en masse de
-- tout le contenu (hormis vidéo) d'un dossier désigné sur le téléphone
-- (dossiers_designes_mobile.py), pour que l'IA retrouve un fichier
-- rapidement par contenu au lieu de fouiller un par un en direct
-- (core/exploration_dossier_mobile.py::chercher_par_contenu, lent).
--
-- Distinct de fichiers_uploades/documents_bibliotheque (ajout manuel
-- fichier par fichier à "Mon espace") : ici c'est TOUT le contenu d'un
-- dossier désigné, transféré automatiquement dès sa désignation. Table
-- séparée exprès -- ne touche rien à la bibliothèque perso existante.
--
-- `chemin` (jsonb, liste ordonnée de noms de sous-dossiers depuis la
-- racine désignée, JAMAIS le nom du fichier lui-même) permet à l'IA de
-- savoir de quel dossier/sous-dossier vient chaque résultat. Phase 2
-- (synchronisation renommage/déplacement/suppression, pas traitée ici)
-- s'appuiera sur hash_contenu pour retrouver un fichier déplacé/renommé
-- indépendamment de son chemin d'origine.
--
-- Réessai (04/09, précisé par Bourama) : PAS de réessai rapproché --
-- un échec passe direct en "echec" et n'est repris QUE par le réessai
-- automatique à froid, SANS plafond (illimité), contrairement à
-- MAX_TENTATIVES_AUTO de la bibliothèque perso -- voir
-- core/vectorisation_dossiers_designes.py.

create table if not exists fichiers_dossier_designe (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  plateforme text not null,
  dossier_nom text not null,
  chemin jsonb not null default '[]'::jsonb,
  nom_fichier text not null,
  type_mime text,
  taille_octets bigint,
  chemin_stockage text not null,
  url_publique text,
  hash_contenu text,
  statut_vectorisation text not null default 'en_attente',
  tentatives_vectorisation int not null default 0,
  erreur_vectorisation text,
  derniere_tentative_vectorisation_a timestamptz,
  created_at timestamptz not null default now(),
  unique (user_id, plateforme, dossier_nom, chemin, nom_fichier)
);

create index if not exists idx_fichiers_dossier_designe_user
  on fichiers_dossier_designe (user_id, dossier_nom, plateforme);

create index if not exists idx_fichiers_dossier_designe_statut
  on fichiers_dossier_designe (statut_vectorisation)
  where statut_vectorisation in ('en_attente', 'en_cours');

create index if not exists idx_fichiers_dossier_designe_hash
  on fichiers_dossier_designe (user_id, hash_contenu);

create table if not exists documents_dossier_designe (
  id uuid primary key default gen_random_uuid(),
  fichier_id uuid not null references fichiers_dossier_designe(id) on delete cascade,
  user_id uuid not null,
  contenu text not null,
  embedding vector(768) not null,
  page_debut int,
  page_fin int,
  timestamp_debut numeric,
  timestamp_fin numeric,
  created_at timestamptz not null default now()
);

create index if not exists idx_documents_dossier_designe_fichier
  on documents_dossier_designe (fichier_id);

-- Recherche sémantique, scopée par utilisateur -- même forme que
-- recherche_bibliotheque (core/bibliotheque_rag.py), avec dossier_nom
-- et chemin en plus pour que l'agent sache d'où vient chaque résultat.
create or replace function recherche_dossiers_designes(
  query_embedding vector(768),
  match_count int,
  p_user_id uuid,
  p_seuil_similarite float default 0.5
)
returns table (
  contenu text, similarite float, fichier_id uuid, nom_fichier text,
  dossier_nom text, chemin jsonb, url_publique text, type_mime text,
  page_debut int, page_fin int, timestamp_debut numeric, timestamp_fin numeric
)
language sql stable
as $$
  select
    dd.contenu, 1 - (dd.embedding <=> query_embedding) as similarite,
    dd.fichier_id, f.nom_fichier, f.dossier_nom, f.chemin, f.url_publique, f.type_mime,
    dd.page_debut, dd.page_fin, dd.timestamp_debut, dd.timestamp_fin
  from documents_dossier_designe dd
  join fichiers_dossier_designe f on f.id = dd.fichier_id
  where dd.user_id = p_user_id
    and (1 - (dd.embedding <=> query_embedding)) >= p_seuil_similarite
  order by dd.embedding <=> query_embedding
  limit match_count;
$$;
