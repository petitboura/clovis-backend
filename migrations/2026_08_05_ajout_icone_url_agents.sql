-- Nouveau système d'icône (2026-08-05, demande Bourama) : remplace à
-- terme l'emoji (ui_config.icone_page) et la grande bannière
-- (image_vitrine_url) partout où l'agent est affiché. Une icône compacte
-- par agent, soit dessinée à la main soit une image uploadée -- même
-- mécanisme d'upload que image_vitrine_url (POST /api/uploads/image),
-- juste une colonne différente. NULL = pas encore migré, fallback sur
-- une icône générique unique côté frontend (jamais l'emoji).
--
-- Appliquée directement via Supabase MCP le 2026-08-05, ce fichier n'est
-- qu'une trace versionnée (même convention que les migrations précédentes).

alter table public.agents add column if not exists icone_url text;
comment on column public.agents.icone_url is
  'Icône compacte de l''agent (2026-08-05) : dessinée à la main ou uploadée, remplace icone_page (emoji) et image_vitrine_url (bannière) dans tout l''affichage. NULL = icône générique par défaut le temps de la migration.';
