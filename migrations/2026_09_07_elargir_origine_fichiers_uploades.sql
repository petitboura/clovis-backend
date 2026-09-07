-- Corrige un ecart entre le code et la base constate le 07/09/2026 (bug
-- remonte par Bourama : partage de code, aucun fichier ne propage jamais
-- vers les receveurs, seuls les liens passent).
--
-- Le commit 1bfab6e ("Bibliotheque: vraies origines distinctes") a
-- introduit les origines "publique", "code_partage" et "ia_generee" cote
-- code (core/bibliotheque_fichiers.py, core/codes_partage.py) sans jamais
-- ecrire la migration correspondante -- la contrainte en base est restee
-- bloquee sur ('chat', 'bibliotheque') uniquement. Consequence en prod :
-- chaque insertion avec une de ces 3 nouvelles origines echouait avec
-- "violates check constraint fichiers_uploades_origine_check", capturee
-- silencieusement par les try/except de core/codes_partage.py.

ALTER TABLE fichiers_uploades DROP CONSTRAINT fichiers_uploades_origine_check;
ALTER TABLE fichiers_uploades ADD CONSTRAINT fichiers_uploades_origine_check
  CHECK (origine = ANY (ARRAY['chat'::text, 'bibliotheque'::text, 'publique'::text, 'code_partage'::text, 'ia_generee'::text]));
