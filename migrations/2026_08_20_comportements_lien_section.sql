-- Extension de comportements_etudiants.lien_type à "section" (20/08/2026,
-- demande Bourama : "un comportement... peut être attaché à un programme,
-- un chapitre, une matière, un examen, une section ou autre"). "section"
-- désigne un classement transversal (semestre/année/section libre, voir
-- classements_transversaux dans 2026_08_12_contenu_pratique_programme.sql)
-- -- pas un nouveau concept, juste une cible de plus pour lien_type/lien_id.
--
-- Contrainte précédente posée par
-- migrations/2026_08_16_liens_bibliotheque_comportements_programme.sql
-- (programme/matiere/chapitre/document/exercice/examen).

alter table comportements_etudiants drop constraint if exists comportements_etudiants_lien_type_check;

alter table comportements_etudiants add constraint comportements_etudiants_lien_type_check
  check (lien_type in ('programme', 'matiere', 'chapitre', 'document', 'exercice', 'examen', 'section'));
