-- 09/09/2026, demande Bourama : le LLM n'avait aucune conscience des
-- dossiers du catalogue public ni des filtres (pays/niveau/categorie/
-- classe/specialite) dans sa recherche -- voir core/catalogue_public_rag.py
-- ("Pas de dossiers ici -- chantier separe, pas encore fait a ce stade").
-- Ajout de ces filtres, tous optionnels (defaut null = aucun filtre),
-- aux deux fonctions de recherche existantes. Retro-compatible : create
-- or replace sans toucher aux parametres existants, uniquement des
-- parametres optionnels en fin de signature.

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
    and (p_pays is null or bp.pays = p_pays)
    and (p_niveau is null or bp.niveau = p_niveau)
    and (p_categorie is null or bp.categorie = p_categorie)
    and (p_classe is null or bp.classe = p_classe)
    and (p_specialite is null or bp.specialite = p_specialite)
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime
  having max(1 - (dcp.embedding <=> query_embedding)) >= p_seuil_similarite
  order by similarite desc
  limit match_count;
$$;

-- recherche_catalogue_public_mots_cles : constat au passage, cette
-- fonction n'avait AUCUNE migration trackee dans le depot (creee
-- directement sur Supabase le 05/09) -- ce create or replace la fait
-- rentrer dans l'historique de migrations en plus d'ajouter les filtres.
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
    and (p_pays is null or bp.pays = p_pays)
    and (p_niveau is null or bp.niveau = p_niveau)
    and (p_categorie is null or bp.categorie = p_categorie)
    and (p_classe is null or bp.classe = p_classe)
    and (p_specialite is null or bp.specialite = p_specialite)
    and (
      to_tsvector('french', coalesce(bp.nom, '') || ' ' || coalesce(bp.description, '')) @@ plainto_tsquery('french', p_question)
      or to_tsvector('french', coalesce(dcp.contenu, '')) @@ plainto_tsquery('french', p_question)
      or to_tsvector('french', coalesce(bp.texte_brut, '')) @@ plainto_tsquery('french', p_question)
    )
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime, bp.texte_brut
  order by similarite desc
  limit match_count;
$$;
