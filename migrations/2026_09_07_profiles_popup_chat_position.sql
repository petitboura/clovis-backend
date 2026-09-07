-- 07/09/2026, demande Bourama : le popup mini du chat (desktop) doit
-- être déplaçable/redimensionnable et retrouver sa taille/position sur
-- n'importe quel appareil où le compte se connecte (voir api/profiles.py
-- MettreAJourProfilPayload/ProfilPublic, et components/chat/ChatFlottant.tsx
-- côté frontend). Même convention "privée" que notifications_proactives_actives/
-- premier_agent_id/est_createur/est_majeur sur cette même table : ne
-- vaut la vraie valeur que pour le propriétaire du profil (voir
-- obtenir_profil_public), jamais exposé sur un profil public.
--
-- NULL = jamais personnalisé par ce compte -- le frontend retombe alors
-- sur la taille/position par défaut (centrée), voir ChatFlottant.tsx.

ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS popup_chat_x integer,
    ADD COLUMN IF NOT EXISTS popup_chat_y integer,
    ADD COLUMN IF NOT EXISTS popup_chat_largeur integer,
    ADD COLUMN IF NOT EXISTS popup_chat_hauteur integer;
