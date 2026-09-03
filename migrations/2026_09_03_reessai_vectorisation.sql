-- 03/09/2026, demande Bourama : un fichier en échec de vectorisation
-- (PDF cassé, coupure réseau, quota API dépassé...) restait affiché avec
-- un point rouge indéfiniment -- la seule "solution" proposée à
-- l'utilisateur était de supprimer le fichier et de le réajouter à la
-- main. Deux mécanismes ajoutés (voir core/file_attente_vectorisation.py) :
--   1. réessai AUTOMATIQUE en arrière-plan, à froid, après un délai --
--      utile pour une panne passagère (API de vectorisation en rate
--      limit, coupure réseau ponctuelle...).
--   2. bouton "Réessayer" manuel, toujours disponible tant que le
--      fichier est en échec, qui relance immédiatement une nouvelle
--      série de tentatives.
--
-- derniere_tentative_vectorisation_a : horodatage de la dernière tentative
-- (succès ou échec) -- sert de base au délai avant un réessai automatique
-- (voir COOLDOWN_AUTO_REESSAI dans file_attente_vectorisation.py). NULL
-- pour toute ligne jamais encore traitée (déjà "pret" à l'insertion, ou
-- encore "en_attente" pour la toute première fois).
alter table fichiers_uploades add column if not exists derniere_tentative_vectorisation_a timestamptz;
alter table bibliotheque_publique add column if not exists derniere_tentative_vectorisation_a timestamptz;
