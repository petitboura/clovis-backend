# clovis-backend — Clovis

Backend FastAPI de **Clovis**, IA autonome pour établissements scolaires
(élève arrive directement sur un chat, aucune landing marketing, aucune
mention d'"établissement"/"école"). Dépôt séparé de `djiguigne-backend`
(dont il a hérité une partie de la structure de code initiale) et de son
frontend `classgpt-frontend` — isolation totale, pas une variante d'un
produit plus grand : le lien avec l'écosystème Djiguignè ne doit jamais
transparaître pour l'utilisateur final.

**Ce README décrit l'état réel du code.** En cas de doute, le code fait
foi — pas d'anciennes conversations ou de documentation externe.

---

## Structure du dépôt

```
core/
  main.py                 chat() — cascade Groq → Gemini → Groq de secours, assemblage du prompt, outils
  mcp_tools.py             moteur MCP générique (catalogue d'outils en cache 24h, appel d'outils externes)
  registre_outils.py       liste des serveurs MCP actifs (seul fichier à modifier pour en ajouter un)
  configuration.py         system prompt central de Clovis, cache 5 min
  proactivite.py           planificateur de relance des utilisateurs inactifs (boucle en tâche de fond, voir api/main.py)
  comportements_etudiants.py  système de "comportements" : routeur léger (llama-3.1-8b-instant) qui sélectionne les
                               candidats présentés au grand modèle, qui décide lui-même de lire le texte complet
  programme_llm.py         logique du programme d'études adaptatif
  bibliotheque_rag.py       bibliothèque personnelle de l'utilisateur (RAG), scopée par user_id
  retriever.py              recherche vectorielle parallèle (prompts, documents)
  embeddings.py             vectorisation partagée (gemini-embedding-001)
  generation_*.py           outils de génération (images, documents, audio, vidéo, 3D, code, site, LaTeX, signature, archives, données)
  serveur_mcp_generation.py serveur MCP interne exposant les outils de génération ci-dessus
  serveur_mcp_github.py     serveur MCP interne pour le connecteur GitHub
  diagnostic.py             script de diagnostic, teste chaque maillon de la chaîne indépendamment

connexions/
  notion.py                connexion Notion par utilisateur (OAuth 2.1 + PKCE + Dynamic Client Registration)
  oauth_generique.py        connexion générique pour les autres services OAuth

indexers/
  index_notion.py           indexation récursive Notion → Supabase
  index_documents.py        indexation PDF → Supabase (RAG documentaire)
  reembed_gemini.py         ré-indexation vers l'embedding Gemini
  storage.py                 upload/liste/suppression de documents dans Supabase Storage

api/
  main.py                   app FastAPI, montage des routers, boucle de planificateur de proactivité
  auth.py                   vérification du JWT Supabase envoyé par le frontend
  chat.py                   endpoint de chat (streaming)
  agents.py                 configuration/édition de l'agent Clovis (system prompt, documents, bibliothèque,
                             administrateurs, notes, commentaires) — plus de création/suppression/vitrine
                             publique/profil par agent (retirés le 14/08, système multi-agents/multi-créateurs
                             sans usage pour Clovis, une seule IA fixe)
  roles.py                  hiérarchie de rôles (nous/établissement/enseignant/étudiant), rattachement à l'inscription
  permissions_hierarchie.py point de vérité "qui a le droit de toucher à l'agent de qui", réutilisé par agents.py
  invitations_clovis.py     invitation d'autres personnes par message/code
  comportements_etudiants.py endpoints du système de comportements (voir core/comportements_etudiants.py)
  programmes.py, contenu_programme.py, plugins_programme.py, contenu_dynamique_matiere.py, audits_programme.py
                             programme d'études adaptatif
  bibliotheque_utilisateur.py endpoints bibliothèque personnelle (niveau="utilisateur", pas d'agent_id)
  historique.py, memoire.py  historique de conversation, mémoire
  profiles.py                profil utilisateur (+ suppression de compte)
  notifications_push.py      notifications push
  feedback.py, uploads.py, journal.py  divers

migrations/                 schéma SQL, un fichier par changement, daté
scripts/                    scripts ponctuels (ex: génération de clés VAPID)
```

## Ce qui tourne en production

- Backend FastAPI (`api/main.py`), déployé sur Railway.
- Frontend séparé : `classgpt-frontend` (Next.js, sur Vercel).
- Une seule IA (Clovis) — pas de multi-agents, pas de marketplace de
  créateurs. Certaines tables (`agents`, `agents_administrateurs`...)
  et endpoints de `api/agents.py` restent nommés "agent" par héritage du
  code d'origine, mais ne servent qu'à Clovis lui-même.

## Variables d'environnement / secrets nécessaires

| Variable | Utilisée par |
|---|---|
| `SUPABASE_URL` | tout le projet |
| `SUPABASE_SECRET` | tout le projet |
| `GROQ_API_KEY` | `core/main.py` (LLM principal + secours) |
| `GOOGLE_API_KEY` | `core/embeddings.py`, `core/main.py` (secours Gemini) |
| `NOTION_TOKEN` | `indexers/index_notion.py`, `connexions/notion.py` |
| `TAVILY_API_KEY` | `core/registre_outils.py` (outil de recherche web) |
| `GITHUB_TOKEN` (optionnel) | `core/serveur_mcp_github.py` (connecteur GitHub, lecture publique) -- sans lui, l'API GitHub non authentifiée est plafonnée à 60 requêtes/heure PAR IP, partagées entre tous les utilisateurs ; un classic token `public_repo` (lecture seule, repos publics) fait passer la limite à 5000/heure |
| `URL_RETOUR_APP` | URL publique du déploiement, utilisée pour le retour OAuth Notion — à recalculer à chaque changement de domaine, pas à copier telle quelle d'un environnement à l'autre |

Détail complet et checklist Railway : voir `RAILWAY_DEPLOY.md`.

## Lancer l'app

```
uvicorn api.main:app --reload
```

## Indexer un nouveau document PDF

```
python indexers/index_documents.py mon_document.pdf
```

(le fichier doit déjà être présent dans le bucket Supabase Storage configuré)
