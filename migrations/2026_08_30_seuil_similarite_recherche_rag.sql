-- Ajout d'un seuil minimum de similarite aux 6 fonctions de recherche RAG
-- (30/08, demande Bourama : la recherche par pertinence renvoyait souvent
-- des resultats hors sujet -- ces fonctions renvoyaient toujours les N
-- resultats les plus proches, meme quand aucun n'etait vraiment pertinent).
-- Seuil par defaut 0.5 (p_seuil_similarite), ajustable si besoin apres test reel.
--
-- Fonctions concernees : recherche_bibliotheque, recherche_bibliotheque_publique,
-- recherche_catalogue_public, recherche_documents (x2 signatures), recherche_prompts (x2 signatures).
--
-- Applique directement via Supabase MCP (apply_migration), ce fichier ne fait
-- que documenter le changement dans le repo, comme les autres migrations trackees ici.

drop function if exists recherche_bibliotheque(vector, integer, uuid);
create function recherche_bibliotheque(
  query_embedding vector(768),
  match_count int,
  p_user_id uuid,
  p_seuil_similarite float default 0.5
)
returns table (
  contenu text, similarite float, fichier_id uuid, nom_fichier text,
  url_publique text, type_mime text, page_debut int, page_fin int,
  timestamp_debut numeric, timestamp_fin numeric
)
language sql
as $$
  select
    db.contenu, 1 - (db.embedding <=> query_embedding) as similarite,
    db.fichier_id, f.nom_fichier, f.url_publique, f.type_mime,
    db.page_debut, db.page_fin, db.timestamp_debut, db.timestamp_fin
  from public.documents_bibliotheque db
  left join public.fichiers_uploades f on f.id = db.fichier_id
  where db.user_id = p_user_id
    and (1 - (db.embedding <=> query_embedding)) >= p_seuil_similarite
  order by db.embedding <=> query_embedding
  limit match_count;
$$;

drop function if exists recherche_bibliotheque_publique(vector, integer, uuid[]);
create function recherche_bibliotheque_publique(
  query_embedding vector(768),
  match_count int,
  p_fichier_ids uuid[],
  p_seuil_similarite float default 0.5
)
returns table (
  contenu text, similarite float, fichier_id uuid, nom_fichier text,
  url_publique text, type_mime text, page_debut int, page_fin int,
  timestamp_debut numeric, timestamp_fin numeric
)
language sql stable
as $$
  select
    db.contenu, 1 - (db.embedding <=> query_embedding) as similarite,
    db.fichier_id, f.nom_fichier, f.url_publique, f.type_mime,
    db.page_debut, db.page_fin, db.timestamp_debut, db.timestamp_fin
  from documents_bibliotheque db
  join fichiers_uploades f on f.id = db.fichier_id
  where db.fichier_id = any(p_fichier_ids)
    and (1 - (db.embedding <=> query_embedding)) >= p_seuil_similarite
  order by db.embedding <=> query_embedding
  limit match_count;
$$;

drop function if exists recherche_catalogue_public(vector, integer);
create function recherche_catalogue_public(
  query_embedding vector(768),
  match_count int,
  p_seuil_similarite float default 0.5
)
returns table (
  fichier_id uuid, nom text, description text, url_publique text,
  type_mime text, similarite float
)
language sql stable
as $$
  select
    bp.id as fichier_id, bp.nom, bp.description, bp.url_publique, bp.type_mime,
    max(1 - (dcp.embedding <=> query_embedding)) as similarite
  from documents_catalogue_public dcp
  join bibliotheque_publique bp on bp.id = dcp.fichier_id
  where bp.statut = 'publie'
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime
  having max(1 - (dcp.embedding <=> query_embedding)) >= p_seuil_similarite
  order by similarite desc
  limit match_count;
$$;

drop function if exists recherche_documents(vector, integer);
create function recherche_documents(
  query_embedding vector(768),
  match_count int,
  p_seuil_similarite float default 0.5
)
returns table (contenu text, similarite float)
language sql
as $$
  select contenu, 1 - (embedding <=> query_embedding) as similarite
  from public.documents
  where (1 - (embedding <=> query_embedding)) >= p_seuil_similarite
  order by embedding <=> query_embedding
  limit match_count;
$$;

drop function if exists recherche_documents(vector, integer, text);
create function recherche_documents(
  query_embedding vector(768),
  match_count int,
  p_agent_id text,
  p_seuil_similarite float default 0.5
)
returns table (contenu text, similarite float)
language sql
as $$
  select contenu, 1 - (embedding <=> query_embedding) as similarite
  from public.documents
  where agent_id = p_agent_id
    and (1 - (embedding <=> query_embedding)) >= p_seuil_similarite
  order by embedding <=> query_embedding
  limit match_count;
$$;

drop function if exists recherche_prompts(vector, integer);
create function recherche_prompts(
  query_embedding vector(768),
  match_count int,
  p_seuil_similarite float default 0.5
)
returns table (contenu text, similarite float)
language sql
as $$
  select contenu, 1 - (embedding <=> query_embedding) as similarite
  from public.prompts_chunks
  where (1 - (embedding <=> query_embedding)) >= p_seuil_similarite
  order by embedding <=> query_embedding
  limit match_count;
$$;

drop function if exists recherche_prompts(vector, integer, text);
create function recherche_prompts(
  query_embedding vector(768),
  match_count int,
  p_agent_id text,
  p_seuil_similarite float default 0.5
)
returns table (contenu text, similarite float)
language sql
as $$
  select contenu, 1 - (embedding <=> query_embedding) as similarite
  from public.prompts_chunks
  where agent_id = p_agent_id
    and (1 - (embedding <=> query_embedding)) >= p_seuil_similarite
  order by embedding <=> query_embedding
  limit match_count;
$$;
