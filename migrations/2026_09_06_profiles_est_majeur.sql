-- Partie 7 (06/09/2026, demande Bourama) : distinction majeur/mineur pour
-- restreindre l'accès (voir core/restriction_mineur.py, api/profiles.py,
-- et lib/api.ts obtenirMonStatut() / enregistrerMonProfil() côté frontend).
--
-- Note : cette migration documente a posteriori un état déjà appliqué en
-- base (écart de process constaté le 07/09/2026), pas un changement de
-- schéma à exécuter.

ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS est_majeur boolean;
