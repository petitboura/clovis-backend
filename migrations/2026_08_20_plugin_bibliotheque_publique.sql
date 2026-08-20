-- Plugin public "bibliothèque partagée" (20/08/2026, demande Bourama) :
-- un plugin existant (voir 2026_08_12_plugins_programme.sql) peut être
-- marqué "contribution_libre" -- dans ce cas, N'IMPORTE QUEL utilisateur
-- connecté peut classer un document dans n'importe quel chapitre/matière/
-- programme de la structure du programme source (pas seulement son
-- propriétaire), publié immédiatement, sans modération. La structure
-- elle-même (chapitres/matières) reste figée -- seul le propriétaire du
-- programme peut la modifier, voir core/bibliotheque_programme.py.

alter table plugins_programme
  add column if not exists contribution_libre boolean not null default false;

-- Traçabilité de qui a classé quoi (nécessaire ici : contrairement au cas
-- normal où classeur == propriétaire == évident, un plugin public peut
-- recevoir des documents de n'importe qui -- sert à autoriser un
-- contributeur à retirer SA propre contribution, voir declasser_document).
alter table bibliotheque_emplacements_programme
  add column if not exists ajoute_par uuid references auth.users(id) on delete set null;

create index if not exists idx_plugins_contribution_libre
  on plugins_programme(programme_source_id) where contribution_libre = true;

-- Recherche RAG à travers PLUSIEURS fichiers (potentiellement de
-- plusieurs utilisateurs différents, cas d'un plugin public) -- distincte
-- de recherche_bibliotheque (scopée par un seul p_user_id, voir
-- core/bibliotheque_rag.py:chercher_bibliotheque). Même structure de
-- retour, filtrée par une liste explicite de fichier_id (jamais par
-- user_id ici) pour ne jamais fuiter le contenu d'un fichier qui ne
-- serait pas réellement classé dans ce plugin.
create or replace function recherche_bibliotheque_publique(
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
  type_mime text
)
language sql stable
as $$
  select
    db.contenu,
    1 - (db.embedding <=> query_embedding) as similarite,
    db.fichier_id,
    f.nom_fichier,
    f.url_publique,
    f.type_mime
  from documents_bibliotheque db
  join fichiers_uploades f on f.id = db.fichier_id
  where db.fichier_id = any(p_fichier_ids)
  order by db.embedding <=> query_embedding
  limit match_count;
$$;
