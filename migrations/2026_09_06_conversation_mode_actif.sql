-- Partie 6 (06/09/2026, demande Bourama) : mode actif par conversation.
-- Un utilisateur peut avoir plusieurs codes rattachés en même temps ; cette
-- table retient lequel s'applique à la conversation en cours.
-- Voir api/mode_actif_conversation.py et lib/api.ts (obtenirModeActif /
-- definirModeActif) côté application.
--
-- Note : cette migration documente a posteriori un état déjà appliqué en
-- base (écart de process constaté le 07/09/2026), pas un changement de
-- schéma à exécuter.

CREATE TABLE IF NOT EXISTS public.conversation_mode_actif (
    conversation_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    rattachement_id uuid REFERENCES public.rattachements_codes(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_mode_actif_user
    ON public.conversation_mode_actif USING btree (user_id);

ALTER TABLE public.conversation_mode_actif ENABLE ROW LEVEL SECURITY;
-- Aucune policy définie : accès exclusivement via la clé service_role
-- (backend), même logique que le reste des tables internes du projet.
