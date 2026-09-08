-- Mode actif applique a la recherche bibliotheque (08/09/2026, demande
-- Bourama) : avant ce fix, recherche_bibliotheque cherchait TOUS les
-- documents de p_user_id sans distinction, y compris ceux recus de
-- plusieurs profs differents (uploade_par = id du prof pour les
-- documents copies via un code, voir core/codes_partage.py::
-- _copier_fichier_pour_receveur -- uploade_par = user_id lui-meme pour
-- un document ajoute directement par l'utilisateur, voir
-- api/uploads.py). Impossible jusqu'ici de scoper par prof actif.
--
-- p_profs_autorises (uuid[], nullable, defaut null) :
--   - null      -> aucun filtre, comportement inchange (retro-compatible)
--   - liste     -> ne renvoie que les documents personnels de p_user_id
--                  (uploade_par = p_user_id) plus ceux des profs listes
--                  (uploade_par = any(p_profs_autorises)) -- une liste
--                  VIDE ne garde donc que les documents personnels.
--
-- Cote appelant (core/bibliotheque_rag.py::chercher_bibliotheque), voir
-- core/codes_partage.py::profs_autorises_recherche_bibliotheque pour la
-- resolution de cette liste a partir du mode actif de la conversation.
--
-- Applique directement via Supabase MCP (apply_migration), ce fichier ne
-- fait que documenter le changement dans le repo, comme les autres
-- migrations trackees ici.

drop function if exists recherche_bibliotheque(vector, integer, uuid, float);
create function recherche_bibliotheque(
  query_embedding vector(768),
  match_count int,
  p_user_id uuid,
  p_seuil_similarite float default 0.5,
  p_profs_autorises uuid[] default null
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
    and (
      p_profs_autorises is null
      or f.uploade_par = p_user_id
      or f.uploade_par = any(p_profs_autorises)
    )
  order by db.embedding <=> query_embedding
  limit match_count;
$$;
