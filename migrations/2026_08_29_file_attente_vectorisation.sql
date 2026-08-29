-- 29/08/2026, demande Bourama : la vectorisation à l'ajout d'un fichier
-- (bibliothèque privée ET publique) était synchrone et bloquante -- un
-- gros PDF ou un upload en masse faisait attendre la requête HTTP tout
-- le temps que dure l'extraction + les appels d'embedding Gemini pour
-- chaque chunk. Le fichier est maintenant stocké et rendu disponible
-- immédiatement ; la vectorisation elle-même part dans une vraie file
-- d'attente traitée en arrière-plan (voir core/file_attente_vectorisation.py).
--
-- statut_vectorisation :
--   'en_attente' -- pas encore traité, dans la file
--   'en_cours'   -- en cours de traitement par le worker
--   'pret'       -- vectorisé avec succès, OU type de fichier qui n'a de
--                   toute façon jamais besoin d'être vectorisé (lien,
--                   vidéo...) -- mis à 'pret' dès l'insertion dans ce cas
--   'echec'      -- a échoué après MAX_TENTATIVES tentatives
--
-- Défaut 'pret' pour les lignes déjà en base : soit déjà vectorisées par
-- l'ancien mécanisme synchrone (avant ce changement), soit d'un type
-- jamais vectorisé -- aucune n'a besoin de repasser par la file.
alter table fichiers_uploades add column if not exists statut_vectorisation text not null default 'pret';
alter table fichiers_uploades add column if not exists tentatives_vectorisation int not null default 0;
alter table fichiers_uploades add column if not exists erreur_vectorisation text;
create index if not exists idx_fichiers_uploades_statut_vectorisation
  on fichiers_uploades (statut_vectorisation)
  where statut_vectorisation in ('en_attente', 'en_cours');

alter table bibliotheque_publique add column if not exists statut_vectorisation text not null default 'pret';
alter table bibliotheque_publique add column if not exists tentatives_vectorisation int not null default 0;
alter table bibliotheque_publique add column if not exists erreur_vectorisation text;
create index if not exists idx_bibliotheque_publique_statut_vectorisation
  on bibliotheque_publique (statut_vectorisation)
  where statut_vectorisation in ('en_attente', 'en_cours');
