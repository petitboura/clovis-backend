-- Partie 4 du chantier "confiance pédagogique" (Point 2, 06/09/2026) :
-- signalement enrichi élève -> correction prof -> règle généralisée.
--

-- Système ENTIÈREMENT SÉPARÉ du like/dislike existant (api/feedback.py,
-- table feedback_messages), décision actée par Bourama, aucune
-- extension du mécanisme existant. Nom volontairement différent de
-- "signalements" (déjà pris par api/signalements.py, droit d'auteur
-- bibliothèque publique, aucun rapport) pour ne jamais confondre les
-- deux dans le code ou les migrations futures.
--

-- Deux entrées distinctes DÈS LA CRÉATION (jamais de reclassement a
-- posteriori, décision actée) :
--   type "A" : correctif de fond sur une notion/méthode, traité par
--     le prof avec son expertise matière (flux de correction ci-dessous).
--   type "B" : comportement général mal configuré, destiné dès
--     l'origine à la cascade de supervision (Partie 10, pas encore
--     construite). statut_cascade préparé dès maintenant pour ne
--     jamais avoir à remigrer le schéma plus tard (règle transversale
--     du plan de travail sur le Point 6).
--

-- prof_id : résolu à la création via rattachements_codes (voir
-- core/corrections_pedagogiques.py::resoudre_prof_actif), à ce stade
-- du produit, un élève n'a jamais plus d'un prof actif en pratique,
-- donc pas d'ambiguïté à gérer ici (confirmé par Bourama). Nullable :
-- un élève sans aucun prof rattaché peut quand même signaler (ex. IA
-- "sans enseignant"), le signalement reste alors sans destinataire
-- tant qu'aucun prof n'est rattaché, mais la donnée n'est jamais perdue.

create table if not exists corrections_pedagogiques (
  id uuid primary key default gen_random_uuid(),
  agent_id text not null references agents(id) on delete cascade,
  etudiant_id uuid not null references auth.users(id) on delete cascade,
  prof_id uuid references auth.users(id) on delete set null,
  type text not null check (type in ('A', 'B')),

  conversation_id uuid,
  question_message_id bigint,
  reponse_message_id bigint,
  question_texte text not null default '',
  reponse_texte text not null default '',
  contexte_conversation jsonb not null default '[]'::jsonb,

  -- Type A uniquement : flux de correction par le prof.
  statut text not null default 'nouveau' check (statut in ('nouveau', 'traite')),
  correction_texte text,
  comportement_id uuid references comportements_etudiants(id) on delete set null,
  -- Partie 1 (structure de notions) pas encore construite, rempli
  -- plus tard sans jamais recréer cette colonne (dépendance optionnelle
  -- et non bloquante, voir plan de travail Partie 4).
  notion_id uuid,

  -- Type B uniquement : file de la Partie 10 (cascade de supervision),
  -- non traitée ici, ce chantier-ci ne fait que préparer le champ.
  statut_cascade text not null default 'en_attente' check (
    statut_cascade in ('en_attente', 'j2_prof', 'j5_etablissement', 'equipe_clovis', 'neutralise', 'resolu')
  ),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_corrections_pedagogiques_prof on corrections_pedagogiques(prof_id, type, statut);
create index if not exists idx_corrections_pedagogiques_etudiant on corrections_pedagogiques(etudiant_id);
create index if not exists idx_corrections_pedagogiques_cascade on corrections_pedagogiques(statut_cascade) where type = 'B';

comment on table corrections_pedagogiques is
  'Signalements élève->prof (Point 2, Partie 4), type A (correction de fond, traitée par le prof, généralisée en comportement) et type B (comportement mal configuré, destiné à la cascade de supervision Partie 10). Totalement séparé du like/dislike existant (feedback_messages).';
