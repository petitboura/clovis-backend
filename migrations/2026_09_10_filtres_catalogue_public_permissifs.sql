-- 10/09/2026, bugs remontés par Bourama en testant les filtres du
-- catalogue public (pays/niveau/categorie/classe/specialite) ajoutés
-- le 09/09 (voir migrations/2026_09_09_filtres_recherche_catalogue_public.sql) :
--
-- 1) Un document dont le champ filtré n'a jamais été renseigné
--    (ex. classe = null) était exclu dès qu'un filtre sur ce champ
--    était actif, alors qu'une recherche sans filtre le trouvait bien.
--    Décision Bourama : "il faut qu'on ne perde rien" -- un document
--    sans valeur sur le champ filtré reste maintenant inclus (on ne
--    peut pas prouver qu'il ne correspond pas), un document avec une
--    valeur EXPLICITEMENT différente reste exclu comme avant.
--
-- 2) Un document avec le champ filtré bien rempli pouvait quand même
--    ne pas remonter : l'égalité était sensible à la casse/aux
--    espaces (déjà documenté comme limite connue dans
--    core/listes_bibliotheque_publique.py -- "Mali" et "mali"
--    créeraient deux entrées distinctes). Confirmé en base (10/09) :
--    les valeurs classe existantes sont bien "Terminale" (majuscule),
--    un filtre envoyé en "terminale" ne matchait donc jamais rien.
--    Comparaison désormais faite via lower(trim(...)) des deux côtés.

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
    and (p_pays is null or bp.pays is null or lower(trim(bp.pays)) = lower(trim(p_pays)))
    and (p_niveau is null or bp.niveau is null or lower(trim(bp.niveau)) = lower(trim(p_niveau)))
    and (p_categorie is null or bp.categorie is null or lower(trim(bp.categorie)) = lower(trim(p_categorie)))
    and (p_classe is null or bp.classe is null or lower(trim(bp.classe)) = lower(trim(p_classe)))
    and (p_specialite is null or bp.specialite is null or lower(trim(bp.specialite)) = lower(trim(p_specialite)))
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
    and (p_pays is null or bp.pays is null or lower(trim(bp.pays)) = lower(trim(p_pays)))
    and (p_niveau is null or bp.niveau is null or lower(trim(bp.niveau)) = lower(trim(p_niveau)))
    and (p_categorie is null or bp.categorie is null or lower(trim(bp.categorie)) = lower(trim(p_categorie)))
    and (p_classe is null or bp.classe is null or lower(trim(bp.classe)) = lower(trim(p_classe)))
    and (p_specialite is null or bp.specialite is null or lower(trim(bp.specialite)) = lower(trim(p_specialite)))
    and (
      to_tsvector('french', coalesce(bp.nom, '') || ' ' || coalesce(bp.description, '')) @@ plainto_tsquery('french', p_question)
      or to_tsvector('french', coalesce(dcp.contenu, '')) @@ plainto_tsquery('french', p_question)
      or to_tsvector('french', coalesce(bp.texte_brut, '')) @@ plainto_tsquery('french', p_question)
    )
  group by bp.id, bp.nom, bp.description, bp.url_publique, bp.type_mime, bp.texte_brut
  order by similarite desc
  limit match_count;
$$;

-- lister_catalogue_public_filtre : nouvelle fonction dédiée pour
-- l'action "lister_catalogue_public" (pas de mot-clé, juste les plus
-- récents + filtres), remplace la construction de requête faite côté
-- Python (core/catalogue_public_rag.py) qui appliquait une égalité
-- stricte -- mêmes deux corrections que ci-dessus (permissif sur
-- champ vide, insensible casse/espaces). Le total (avant pagination)
-- est renvoyé via count(*) over(), calculé sur l'ensemble filtré
-- avant que limit/offset ne s'appliquent.
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
    and (p_pays is null or bp.pays is null or lower(trim(bp.pays)) = lower(trim(p_pays)))
    and (p_niveau is null or bp.niveau is null or lower(trim(bp.niveau)) = lower(trim(p_niveau)))
    and (p_categorie is null or bp.categorie is null or lower(trim(bp.categorie)) = lower(trim(p_categorie)))
    and (p_classe is null or bp.classe is null or lower(trim(bp.classe)) = lower(trim(p_classe)))
    and (p_specialite is null or bp.specialite is null or lower(trim(bp.specialite)) = lower(trim(p_specialite)))
  order by bp.created_at desc
  limit p_limite offset p_decalage;
$$;
