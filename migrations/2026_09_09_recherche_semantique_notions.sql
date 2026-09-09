-- 09/09/2026, demande Bourama : recherche semantique du programme (bugs
-- 4, 5, 6 et 7 de la consultation cote eleve, remontes le 08/09/2026 :
-- matching par egalite stricte sur le nom, silence total en cas
-- d'echec, jamais oblige). Remplace le matching texte de
-- core/avancement_notions_ia.py par une recherche vectorielle, comme le
-- reste du RAG de la plateforme (voir core/embeddings.py,
-- gemini-embedding-001, 768 dimensions).
--
-- Un seul vecteur par notion (nom + consigne_llm si definie, voir
-- core/programme_notions.py, revectorisation synchrone a chaque
-- creation/modification) : les notions sont courtes, pas de decoupage
-- en chunks necessaire contrairement aux documents.
--
-- NULL tant qu'une notion n'a jamais ete (re)vectorisee avec succes
-- (ex : Gemini en pause quota au moment de la creation/modification),
-- filtree explicitement dans la RPC ci-dessous (n.embedding is not
-- null), ces lignes sont donc exclues des resultats jusqu'a la
-- prochaine modification qui reessaiera.
--
-- Applique directement via Supabase MCP (apply_migration), ce fichier
-- ne fait que documenter le changement dans le repo.
alter table notions add column if not exists embedding vector(768);

create or replace function recherche_notions(
  query_embedding vector(768),
  match_count int,
  p_code_id uuid,
  p_seuil_similarite float default 0.5
)
returns table (
  id uuid, nom text, statut text, notion_parent_id uuid,
  regle_comportement text, consigne_llm text, similarite float
)
language sql stable
as $$
  select
    n.id, n.nom, n.statut, n.notion_parent_id,
    n.regle_comportement, n.consigne_llm,
    1 - (n.embedding <=> query_embedding) as similarite
  from notions n
  where n.code_id = p_code_id
    and n.embedding is not null
    and (1 - (n.embedding <=> query_embedding)) >= p_seuil_similarite
  order by n.embedding <=> query_embedding
  limit match_count;
$$;
