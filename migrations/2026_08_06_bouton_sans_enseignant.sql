-- Bouton "Sans enseignant" (06/08/2026, demande Bourama) : jusqu'ici son
-- affichage était câblé en dur sur `contenu_dynamique_par_matiere`
-- (agents type Nitrux uniquement). Bourama veut le rendre pilotable
-- indépendamment de ce flag -- pas encore auto (dépendra plus tard d'une
-- IA "parents" liée + du niveau d'étude), pour l'instant un simple
-- interrupteur qu'on met nous-mêmes en base. Défaut TRUE : "on le laisse
-- dans toute l'IA" pour l'instant, quel que soit l'agent.
alter table agents add column if not exists bouton_sans_enseignant boolean not null default true;
