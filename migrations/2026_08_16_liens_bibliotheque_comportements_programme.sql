-- Lie la bibliotheque personnelle et les comportements aux emplacements
-- du programme (16/08/2026, demande explicite de Bourama, option B
-- confirmee : "les documents dans un programme n'importe ou dans le
-- programme seront dans la bibliotheque avec un libelle", plusieurs
-- emplacements possibles par document).
--
-- Remplace, POUR LES NOUVEAUX AJOUTS, l'ancienne table documents_programme
-- (titre + url_ou_contenu, rattachee uniquement a un chapitre, voir
-- 2026_08_12_contenu_pratique_programme.sql) : un document ajoute
-- maintenant n'importe ou dans le programme est une vraie entree
-- fichiers_uploades (bibliotheque), taguee avec un ou plusieurs
-- emplacements via la table ci-dessous. documents_programme et son API
-- existante sont laisses TELS QUELS (pas supprimes, pas migres) --
-- coexistence assumee le temps que le frontend bascule dessus, decision
-- explicitement pas prise ici (pas demandee).
--
-- Meme reference polymorphe deja utilisee pour classement_transversal_items
-- (voir 2026_08_12_contenu_pratique_programme.sql) : pas de vraie FK SQL
-- possible sur plusieurs tables a la fois, verification de propriete faite
-- cote code (core/bibliotheque_programme.py), pas en base.

create table if not exists bibliotheque_emplacements_programme (
  id uuid primary key default gen_random_uuid(),
  fichier_id uuid not null references fichiers_uploades(id) on delete cascade,
  type_cible text not null check (type_cible in ('programme', 'matiere', 'chapitre')),
  cible_id uuid not null,
  created_at timestamptz not null default now(),
  unique (fichier_id, type_cible, cible_id)
);
create index if not exists idx_bib_emplacements_fichier on bibliotheque_emplacements_programme(fichier_id);
create index if not exists idx_bib_emplacements_cible on bibliotheque_emplacements_programme(type_cible, cible_id);

-- Comportements lies a un emplacement du programme (facultatif -- NULL
-- pour un comportement generique comme avant, comportement inchange pour
-- tout ce qui existe deja). Type_cible plus large que ci-dessus (englobe
-- aussi document/exercice/examen, comme classement_transversal_items,
-- puisque Bourama a dit "un programme, un chapitre, ou quoi que ce soit
-- dans le programme").
alter table comportements_etudiants add column if not exists lien_type text
  check (lien_type in ('programme', 'matiere', 'chapitre', 'document', 'exercice', 'examen'));
alter table comportements_etudiants add column if not exists lien_id uuid;
create index if not exists idx_comportements_lien on comportements_etudiants(lien_type, lien_id);
