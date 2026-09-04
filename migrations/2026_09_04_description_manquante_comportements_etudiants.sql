-- 04/09/2026, demande Bourama : file d'attente pour générer automatiquement
-- une vraie description quand un skill importé (.md, individuel ou dossier,
-- voir importer_comportement_depuis_skill_md) arrive avec un champ
-- description vide dans son frontmatter -- sans faire attendre l'upload
-- (voir core/file_attente_description_skills.py). Même schéma/conventions
-- que statut_vectorisation sur fichiers_uploades.
ALTER TABLE comportements_etudiants
  ADD COLUMN statut_description text NOT NULL DEFAULT 'pret'
    CHECK (statut_description = ANY (ARRAY['pret', 'en_attente', 'en_cours', 'echec'])),
  ADD COLUMN tentatives_description integer NOT NULL DEFAULT 0,
  ADD COLUMN erreur_description text,
  ADD COLUMN derniere_tentative_description_a timestamp with time zone;
