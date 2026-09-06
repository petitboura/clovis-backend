-- 06/09/2026, Partie 3 du chantier "confiance pedagogique" (voir
-- clovis-plan-travail-10-parties.md, Point 1 brique C du document de
-- vision) : regle de comportement de l'IA face a une notion pas encore
-- vue, rattachable a n'importe quel niveau de l'arborescence de la
-- Partie 1 (une notion racine agit comme "chapitre"/"matiere" pour ses
-- descendants, l'arborescence etant libre -- voir
-- core/avancement_notions_ia.py:regle_effective_pour_notion pour la
-- resolution par heritage du parent le plus proche).
--
-- NULL = aucune regle definie a ce niveau precis (regle heritee du
-- parent le plus proche qui en a une, ou aucune regle du tout si aucun
-- ancetre n'en definit une).

alter table notions
  add column if not exists regle_comportement text
  check (regle_comportement is null or regle_comportement in ('bloquer', 'contourner', 'signaler'));
