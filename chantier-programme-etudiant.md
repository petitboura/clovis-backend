# Chantier — Nouveau produit étudiant (Clovis)

---

## Partie 1 — Structure du programme (rappel utile pour la partie 2 et 3)

- Hiérarchie : **programme** (par classe/niveau) → **matières** → **chapitres**.
- "Niveau" = simple rattachement organisationnel, pas une logique de droits.
- Contenu pratique rattaché : **documents** et **exercices** (1 seul chapitre), **examens/devoirs** (many-to-many avec les chapitres).
- Classement transversal superposé (semestre, année, section libre), applicable à matière/chapitre/document/exercice/examen.
- But : donner à l'IA un cadre structurant (jamais hors-programme, jamais hors-niveau) pour générer du contenu à la demande.

*(Implémentation déjà en place dans `clovis-backend` : tables `programmes`/`matieres`/`chapitres`, `documents_programme`, `exercices_programme`, `examens_programme` + `examen_chapitres`, `classements_transversaux` + `classement_transversal_items`, système de plugins `plugins_programme`/`plugin_telechargements`. Pour le détail complet — limites de contenu par matière/chapitre, système de plugins et son modèle économique — voir les migrations `2026_08_12_*` et `2026_08_14_plugin_examens_transverses.sql`.)*

---

## Partie 2 — Section "Notion-like" intégrée à l'app

### Principe
- Une section dans l'appli existante (Clovis) — **pas une nouvelle app séparée**.
- Inspirée de Notion, mais **simplifiée** : uniquement les fonctionnalités utiles pour les chercheurs.
- Pas deux publics distincts : les étudiants sont eux-mêmes des chercheurs (démarche de recherche/organisation/synthèse) → un seul outil, pas un espace "chercheurs" séparé d'un espace "étudiants".

### Relation avec le reste de l'appli
- Cette section n'est **pas mélangée/fusionnée** avec le reste de l'appli — elle reste distincte.
- Mais elle est **liée** : une page peut décrire/référencer où se trouve tel ou tel contenu ailleurs dans l'appli (ex. un chapitre, un document), pour tout retrouver depuis une seule page plutôt que naviguer entre différents endroits de stockage.
- But : **centraliser**, servir de point d'accès unique.
- Cette page de liens/références est elle-même une page dans la section Notion-like — indépendante des autres pages de cette section (donc deux types de pages : pages de contenu classique, et pages "carrefour" qui pointent vers d'autres emplacements — typiquement vers la structure programme de la partie 1).

### Recherche — usages réels de Notion par les étudiants
Fonctionnalités les plus utilisées à retenir :
- Une page par matière, avec sous-pages pour cours/devoirs/projets/révisions.
- Bases de données de révision (tableaux/listes) pour suivre la progression, avec plusieurs modes d'affichage (liste, tableau, calendrier, Kanban).
- Support des équations mathématiques et symboles en LaTeX — précieux pour les matières scientifiques.
- Gestion de tâches : classer les devoirs par priorité, sous-tâches par projet.
- Répétition espacée (spaced repetition) pour réviser sur plusieurs semaines — un vrai différenciateur des meilleurs modèles Notion étudiants.
- Collaboration fluide entre camarades, ressources centralisées.

Fonctionnalités à écarter (trop complexes pour l'objectif de simplicité) :
- Bases de données relationnelles avancées, widgets tiers, intégrations externes (Google Calendar, météo, flux Twitter...).
- Marketplace de templates payants (jusqu'à $19 chez Notion) — chez Clovis ce rôle est déjà couvert par le système de plugins (partie 1).

Point clé : Notion donne déjà gratuitement son plan payant aux étudiants (email universitaire) — donc l'avantage différenciant n'est pas le prix, c'est la **simplicité immédiate** (zéro configuration, contrairement à Notion où la configuration relationnelle prend du temps à un utilisateur expérimenté) et le fait que **c'est déjà dans l'écosystème** (pas de compte à créer, connecté nativement à la structure classe/matière/chapitre de la partie 1).

---

## Partie 3 — Agent IA avec contrôle des appareils (version simplifiée)

### Principe général
Fini l'idée initiale de contrôle total du téléphone façon "agent autonome" (accessibilité, clics à l'écran) — trop de risque de blocage par Google Play. Nouvelle approche : uniquement des actions qui passent par des mécanismes officiels et légers, sans jamais toucher à l'API d'accessibilité.

### Ce qu'on ajoute, sans aucun risque

**Suivi et information**
- Voir le temps passé par app (historique d'utilisation).
- Savoir quelle app est active en ce moment (même mécanisme que le temps passé).
- Naviguer, éditer, ajouter, classer et créer des dossiers/fichiers — mais uniquement dans les dossiers que l'étudiant a explicitement désignés une fois (choix persistant, pas à redemander à chaque fois).

**Actions et rappels**
- Envoyer des notifications classiques.
- Ouvrir une app à la demande.
- Notifications en plein écran (jouable, mais à réserver à un usage type "alarme/rappel important" pour rester dans les clous de la review Google).
- Créer des rappels/alarmes via l'app native d'horloge.
- Créer/lire des événements calendrier.
- Activer/désactiver le mode Ne pas déranger pendant une session.
- Ajuster le volume/sonnerie pendant une session.

**Clovis qui agit pour l'étudiant (pas l'inverse)**
- Permettre à Clovis d'utiliser une app tierce via l'API propre de cette app ou un connecteur MCP (comme Notion, Google Calendar, Gmail, Drive...) — même principe que les connecteurs Claude actuels. Ne fonctionne que pour les apps qui exposent une API/MCP publique (donc pas Instagram, TikTok, WhatsApp perso).

### Ce qui reste explicitement écarté (pas dans le scope)
- Fermer une autre app à distance — impossible techniquement, pas juste risqué.
- Bloquer/débloquer l'accès à une app — même limite technique.
- Cliquer/remplir des champs dans d'autres apps — nécessite l'accessibilité, écarté depuis le début.
- Accès à tous les fichiers du téléphone sans désignation explicite — réservé aux gestionnaires de fichiers, refus quasi certain de Google Play.
- Détecter automatiquement un nouveau dossier ajouté ailleurs sur le téléphone (hors des dossiers désignés) — bloqué par le système lui-même, pas de contournement possible sans le même accès large refusé plus haut.

### Mécanique commune à tout ce qui touche aux fichiers/dossiers
L'initiative part toujours de l'étudiant. Clovis peut proposer ("veux-tu me donner accès à un autre dossier ?"), mais c'est toujours l'étudiant qui choisit via le sélecteur système. Une fois donné, l'accès persiste — pas de reconfirmation à chaque fois (même logique que l'appairage entre appareils, façon WhatsApp Web).

---

*Document généré à partir des sessions de réflexion — chantier Clovis, distinct des dépôts Djiguignè.*
