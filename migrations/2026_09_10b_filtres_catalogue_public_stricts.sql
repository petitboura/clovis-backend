-- 10/09/2026 (suite), retour en arrière demandé par Bourama sur le
-- comportement "permissif" de la migration précédente
-- (2026_09_10_filtres_catalogue_public_permissifs.sql) : les
-- documents sans valeur renseignée sur un champ filtré ne doivent PLUS
-- être inclus par défaut -- la cause réelle des documents manquants
-- était les deux autres bugs déjà corrigés (fonctions RPC dupliquées
-- + comparaison sensible à la casse), pas l'absence de valeur.
--
-- Comportement final : chaque filtre FOURNI doit matcher EXACTEMENT
-- (insensible casse/espaces, ça reste), et c'est un ET entre tous les
-- filtres fournis. Un filtre NON fourni (paramètre null) reste
-- totalement ignoré, aucune contrainte sur ce champ.

create or replace function recherche_catalogue_public(
  query_embedding vector(768),
  match_count int,
  p_seuil_similarite float default 0.5,
  p_dossier_id uuid default null,
  p_pays text default null,
  p_niveau text default null,
  p_categorie text default null,
  p_classe text default null,
  p_specialite text default null
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
    and (p_dossier_id is null or exists (
      select 1 from fichiers_dossiers_catalogue_public fdcp
      where fdcp.fichier_id = bp.id and fdcp.dossier_id = p_dossier_id
    ))
    and (p_pays is null or lower(trim(bp.pays)) = lower(trim(p_pays)))
    and (p_niveau is null or lower(trim(bp.niveau)) = lower(trim(p_niveau)))
    and (p_categorie is null or lower(trim(bp.categorie)) = lower(trim(p_categorie)))
    and (p_classe is null or lower(trim(bp.classe)) = lower(trim(p_classe)))
    and (p_specialite is null or lower(trim(bp.specialite)) = lower(trim(p_specialite)))
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime
  having max(1 - (dcp.embedding <=> query_embedding)) >= p_seuil_similarite
  order by similarite desc
  limit match_count;
$$;

create or replace function recherche_catalogue_public_mots_cles(
  p_question text,
  match_count integer,
  p_dossier_id uuid default null,
  p_pays text default null,
  p_niveau text default null,
  p_categorie text default null,
  p_classe text default null,
  p_specialite text default null
)
returns table(fichier_id uuid, nom text, description text, url_publique text, type_mime text, similarite double precision)
language sql stable
set search_path to 'public', 'extensions'
as $$
  select
    bp.id as fichier_id, bp.nom, bp.description, bp.url_publique, bp.type_mime,
    greatest(
      ts_rank_cd(to_tsvector('french', coalesce(bp.nom, '') || ' ' || coalesce(bp.description, '')), plainto_tsquery('french', p_question)),
      coalesce(max(ts_rank_cd(to_tsvector('french', coalesce(dcp.contenu, '')), plainto_tsquery('french', p_question))), 0),
      ts_rank_cd(to_tsvector('french', coalesce(bp.texte_brut, '')), plainto_tsquery('french', p_question))
    ) as similarite
  from bibliotheque_publique bp
  left join documents_catalogue_public dcp on dcp.fichier_id = bp.id
  where bp.statut = 'publie'
    and (p_dossier_id is null or exists (
      select 1 from fichiers_dossiers_catalogue_public fdcp
      where fdcp.fichier_id = bp.id and fdcp.dossier_id = p_dossier_id
    ))
    and (p_pays is null or lower(trim(bp.pays)) = lower(trim(p_pays)))
    and (p_niveau is null or lower(trim(bp.niveau)) = lower(trim(p_niveau)))
    and (p_categorie is null or lower(trim(bp.categorie)) = lower(trim(p_categorie)))
    and (p_classe is null or lower(trim(bp.classe)) = lower(trim(p_classe)))
    and (p_specialite is null or lower(trim(bp.specialite)) = lower(trim(p_specialite)))
    and (
      to_tsvector('french', coalesce(bp.nom, '') || ' ' || coalesce(bp.description, '')) @@ plainto_tsquery('french', p_question)
      or to_tsvector('french', coalesce(dcp.contenu, '')) @@ plainto_tsquery('french', p_question)
      or to_tsvector('french', coalesce(bp.texte_brut, '')) @@ plainto_tsquery('french', p_question)
    )
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime, bp.texte_brut
  order by similarite desc
  limit match_count;
$$;

create or replace function lister_catalogue_public_filtre(
  p_limite int,
  p_decalage int default 0,
  p_dossier_id uuid default null,
  p_pays text default null,
  p_niveau text default null,
  p_categorie text default null,
  p_classe text default null,
  p_specialite text default null
)
returns table (
  id uuid, nom text, description text, url_publique text, total bigint
)
language sql stable
as $$
  select
    bp.id, bp.nom, bp.description, bp.url_publique,
    count(*) over() as total
  from bibliotheque_publique bp
  where bp.statut = 'publie'
    and (p_dossier_id is null or exists (
      select 1 from fichiers_dossiers_catalogue_public fdcp
      where fdcp.fichier_id = bp.id and fdcp.dossier_id = p_dossier_id
    ))
    and (p_pays is null or lower(trim(bp.pays)) = lower(trim(p_pays)))
    and (p_niveau is null or lower(trim(bp.niveau)) = lower(trim(p_niveau)))
    and (p_categorie is null or lower(trim(bp.categorie)) = lower(trim(p_categorie)))
    and (p_classe is null or lower(trim(bp.classe)) = lower(trim(p_classe)))
    and (p_specialite is null or lower(trim(bp.specialite)) = lower(trim(p_specialite)))
  order by bp.created_at desc
  limit p_limite offset p_decalage;
$$;
