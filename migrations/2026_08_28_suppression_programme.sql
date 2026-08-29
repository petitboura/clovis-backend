-- 28/08/2026, demande Bourama : suppression complète de la fonctionnalité
-- "Programme" (et de tout ce qui en dépendait -- audits, plugins publics
-- de programme, classement de la bibliothèque dans le programme, lien
-- d'un skill vers un emplacement du programme). Voir le nettoyage de code
-- correspondant sur clovis-backend (suppression de 9 fichiers dédiés +
-- édition d'une vingtaine d'autres) et clovis-frontend (déjà fait).
--
-- CASCADE utilisé volontairement : la structure programme est
-- entièrement propriétaire de ses propres tables filles (personne
-- d'autre ne les référence, vérifié dans le code avant cette migration),
-- donc CASCADE ne fait ici que suivre les FK internes à ce même groupe
-- de tables (ex: chapitres -> matieres -> programmes), pas de risque de
-- supprimer une donnée hors périmètre programme.

DROP TABLE IF EXISTS classement_transversal_items CASCADE;
DROP TABLE IF EXISTS classements_transversaux CASCADE;
DROP TABLE IF EXISTS examen_chapitres CASCADE;
DROP TABLE IF EXISTS examens_programme CASCADE;
DROP TABLE IF EXISTS exercices_programme CASCADE;
DROP TABLE IF EXISTS documents_programme CASCADE;
DROP TABLE IF EXISTS audits_chapitre CASCADE;
DROP TABLE IF EXISTS audits_matiere CASCADE;
DROP TABLE IF EXISTS audits_programme CASCADE;
DROP TABLE IF EXISTS historique_ecritures_programme CASCADE;
DROP TABLE IF EXISTS bibliotheque_emplacements_programme CASCADE;
DROP TABLE IF EXISTS plugin_telechargements CASCADE;
DROP TABLE IF EXISTS plugins_programme CASCADE;
DROP TABLE IF EXISTS chapitres CASCADE;
DROP TABLE IF EXISTS matieres CASCADE;
DROP TABLE IF EXISTS programmes CASCADE;

-- comportements_etudiants.lien_type/lien_id/depuis_audit : mécanisme de
-- rattachement d'un skill à un emplacement du programme (7 types
-- possibles, tous des constructions programme -- voir
-- core/bibliotheque_programme.py avant suppression), et marqueur des
-- skills générés par l'ancien système d'audit programme. Plus aucune
-- cible possible une fois la structure programme supprimée ci-dessus.
ALTER TABLE comportements_etudiants DROP COLUMN IF EXISTS lien_type;
ALTER TABLE comportements_etudiants DROP COLUMN IF EXISTS lien_id;
ALTER TABLE comportements_etudiants DROP COLUMN IF EXISTS depuis_audit;

-- signalements_bibliotheque.type_emplacement/emplacement_id : ne
-- servaient qu'au type de signalement "document_programme" (retiré,
-- voir api/signalements.py), pour désigner un emplacement du programme
-- où un document était classé.
ALTER TABLE signalements_bibliotheque DROP COLUMN IF EXISTS type_emplacement;
ALTER TABLE signalements_bibliotheque DROP COLUMN IF EXISTS emplacement_id;

-- codes_partage.programme_id : la table codes_partage elle-même reste
-- (sert aussi pour les comportements/bibliothèque partagés), seule la
-- colonne liée au programme disparaît. Le DROP TABLE programmes
-- ci-dessus aurait de toute façon fait tomber cette colonne via CASCADE
-- si elle avait encore une contrainte FK dessus ; cette ligne couvre le
-- cas où programme_id n'avait pas de FK explicite (juste un uuid libre,
-- comme observé dans core/codes_partage.py avant nettoyage).
ALTER TABLE codes_partage DROP COLUMN IF EXISTS programme_id;

-- pages_carrefour_references : cible polymorphe sans FK (cible_id est un
-- uuid libre, jamais de contrainte vers une table précise -- voir
-- migrations/2026_08_20_pages_carrefour_latex.sql). Les lignes qui
-- pointaient vers programme/matiere/chapitre/document deviennent des
-- références orphelines une fois les tables ci-dessus supprimées : le
-- code (core/pages_notion_llm.py) les ignore déjà silencieusement à
-- l'affichage, mais on les nettoie ici pour ne pas laisser de déchets.
DELETE FROM pages_carrefour_references
WHERE type_cible IN ('programme', 'matiere', 'chapitre', 'document');
