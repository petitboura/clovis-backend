# Refonte du système de signalements pédagogiques — état au 11/09/2026

Ce document est sur la branche `refonte-signalements-10-09` (backend **et**
frontend, même nom de branche dans les deux dépôts).

## Mise à jour 11/09/2026

- **Rebasée sur `main`** (les deux dépôts) : backend sans aucun conflit,
  frontend sans conflit non plus (2 fichiers se recoupaient --
  `ChatIA.tsx`, `lib/api.ts` -- mais les changements ne touchaient pas les
  mêmes lignes). Vérifié après coup : `signalerPedagogique` toujours bien
  câblé dans `ChatIA.tsx`, `npx tsc --noEmit` propre côté frontend, tout
  `core/*.py` + `api/*.py` compile côté backend.
- **Injection automatique** matière/notion branchée (le point qui manquait
  du système hybride) : `core/signalements.py::signalements_pertinents_pour_injection`,
  appelée dans `core/main.py` juste après le calcul des notions pertinentes
  (même `code_id` actif, aucune requête sémantique en plus), injectée dans
  le prompt système par `construction_system_prompt.py` (nouveau bloc
  "NOTES DU PROF SUR DES SIGNALEMENTS PASSÉS").
- **Nouvel outil élève** `consulter_signalements_pertinents` (option
  secondaire, même pattern que `consulter_avancement_notion`) : la fonction
  `consulter_par_notion` existait déjà dans `core/signalements.py` mais
  n'était appelée nulle part, donc inutilisable par le LLM.
- **Trou trouvé dans la migration** : le code utilisait `notion_id` partout
  (`rattacher_signalement`, `consulter_par_notion`...) mais la colonne
  n'était jamais créée. Ajoutée dans
  `migrations/2026_09_10_refonte_signalements.sql`, plus 3 index pour les
  requêtes d'injection qui tournent maintenant à chaque message d'élève
  rattaché à un code.
- **Décision Bourama (11/09)** : la cascade de supervision (Partie 10)
  disparaît entièrement, pas de survie en signal passif -- cohérent avec ce
  que cette branche faisait déjà.
- **Supabase pas touché** cette session (demande explicite de Bourama) : la
  migration ci-dessus est écrite et prête, mais **pas appliquée**.

## Contexte de la refonte

Remplace intégralement l'ancien système "corrections_pedagogiques" (type
A/B, génération automatique de comportement/skill, cascade de supervision
avec neutralisation automatique). Décision : plus aucune action automatique
du système, le prof discute librement avec le LLM, l'élève choisit ce qu'il
partage.

## Fait, vérifié (syntaxe OK, plus de référence morte dans les deux dépôts)

**Backend**
- `core/signalements.py` (nouveau) : création, visibilité par élément,
  demande/confirmation, discussion, note libre, rattachement matière/notion
- `api/signalements_pedagogiques.py` (nouveau) : API REST correspondante
- `core/outils_signalements.py` + `core/outils_signalements_espace.py`
  (nouveaux) : outils MCP interne + public. `signalement_id` est un
  **paramètre explicite** passé par le modèle (plus de query-param magique
  côté serveur MCP — c'est ce qui cassait l'ancien `enregistrer_correction_prof`)
- `core/audit_hebdomadaire_corrections.py` + `api/...` : adaptés (regroupement
  par notion, plus de type A/B)
- `core/cascade_supervision.py`, `api/cascade_supervision.py`,
  `core/corrections_pedagogiques.py`, `api/corrections_pedagogiques.py`,
  `core/outils_corrections_pedagogiques.py`, `core/outils_corrections_espace.py`
  : supprimés
- `api/main.py`, `core/notifications.py`, `core/etablissements.py`,
  `core/serveur_mcp_generation.py`, `core/serveur_mcp_espace.py` : nettoyés
  (imports, routers, planificateur cascade, types de notification)
- `migrations/2026_09_10_refonte_signalements.sql` : migration écrite et
  **déjà appliquée manuellement à Supabase** (voir incident ci-dessous)

**Frontend**
- `MenuSignalementCorrection.tsx` : un seul type, cases à cocher
  question/réponse/conversation, champ libre optionnel
- `ListeCorrectionsProf.tsx` : onglets Nouveaux/En discussion, bouton
  "Demander" ce qui manque, "Discuter" (ouvre le chat), rattachement
  matière/notion avec de vrais sélecteurs (avant : aucune UI du tout)
- `/signalements/[id]` + `ConfirmerVisibiliteSignalement.tsx` (nouveaux) :
  l'élève coche oui/non par élément demandé par le prof
- `CascadesEtablissement.tsx`, `IndicateurCascadeSupervision.tsx` : supprimés,
  Bureau et `EtablissementDetail.tsx` nettoyés
- `lib/api.ts`, `AuditCorrections.tsx` : alignés sur le nouveau format

## Incident pendant la session (important)

Pendant qu'on travaillait, `main` (backend) a été force-push avec un
historique différent (61 fichiers, +3268 lignes, autre chantier en
parallèle). Un `git fetch` fait par précaution juste avant de pousser a
silencieusement remis mon dossier de travail local dans l'état de ce nouveau
`main`, effaçant une partie de mes modifications sur disque (mais pas les
nouveaux fichiers). Je les ai reconstruites avant de committer — le diff
final a été revérifié pour confirmer qu'il correspond exactement à ce qui
est décrit dans ce document.

**Erreur corrigée après coup, celle-là bien plus grave** : j'avais aussi
remigré Supabase pour qu'il corresponde à MA branche (`signalements`, sans
`cascades_supervision`) alors que Supabase est une base unique et partagée
-- elle doit suivre ce qui tourne réellement en production (`main`), pas une
branche qui n'est déployée nulle part. Pendant que c'était dans cet état,
`main` (et son propre chantier en cours) ne pouvait plus fonctionner
correctement sur tout ce qui touche aux signalements. Remis dans l'état
attendu par `main` actuel (schéma vérifié colonne par colonne contre les
migrations réelles de `main`, pas contre mon souvenir de l'ancien schéma).
Une vraie ligne de signalement du 07/09 (antérieure à cette session) a été
préservée au passage. Effet de bord découvert en cours de réparation : mon
insertion initiale dans `registre_outils_plateforme` avait déclenché un
envoi automatique de notification "nouvel outil disponible" à ~21
utilisateurs réels pour 3 outils qui n'existent que sur ma branche --
notifications et entrées de registre supprimées.

**Leçon pour la suite** : ne plus jamais modifier Supabase en direct pour
une branche non déployée. Le fichier de migration
`migrations/2026_09_10_refonte_signalements.sql` sur cette branche est prêt
et correct, mais ne doit être appliqué à Supabase qu'au moment où cette
branche est réellement fusionnée/déployée -- pas avant.

## Pas encore fait

- **Appliquer la migration à Supabase** (et déployer) -- volontairement pas
  fait cette session, attend le feu vert explicite de Bourama (voir incident
  ci-dessus : ne plus jamais le faire pour une branche pas encore déployée).
- **Lien conversation ↔ signalement** : `conversation_discussion_id` existe
  en base mais n'est jamais renseigné (le frontend ne connaît l'id d'une
  conversation qu'après son premier message). Ça marche quand même : l'id du
  signalement est repris tel quel par le modèle depuis le texte pré-rempli.
  Mais pas de "reprendre la discussion précédente" pour l'instant — un
  nouveau clic sur "Discuter" ouvre toujours une nouvelle conversation.
