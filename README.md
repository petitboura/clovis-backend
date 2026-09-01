# clovis-backend — Clovis

Backend FastAPI de **Clovis**, IA autonome pour établissements scolaires
(élève arrive directement sur un chat, aucune landing marketing, aucune
mention d'"établissement"/"école"). Dépôt séparé de `djiguigne-backend`
(dont il a hérité une partie de la structure de code initiale) et de son
frontend `clovis-frontend` (anciennement `classgpt-frontend`) — isolation
totale, pas une variante d'un produit plus grand : le lien avec
l'écosystème Djiguignè ne doit jamais transparaître pour l'utilisateur
final.

Le backend sert aussi une **app mobile Android/iOS** (Capacitor, dans
`clovis-frontend/android` et `/ios`) et un **serveur MCP public** qui
permet à Claude et à d'autres clients MCP externes de se connecter
directement à l'espace d'un utilisateur.

**Ce README décrit l'état réel du code.** En cas de doute, le code fait
foi — pas d'anciennes conversations ou de documentation externe.

---

## Structure du dépôt

```
core/
  main.py                      chat() — cascade Groq → Gemini → Groq de secours, assemblage du prompt, outils
  mcp_tools.py                 moteur MCP générique (catalogue d'outils en cache 24h, appel d'outils externes)
  registre_outils.py           registre des outils (bras) MCP actifs
  configuration.py             system prompt central de Clovis, chargé depuis Notion, cache 5 min
  proactivite.py               relance des utilisateurs inactifs (boucle en tâche de fond, voir api/main.py)
  comportements_etudiants.py   "Mes comportements" (skills côté UI) : routeur léger qui présente les
                                candidats, le grand modèle décide lui-même de lire le texte complet
  contenu_dynamique_matiere.py résolution du system prompt pour les agents à contenu dynamique par matière
  auth.py                      authentification (email/mot de passe + Google) via Supabase Auth
  fournisseurs_llm.py          LLM premium (Claude, GPT, Gemini, DeepSeek) — gardes-fous d'accès
  retriever.py                 recherche vectorielle parallèle (prompts, documents), scopée par agent
  embeddings.py                vectorisation partagée (gemini-embedding-001)
  bibliotheque_fichiers.py     bibliothèque de fichiers uploadés, persistante, à 3 niveaux d'accès
  bibliotheque_rag.py          RAG sur la bibliothèque personnelle d'un utilisateur
  dossiers_bibliotheque.py     dossiers/sous-dossiers de la bibliothèque personnelle
  catalogue_public_rag.py      RAG sur le catalogue de la bibliothèque publique
  dossiers_catalogue_public.py dossiers du catalogue public
  codes_partage.py             codes de partage (remplace l'ancien système d'invitations)
  file_attente_vectorisation.py file d'attente de vectorisation en arrière-plan
  calcul_symbolique.py         calcul symbolique via SymPy — pas de clé API, pas de service externe
  description_multimedia.py    description automatique d'image (vision) et transcription automatique
  conversion_pdf.py            conversion Word/Excel/PowerPoint → PDF, pour l'aperçu dans la bibliothèque
  securite_chemins.py          sanitisation des chemins relatifs fournis par le modèle pour les fichiers
  erreurs.py                   messages d'erreur centralisés, orientés utilisateur (miroir de lib/erreurs.ts)
  generation_*.py              outils de génération : images, documents, audio, vidéo, 3D, code, site,
                                LaTeX, signature (Lumin), archives (zip), données (JSON/XML)
  serveur_mcp_generation.py    serveur MCP interne (32 outils : génération, bibliothèque, mémoire,
                                comportements, exploration de dossier mobile...), monté directement dans l'app
  serveur_mcp_github.py        serveur MCP interne pour le connecteur GitHub
  serveur_mcp_espace.py        serveur MCP PUBLIC "Mon espace" — connecteur externe (Claude, etc.) exposé
                                à l'utilisateur, authentifié par OAuth 2.1
  serveur_mcp_public.py        serveur MCP PUBLIC de Clovis, destiné à être ajouté comme connecteur
  mcp_auth_public.py           vérification des jetons OAuth pour les serveurs MCP publics
  confirmations_mcp.py         confirmations en attente pour l'outil MCP externe discuter_avec_clovis
  notifications_push.py        notifications push (Web Push, FCM Android, APNs iOS)
  actions_appareil_mobile.py   "cerveau mobile" : file d'actions en attente pour le téléphone (fichiers,
                                notifications, DND...), consommées par l'app au prochain réveil
  canal_temps_reel.py          canal temps réel avec un téléphone ouvert (contrairement à
                                actions_appareil_mobile.py qui est fire-and-forget)
  exploration_dossier_mobile.py exploration en direct des fichiers du téléphone via le canal temps réel
  lecture_fichier_mobile.py    lecture d'un fichier du téléphone via le canal temps réel
  dossiers_designes_mobile.py  dossiers du téléphone désignés comme sources par l'utilisateur
  usage_appareil_mobile.py     socle de la brique "app mobile" côté backend
  creation_agent.py            logique pure de création d'agent (id, extraction)
  diagnostic.py                script de diagnostic — teste chaque maillon de la chaîne indépendamment

connexions/
  notion.py                    connexion Notion par utilisateur (OAuth 2.1 + PKCE + Dynamic Client Registration)
  oauth_generique.py           connexion générique pour les autres services OAuth

indexers/
  index_notion.py              indexation récursive Notion → Supabase
  index_documents.py           indexation PDF → Supabase (RAG documentaire)
  reembed_gemini.py            ré-indexation en masse vers l'embedding Gemini
  storage.py                   upload/liste/suppression de documents dans Supabase Storage

api/
  main.py                      app FastAPI, montage des routers, boucle de planificateur de proactivité
  auth.py                      vérification du JWT Supabase envoyé par le frontend
  chat.py                      endpoint de chat (streaming)
  agents.py                    configuration/édition de l'agent Clovis (system prompt, documents,
                                bibliothèque, administrateurs) — pas de création/suppression/vitrine
                                publique par agent (retiré le 14/08, une seule IA fixe)
  roles.py                     hiérarchie de rôles (nous/établissement/enseignant/étudiant)
  permissions_hierarchie.py    "qui a le droit de toucher à l'agent de qui", réutilisé par agents.py
  invitations_clovis.py        invitation d'autres personnes par message/code
  comportements_etudiants.py   endpoints du système de comportements
  contenu_dynamique_matiere.py contenu dynamique par matière
  bibliotheque_utilisateur.py  endpoints bibliothèque personnelle (niveau="utilisateur", pas d'agent_id)
  bibliotheque_publique.py     bibliothèque publique
  dossiers_bibliotheque.py     routes REST pour les dossiers de la bibliothèque personnelle
  dossiers_catalogue_public.py routes REST pour les dossiers du catalogue public
  comportements_publics.py     catalogue public de comportements
  codes_partage.py             endpoints des codes de partage
  outils_registre.py           registre d'affichage des outils, exposé au frontend
  generation.py                génération déclenchée par un bouton explicite (hors chat)
  signalements.py              signalements de contenu (bibliothèque publique + documents publics)
  contenu_legal.py             contenu légal (CGU, politique de copyright)
  appareils_mobiles.py         enregistrement/désinscription des appareils mobiles, résolution de titre Notion
  canal_temps_reel.py          endpoints du canal temps réel mobile
  historique.py, memoire.py    historique de conversation, mémoire
  profiles.py                  profil utilisateur (+ suppression de compte)
  notifications_push.py        routes des notifications push
  feedback.py                  like/dislike sur les messages assistant
  uploads.py                   upload d'images (avatar, image de vitrine)
  journal.py                   journal d'audit des actions structurelles/sensibles

migrations/                    schéma SQL, un fichier par changement, daté (39 fichiers)
scripts/                       scripts ponctuels (ex : génération de clés VAPID)
_desactive_programme/          ancien "programme d'études adaptatif" (api/core), désactivé le 28-29/08/2026 —
                                voir LISEZ_MOI_NE_JAMAIS_REUTILISER.md, ne jamais réactiver sans consigne explicite
```

## Ce qui tourne en production

- Backend FastAPI (`api/main.py`), déployé sur Railway (projet
  `prolific-truth`, service `clovis-backend`), buildé par Railpack.
- Frontend séparé : `clovis-frontend` (Next.js, sur Vercel), qui produit
  aussi le build mobile Capacitor (Android/iOS) consommant cette même API.
- Un serveur MCP public (`core/serveur_mcp_espace.py` /
  `serveur_mcp_public.py`) que Claude et d'autres clients MCP peuvent
  ajouter comme connecteur pour agir sur l'espace d'un utilisateur.
- Une seule IA (Clovis) — pas de multi-agents, pas de marketplace de
  créateurs. Certaines tables (`agents`, `agents_administrateurs`...) et
  endpoints de `api/agents.py` restent nommés "agent" par héritage du code
  d'origine, mais ne servent qu'à Clovis lui-même.
- Le "programme d'études adaptatif" (matière/chapitre/examens) est
  **désactivé** depuis fin août 2026, code déplacé dans
  `_desactive_programme/`, jamais à réutiliser sans consigne explicite.

## Variables d'environnement / secrets nécessaires

| Variable | Utilisée par |
|---|---|
| `SUPABASE_URL` / `SUPABASE_SECRET` | tout le projet |
| `GROQ_API_KEY` | `core/main.py` (LLM principal + secours), génération audio, uploads |
| `GOOGLE_API_KEY` | `core/embeddings.py`, `core/main.py` (secours Gemini) |
| `NOTION_TOKEN` | `indexers/index_notion.py`, `connexions/notion.py`, `core/configuration.py`, `core/diagnostic.py` |
| `TAVILY_API_KEY` | `core/registre_outils.py` (outil de recherche web) |
| `GITHUB_TOKEN` (optionnel) | `core/serveur_mcp_github.py` — sans lui, l'API GitHub non authentifiée est plafonnée à 60 requêtes/heure PAR IP ; un classic token `public_repo` fait passer la limite à 5000/heure |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | `connexions/oauth_generique.py` (connexion OAuth GitHub par agent) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` | `core/fournisseurs_llm.py` (LLM premium optionnels — absents, seuls Gemini/DeepSeek de secours restent proposés) |
| `TOGETHER_API_KEY` | `core/generation_images.py` (meilleure fiabilité que le fournisseur par défaut) |
| `FAL_KEY` | `core/generation_3d.py`, `core/generation_video.py` — absente en prod, ces deux outils sont désactivés (`disponible=false`) |
| `GOOGLE_TTS_API_KEY` / `AUDIO_TTS_ACTIF` | `core/generation_audio.py` — absentes en prod, `generer_audio` désactivé |
| `LUMIN_API_KEY` | `core/generation_signature.py` — absente en prod, `envoyer_pour_signature` désactivé |
| `CLOUDCONVERT_API_KEY` | `core/conversion_pdf.py` (aperçu PDF pour Word/Excel/PowerPoint) |
| `VERCEL_API_TOKEN` | `core/generation_site.py` (déploiement de sites générés — présente et fonctionnelle) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY_PEM_B64` | `core/notifications_push.py`, canal Web Push (générées une seule fois via `scripts/generer_cles_vapid.py`) |
| `FCM_SERVICE_ACCOUNT_JSON_B64` / `FCM_PROJECT_ID` | `core/notifications_push.py`, canal Android — pas encore configurées en prod (TODO explicite) |
| `APNS_KEY_P8_B64` / `APNS_KEY_ID` / `APNS_TEAM_ID` | `core/notifications_push.py`, canal iOS — pas encore configurées en prod (TODO explicite) |
| `URL_RETOUR_APP` | `connexions/notion.py`, `connexions/oauth_generique.py`, `api/agents.py` — URL publique du déploiement pour le retour OAuth, à recalculer à chaque changement de domaine |
| `URL_RESOURCE_SERVER_PUBLIC` | `core/mcp_auth_public.py` (serveur MCP public, retombe sur `RAILWAY_PUBLIC_DOMAIN` si absente) |

Détail complet, variables obsolètes et checklist Railway : voir
`RAILWAY_DEPLOY.md`.

## Lancer l'app

```
uvicorn api.main:app --reload
```

## Indexer un nouveau document PDF

```
python indexers/index_documents.py mon_document.pdf
```

(le fichier doit déjà être présent dans le bucket Supabase Storage configuré)
