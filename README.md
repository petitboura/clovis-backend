# djiguigne-backend — Djiguignè AI

Plateforme multi-agents : n'importe qui peut créer son propre assistant IA
sans coder (documents PDF, prompt Notion, outils externes) et obtenir un
lien de chat prêt à partager.

Le projet a effectué son **pivot vers une plateforme sociale** (feed de
découverte, pages agent publiques, profils créateurs, notes, commentaires,
follow, notifications, mises à jour d'agent, Article/Réflexion/Histoire —
détail dans la section dédiée plus bas). Voir `api/PLAN.md` pour la
migration Streamlit → API qui sous-tend ce pivot.

**Ce README décrit l'état réel du code.** En cas de doute, le code et
`api/PLAN.md` font foi — pas d'anciennes conversations ou de documentation
externe.

---

## Structure du dépôt

```
core/
  auth.py              authentification étudiant (email/mdp + Google via Supabase Auth), connexion optionnelle
  configuration.py     system prompt central chargé depuis Notion par agent, cache 5 min
  creation_agent.py    logique pure de création d'agent (génération d'id, prompt) — partagée Streamlit/API
  diagnostic.py        script de diagnostic, teste chaque maillon de la chaîne indépendamment
  embeddings.py        vectorisation partagée (gemini-embedding-001)
  main.py              chat() — cascade Groq → Gemini → Groq de secours, assemblage du prompt, outils
  mcp_tools.py         moteur MCP générique (appel d'outils externes)
  registre_outils.py   liste des outils MCP actifs (seul fichier à modifier pour en ajouter un)
  retriever.py         recherche vectorielle parallèle, scopée par agent (prompts, documents)
  themes.py            constantes de thème partagées entre formulaires et rendu

connexions/
  notion.py            connexion Notion par étudiant (OAuth 2.1 + PKCE + Dynamic Client Registration)

faces/
  app_etudiant.py       point d'entrée Streamlit, routage entre les vues ci-dessous
  vues/
    chat.py             interface de chat (étudiant), limite de messages pour visiteur non connecté
    creer_agent.py      formulaire de création d'agent (côté créateur)
    mes_agents.py       liste/édition/désactivation des agents d'un créateur
    recuperation_mdp.py récupération de mot de passe, partagée entre les 3 vues créateur/étudiant
    theme_djiguigne.py  identité visuelle partagée (couleurs, polices, logo)
    vitrine.py          page d'accueil publique côté créateur, aucune logique métier

indexers/
  index_notion.py       indexation récursive Notion → Supabase
  index_documents.py    indexation PDF → Supabase (RAG documentaire)
  reembed_gemini.py     ré-indexation vers l'embedding Gemini
  storage.py            upload/liste/suppression de documents dans Supabase Storage

api/
  main.py               app FastAPI (backend en construction, voir api/PLAN.md)
  auth.py               vérification du JWT Supabase envoyé par le frontend
  agents.py             endpoints agents (création, feed, détail, vitrine, notes, commentaires, suppression)
  agent_updates.py      endpoints mises à jour d'agent (publier, lister, liker, commenter)
  posts.py               endpoints Article/Réflexion/Histoire
  creators.py            endpoints follow/unfollow créateur
  profiles.py            endpoints portfolio créateur (+ suppression de compte)
  notifications.py       endpoints notifications (follow, commentaire, note, mise à jour d'agent)
  search.py              endpoint de recherche (agents + créateurs)
  PLAN.md                suivi détaillé de la migration Streamlit → API
```

## Fonctionnalités sociales (pivot terminé)

- **Feed public** (`/`, `GET /api/feed`) : agents publiés récemment, 5
  onglets (Agents / Créateurs / Article / Réflexion / Histoire).
- **Pages agent publiques** (`/agent/[id]`) : note, commentaires, mises à
  jour (avec like/commentaire/partage), bouton "Utiliser" vers le chat
  Streamlit.
- **Profils créateurs** (`/u/[id]`) : agents publiés, follow, et les 3
  mêmes sections Article/Réflexion/Histoire filtrées sur ce créateur.
- **Mises à jour d'agent** (`api/agent_updates.py`) : un créateur publie
  ce qu'il a changé sur un agent (depuis "Modifier agent") ; toute
  personne ayant déjà utilisé cet agent (même une fois) reçoit une
  notification.
- **Article / Réflexion / Histoire** (`api/posts.py`, table `posts`) :
  3 formats de publication créateur, publiables depuis "Mon espace".
- **Notifications** (`api/notifications.py`) : follow, commentaire, note,
  mise à jour d'agent — lignes créées uniquement par des triggers
  Postgres, jamais insérées directement par l'API.
- **Zone de danger** ("Modifier le profil" côté frontend) : déconnexion,
  suppression de compte / d'un agent / d'une histoire.

## Ce qui tourne en production aujourd'hui

- **L'app Streamlit (`faces/`) est l'unique interface utilisateur actuellement déployée**, hébergée sur Railway.
- **Le chat restera en Streamlit indéfiniment**, même après le pivot social. Seules les autres vues créateur (`creer_agent.py`, `mes_agents.py`, `vitrine.py`) seront progressivement remplacées par un frontend Next.js séparé.
- **`api/` existe dans le dépôt mais n'est pas encore déployé** : Railway fait toujours tourner Streamlit, pas ce backend FastAPI (bascule prévue à l'Étape 6 de `api/PLAN.md`, pas avant que tout soit validé en conditions réelles).

## Variables d'environnement / secrets nécessaires

| Variable | Utilisée par |
|---|---|
| `SUPABASE_URL` | tout le projet |
| `SUPABASE_SECRET` | tout le projet |
| `GROQ_API_KEY` | `core/main.py` (LLM principal + secours) |
| `GOOGLE_API_KEY` | `core/embeddings.py`, `core/main.py` (secours Gemini) |
| `NOTION_TOKEN` | `indexers/index_notion.py`, `connexions/notion.py` |
| `TAVILY_API_KEY` | `core/registre_outils.py` (outil de recherche web) |
| `GITHUB_TOKEN` (optionnel) | `core/main.py` (connecteur GitHub, lecture publique) -- sans lui, l'API GitHub non authentifiée est plafonnée à 60 requêtes/heure PAR IP, partagées entre tous les étudiants ; un classic token `public_repo` (lecture seule, repos publics) fait passer la limite à 5000/heure |
| `URL_RETOUR_APP` | URL publique du déploiement, utilisée pour le retour OAuth Notion — à recalculer à chaque changement de domaine, pas à copier telle quelle d'un environnement à l'autre |

Variables **obsolètes**, ignorées par le code actuel : `OPENROUTER_API_KEY`
(remplacée par `GOOGLE_API_KEY`), `NOTION_PAGE_ID` (remplacée par la colonne
`agents.notion_page_id`, multi-agent). Détail complet et checklist Railway :
voir `RAILWAY_DEPLOY.md`.

## Lancer l'app

```
streamlit run faces/app_etudiant.py
```

Un agent précis se sélectionne via `?agent=<id>` dans l'URL.

## Indexer un nouveau document PDF

```
python indexers/index_documents.py mon_document.pdf
```

(le fichier doit déjà être présent dans le bucket Supabase Storage configuré)

## Pour aller plus loin

- **`api/PLAN.md`** — plan et avancement de la migration Streamlit → API FastAPI
- **`RAILWAY_DEPLOY.md`** — checklist des secrets à configurer sur Railway
