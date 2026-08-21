-- Section "Notion-like" (Partie 2), lot 4/5 -- répétition espacée,
-- 2026-08-20, demande Bourama.
--
-- Table SÉPARÉE plutôt que des colonnes ajoutées à bases_donnees_elements :
-- un élément peut être une tâche (jamais révisée) ou une fiche de
-- révision -- polluer la table générique du lot 3 avec des colonnes
-- spécifiques à l'algorithme aurait été casser sa généricité. Ici, un
-- élément n'a une ligne dans revision_etats QUE s'il a déjà été révisé
-- au moins une fois (créée à la première réponse, voir
-- core/revision_llm.py::enregistrer_reponse).
--
-- Algorithme SM-2 simplifié (Anki-like) : intervalle_jours et
-- facteur_facilite évoluent à chaque réponse, prochaine_revision est
-- recalculée en conséquence -- voir core/revision_llm.py pour le détail
-- du calcul.

create table if not exists revision_etats (
  element_id uuid primary key references bases_donnees_elements(id) on delete cascade,
  prochaine_revision timestamptz not null default now(),
  intervalle_jours int not null default 1,
  facteur_facilite numeric not null default 2.5,
  repetitions int not null default 0,
  derniere_revision timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_revision_etats_prochaine on revision_etats(prochaine_revision);
