# ⚠️ SECTION DÉSACTIVÉE — À NE JAMAIS JAMAIS RÉUTILISER

Décision de Bourama (29/08/2026) : la fonctionnalité **"Programme"**
(structure classe/matière/chapitre, documents/exercices/examens rattachés,
plugins de programme, audits, classement de la bibliothèque dans le
programme) est **complètement désactivée et isolée**.

## Ce qui a été fait

- Les 9 fichiers qui appartenaient EXCLUSIVEMENT à cette fonctionnalité ont
  été déplacés ici, tels quels, sans aucune modification :
  - `api/programmes.py`
  - `api/plugins_programme.py`
  - `api/contenu_programme.py`
  - `api/emplacements_bibliotheque_programme.py`
  - `api/audits_programme.py`
  - `core/programme_llm.py`
  - `core/programme_ecriture.py`
  - `core/audit_programme.py`
  - `core/bibliotheque_programme.py`
- Tous les imports, enregistrements de routes FastAPI, outils MCP,
  injections dans le system prompt du chat, et entrées de registre qui
  branchaient ces fichiers au reste de l'application ont été retirés du
  code actif (voir les commits associés).
- Rien n'a été supprimé en base de données : les tables `programmes`,
  `matieres`, `chapitres`, `documents_programme`, `exercices_programme`,
  `examens_programme`, `examen_chapitres`, `plugins_programme`,
  `plugin_telechargements`, `audits_programme`, `audits_matiere`,
  `audits_chapitre`, `historique_ecritures_programme`,
  `bibliotheque_emplacements_programme`, `classements_transversaux`,
  `classement_transversal_items` existent toujours mais ne sont plus
  appelées par aucun code actif.

## Règle absolue pour la suite

- **NE JAMAIS RÉUTILISER** ce dossier, en totalité ou en partie, dans
  quelque fonctionnalité future que ce soit — même si un besoin similaire
  se présente, même "juste pour s'inspirer".
- **NE JAMAIS RE-BRANCHER** un de ces fichiers (import, route, outil MCP)
  au reste de l'application.
- **IGNORER COMPLÈTEMENT** ce dossier lors de toute exploration du dépôt,
  toute recherche de code existant, ou toute suggestion d'architecture.
  Il ne fait plus partie du produit.

Si un futur chantier a besoin d'une structure "programme"/classe/matière/
chapitre, il doit être reconstruit entièrement à neuf, sans réutiliser ce
code — en redemandant explicitement à Bourama la spécification voulue.
