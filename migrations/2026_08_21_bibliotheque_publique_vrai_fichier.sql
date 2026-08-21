-- 21/08/2026, correction Bourama : la bibliothèque publique doit stocker
-- un VRAI fichier uploadé (nom + description en ACCOMPAGNEMENT du
-- fichier, pas à la place). Ajout des colonnes de stockage, même schéma
-- que fichiers_uploades (voir core/bibliotheque_fichiers.py), sur la
-- table bibliotheque_publique créée le 21/08 (migration précédente
-- 2026_08_21_actif_comportements_publics_bibliotheque_publique.sql).
alter table bibliotheque_publique add column if not exists chemin_stockage text;
alter table bibliotheque_publique add column if not exists url_publique text;
alter table bibliotheque_publique add column if not exists type_mime text;
alter table bibliotheque_publique add column if not exists taille_octets bigint;
