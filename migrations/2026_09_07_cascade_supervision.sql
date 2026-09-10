-- Partie 10 du chantier "confiance pédagogique" (Point 6, cascade de
-- supervision, 07/09/2026). Construite en dernier, comme prévu par le
-- plan de travail : ne casse rien des Parties 1 à 9, qui tournent déjà
-- sans elle.
--
-- Reprend le champ statut_cascade déjà préparé sur corrections_pedagogiques
-- (Partie 4) sans le remigrer : les valeurs autorisées restent
-- ('en_attente', 'j2_prof', 'j5_etablissement', 'equipe_clovis',
-- 'neutralise', 'resolu'). Ce chantier-ci choisit de n'utiliser
-- QUE 'en_attente' / 'j2_prof' / 'j5_etablissement' / 'equipe_clovis' /
-- 'resolu' comme étapes de la progression temporelle (voir
-- core/cascade_supervision.py pour le détail), et laisse 'neutralise'
-- de côté pour cette progression : la neutralisation de la règle
-- contestée est un fait immédiat et indépendant du calendrier J2/J5
-- (règle explicite de la vision), pas une étape de plus dans la même
-- file - elle est donc suivie par des colonnes dédiées ci-dessous
-- plutôt que par cette valeur d'enum, pour ne jamais mélanger "où en
-- est la notification humaine" et "la règle a-t-elle été neutralisée".
-- Point à valider avec Bourama si cette lecture ne correspond pas à
-- l'intention d'origine du champ.

-- Une cascade = un groupe de signalements de type B accumulés sur le
-- même (prof_id, agent_id). Table séparée de corrections_pedagogiques
-- (règle transversale du plan de travail : chaque brique dans son
-- propre fichier/table neuve) plutôt qu'un ajout de colonnes
-- supplémentaires sur une table déjà volumineuse.
create table if not exists cascades_supervision (
  id uuid primary key default gen_random_uuid(),
  prof_id uuid not null references auth.users(id) on delete cascade,
  agent_id text not null references agents(id) on delete cascade,

  statut text not null default 'j2_prof' check (
    statut in ('j2_prof', 'j5_etablissement', 'equipe_clovis', 'resolu')
  ),

  seuil_declenchement int not null default 3,
  compteur_signalements int not null default 0,

  declenchee_le timestamptz not null default now(),
  j2_le timestamptz not null default (now() + interval '2 days'),
  j5_le timestamptz not null default (now() + interval '5 days'),

  /* Renseigné seulement si un rattachement établissement accepté
     commun (prof + au moins un des élèves signalants) a été trouvé au
     moment du passage à J2, voir le tableau à trois états du Point 6.
     Si null à J5, l'étape établissement est simplement sautée (le
     délai est quand même respecté, règle explicite de la vision). */
  etablissement_id uuid references etablissements(id) on delete set null,

  /* Neutralisation automatique et immédiate (indépendante de statut
     ci-dessus, voir note en tête de fichier). comportement_id nullable :
     la détection par LLM (core/cascade_supervision.py) peut échouer à
     identifier une règle candidate avec assez de confiance, auquel cas
     la cascade suit son cours sans neutralisation automatique plutôt
     que de désactiver au hasard un comportement du prof. */
  comportement_neutralise_id uuid references comportements_etudiants(id) on delete set null,
  neutralisee_le timestamptz,

  resolue_le timestamptz,
  resolue_par uuid references auth.users(id) on delete set null,
  note_resolution text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_cascades_supervision_prof on cascades_supervision(prof_id, statut);
create index if not exists idx_cascades_supervision_etablissement on cascades_supervision(etablissement_id, statut);
create index if not exists idx_cascades_supervision_progression on cascades_supervision(statut, j2_le, j5_le) where statut <> 'resolu';

comment on table cascades_supervision is
  'Partie 10 (cascade de supervision, Point 6) : accumulation détectée de signalements pédagogiques de type B sur un même (prof_id, agent_id), progression temporelle J2 (prof) -> J5 (établissement si applicable, sinon direct) -> équipe Clovis, neutralisation immédiate et automatique de la règle candidate suivie séparément du statut.';

-- Rattache chaque signalement de type B individuel à la cascade dont il
-- fait partie, une fois le seuil d'accumulation atteint (null tant que
-- le signalement est isolé, en dessous du seuil - reste alors en
-- statut_cascade='en_attente', déjà la valeur par défaut existante,
-- aucune migration de donnée nécessaire pour les lignes déjà en base).
alter table corrections_pedagogiques
  add column if not exists cascade_id uuid references cascades_supervision(id) on delete set null;

create index if not exists idx_corrections_pedagogiques_cascade_id on corrections_pedagogiques(cascade_id);
