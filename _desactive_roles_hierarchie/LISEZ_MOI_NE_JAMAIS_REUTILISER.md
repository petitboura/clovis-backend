# Ne jamais réutiliser -- zone désactivée

Déplacés ici le 06/09/2026 pendant la Partie 9 du chantier confiance
pédagogique (nouveau système établissement, voir `core/etablissements.py`
et `api/etablissements.py`).

## Contenu

- `api/roles.py` -- ancienne hiérarchie de rôles fixe (établissement /
  enseignant / étudiant), migration `2026_08_04_roles_hierarchie.sql`.
  Aucun `app.include_router(...)` ne le branchait déjà dans `api/main.py`
  avant ce déplacement -- aucun endpoint n'était joignable.
- `api/invitations_clovis.py` -- variante par code à partager du même
  système de rattachement fixe. Même constat : non branché.

## Pourquoi déplacés plutôt que supprimés

Racine du problème : `profiles.role` / `etablissement_id` / `enseignant_id`
sont des colonnes fixes, choisies une fois, non modifiables -- incompatible
avec le nouveau modèle de rattachements multiples et modifiables (suivre /
se connecter / accepter) demandé par Bourama pour le Point 6 du document de
vision confiance pédagogique. Remodeler `roles.py` pour ce nouveau modèle
aurait cassé ce schéma fixe ; d'où la décision de repartir sur des tables
neuves entièrement séparées (`etablissements`,
`etablissements_rattachements`, `etablissements_publications`) plutôt que
de réutiliser ou remodeler ce code.

Gardés sur disque (pas supprimés) au cas où Bourama veut relire l'ancienne
logique -- mais ne jamais les rebrancher ni copier leur schéma sans lui
redemander explicitement.

## Ce qui a été extrait AVANT le déplacement (important)

`api/roles.py` contenait aussi `resoudre_destinataire_autorise` et
`_inserer_message`, activement utilisées par l'outil IA `envoyer_message`
(messagerie directe basée sur la hiérarchie, via
`core/outils_memoire_profil.py` -> `core/serveur_mcp_generation.py`). Ces
fonctions (et leurs dépendances `_contacts_autorises`,
`_peut_echanger_messages`, `_profils_par_colonne`,
`_etablissement_de_etudiant`, `_nom_affiche_ou_repli`) ont été extraites
tel quel vers `core/messagerie_directe.py` avant ce déplacement -- aucun
changement de comportement, `envoyer_message` continue de fonctionner à
l'identique. `api/roles.py` importe maintenant ces fonctions depuis
`core/messagerie_directe.py` au lieu de les redéfinir.

`api/permissions_hierarchie.py` (utilisé par `api/agents.py`) reste
également intouché et à sa place normale dans `api/` -- il n'a pas été
déplacé, il reste actif.
