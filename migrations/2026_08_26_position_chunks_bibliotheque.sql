-- Localisation précise d'un chunk dans son document d'origine (26/08,
-- demande Bourama : citations cliquables avec deux popups séparées --
-- la SOURCE (document entier) et le PARAGRAPHE exact utilisé, positionné
-- directement au bon endroit : la page pour un PDF, l'instant pour un
-- audio). Null pour tout ce qui n'a pas de notion de position (note
-- texte, image, lien) -- ceux-là gardent un seul clic (voir
-- core/bibliotheque_rag.py:formater_source_bibliotheque).
--
-- Appliqué directement via Supabase MCP (apply_migration), ce fichier
-- ne fait que documenter le changement dans le repo, comme les autres
-- migrations trackées ici.

alter table documents_bibliotheque
  add column if not exists page_debut int,
  add column if not exists page_fin int,
  add column if not exists timestamp_debut numeric,
  add column if not exists timestamp_fin numeric;

drop function if exists recherche_bibliotheque(vector, integer, uuid);
drop function if exists recherche_bibliotheque_publique(vector, integer, uuid[]);

create function recherche_bibliotheque(
  query_embedding vector(768),
  match_count int,
  p_user_id uuid
)
returns table (
  contenu text,
  similarite float,
  fichier_id uuid,
  nom_fichier text,
  url_publique text,
  type_mime text,
  page_debut int,
  page_fin int,
  timestamp_debut numeric,
  timestamp_fin numeric
)
language sql
as $$
  select
    db.contenu,
    1 - (db.embedding <=> query_embedding) as similarite,
    db.fichier_id,
    f.nom_fichier,
    f.url_publique,
    f.type_mime,
    db.page_debut,
    db.page_fin,
    db.timestamp_debut,
    db.timestamp_fin
  from public.documents_bibliotheque db
  left join public.fichiers_uploades f on f.id = db.fichier_id
  where db.user_id = p_user_id
  order by db.embedding <=> query_embedding
  limit match_count;
$$;

create function recherche_bibliotheque_publique(
  query_embedding vector(768),
  match_count int,
  p_fichier_ids uuid[]
)
returns table (
  contenu text,
  similarite float,
  fichier_id uuid,
  nom_fichier text,
  url_publique text,
  type_mime text,
  page_debut int,
  page_fin int,
  timestamp_debut numeric,
  timestamp_fin numeric
)
language sql stable
as $$
  select
    db.contenu,
    1 - (db.embedding <=> query_embedding) as similarite,
    db.fichier_id,
    f.nom_fichier,
    f.url_publique,
    f.type_mime,
    db.page_debut,
    db.page_fin,
    db.timestamp_debut,
    db.timestamp_fin
  from documents_bibliotheque db
  join fichiers_uploades f on f.id = db.fichier_id
  where db.fichier_id = any(p_fichier_ids)
  order by db.embedding <=> query_embedding
  limit match_count;
$$;
