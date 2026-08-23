-- Lot 5 Partie 3 (app mobile), 23/08/2026.
-- Permet a demarrer_connexion_notion de stocker le redirect_uri utilise
-- (web ou mobile) pour que finaliser_connexion_notion echange le code avec
-- exactement la meme valeur, comme l'exige Notion.
-- Deja appliquee en production via Supabase MCP le 23/08/2026.

alter table notion_oauth_temp add column redirect_uri text;
