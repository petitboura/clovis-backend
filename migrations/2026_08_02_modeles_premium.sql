-- Migration : modeles premium (Claude / GPT / Gemini / DeepSeek)
-- Voir core/fournisseurs_llm.py et api/agents.py pour le contexte.
-- A executer manuellement (Supabase SQL editor ou MCP) -- staging
-- d'abord (nzgtgtghhnvrokqvagst) puis prod (rwcyeppxfonvqbvztxyg) une
-- fois valide, sauf si Bourama prefere l'inverse.

-- 1. Abonnement premium par agent (v1 : rempli a la main par Bourama,
--    pas de systeme de paiement pour l'instant -- voir page Notion
--    "Pricing -- Agent Maths").
alter table agents
    add column if not exists distributeur_debloque text
        check (distributeur_debloque in ('aucun', 'deepseek', 'gemini', 'gpt', 'claude')),
    add column if not exists palier_debloque text
        check (palier_debloque in ('essentiel', 'avance', 'pro')),
    add column if not exists modele_choisi text;

comment on column agents.distributeur_debloque is
    'Fournisseur premium debloque pour cet agent (hierarchie cumulative claude > gpt > gemini > deepseek). NULL/aucun = agent sur la cascade Groq par defaut, aucun selecteur affiche.';
comment on column agents.palier_debloque is
    'Qualite de modele debloquee (essentiel/avance/pro, cumulatif ascendant). Ignore si distributeur_debloque est NULL/aucun.';
comment on column agents.modele_choisi is
    'modele_id par defaut choisi par le createur parmi les modeles debloques (voir core/fournisseurs_llm.py:modeles_disponibles_pour_agent). NULL = pas de preference, cascade Groq par defaut tant qu''aucun modele n''est force par message.';

-- 2. Tracabilite : quel modele a genere chaque reponse assistant.
alter table historique_conversations
    add column if not exists modele text;

comment on column historique_conversations.modele is
    'modele_id qui a genere cette reponse (ex: "claude-sonnet-5", "openai/gpt-oss-120b", "gemini-2.5-flash"). NULL pour les lignes anterieures a cette migration -- pas de retro-remplissage, comportement d''affichage inchange pour l''historique existant.';
