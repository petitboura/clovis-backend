-- Extension de bibliotheque_emplacements_programme aux exercices et
-- examens (17/08/2026, demande Bourama : "il faut meme en ajouter si ca
-- ne suffit pas" -- les pieces jointes ne doivent pas se limiter au
-- squelette programme/matiere/chapitre, un exercice ou un examen
-- doivent pouvoir recevoir un document depuis la bibliotheque au meme
-- titre. Voir 2026_08_16_liens_bibliotheque_comportements_programme.sql
-- pour la table d'origine.

alter table bibliotheque_emplacements_programme
  drop constraint bibliotheque_emplacements_programme_type_cible_check;

alter table bibliotheque_emplacements_programme
  add constraint bibliotheque_emplacements_programme_type_cible_check
  check (type_cible in ('programme', 'matiere', 'chapitre', 'exercice', 'examen'));

-- Enonce d'un exercice desormais optionnel : une piece jointe (photo/PDF
-- classee via bibliotheque_emplacements_programme) peut remplacer
-- entierement le texte tape (17/08, demande Bourama : "les deux
-- cohabitent, fichier OU texte au choix").
alter table exercices_programme alter column enonce drop not null;
alter table exercices_programme alter column enonce set default '';
