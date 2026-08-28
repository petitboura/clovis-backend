-- RAG sur le catalogue de la bibliothèque publique (28/08/2026, demande
-- Bourama : "un truc qui permet à l'IA de trouver un dossier ou fichier
-- dans la bibliothèque publique par RAG ... mais il sert juste à
-- trouver un document pas à l'utiliser pour répondre").
--
-- Distincte à la fois de documents_bibliotheque (RAG perso, scopé
-- user_id) et de recherche_bibliotheque_publique (RAG des plugins
-- publics/contribution_libre, scopé par une liste de fichier_id de
-- fichiers_uploades -- un système différent, voir
-- core/bibliotheque_programme.py). Ici, fichier_id référence
-- bibliotheque_publique (le catalogue "tout le monde peut y ajouter un
-- document", voir api/bibliotheque_publique.py). Pas de user_id : la
-- recherche porte sur TOUT le catalogue, pas un sous-ensemble par
-- utilisateur.
--
-- Appliqué directement via Supabase MCP (apply_migration), ce fichier
-- ne fait que documenter le changement dans le repo, comme les autres
-- migrations trackées ici.

create table if not exists documents_catalogue_public (
  id uuid primary key default gen_random_uuid(),
  fichier_id uuid not null references bibliotheque_publique(id) on delete cascade,
  contenu text not null,
  embedding vector(768) not null,
  page_debut int,
  page_fin int,
  timestamp_debut numeric,
  timestamp_fin numeric,
  created_at timestamptz not null default now()
);
create index if not exists idx_documents_catalogue_public_fichier on documents_catalogue_public(fichier_id);

-- Recherche sémantique sur TOUT le catalogue public. Ne renvoie JAMAIS
-- `contenu` -- volontaire : cet outil sert à l'IA à LOCALISER un
-- document (nom/description/lien), jamais à lui fournir un texte à
-- citer/paraphraser directement dans une réponse (voir
-- core/catalogue_public_rag.py:chercher_catalogue_public).
create or replace function recherche_catalogue_public(
  query_embedding vector(768),
  match_count int
)
returns table (
  fichier_id uuid,
  nom text,
  description text,
  url_publique text,
  type_mime text,
  similarite float
)
language sql stable
as $$
  select
    bp.id as fichier_id,
    bp.nom,
    bp.description,
    bp.url_publique,
    bp.type_mime,
    max(1 - (dcp.embedding <=> query_embedding)) as similarite
  from documents_catalogue_public dcp
  join bibliotheque_publique bp on bp.id = dcp.fichier_id
  where bp.statut = 'publie'
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime
  order by similarite desc
  limit match_count;
$$;
