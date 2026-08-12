# Déploiement Railway — checklist

Établie en lisant le code réel (grep de `get_secret("...")` sur tout le
dépôt) et la config Railway réelle du service `web` (projet `adequate-joy`,
domaine `web-production-16d21.up.railway.app`), pas en devinant.

Depuis le retrait complet de Streamlit (25/07/2026), il n'existe plus
qu'un seul service de prod pour ce dépôt : l'API FastAPI (`api/main.py`),
buildée par Railpack (`railpack.json` à la racine, `nixpacks.toml` n'existe
plus).

## Variables actuellement configurées sur Railway

| Variable | Utilisée par |
|---|---|
| `SUPABASE_URL` | tout le projet (13 fichiers) |
| `SUPABASE_SECRET` | tout le projet (13 fichiers) |
| `GROQ_API_KEY` | core/main.py, core/proactivite.py, core/generation_audio.py, api/uploads.py |
| `GOOGLE_API_KEY` | core/main.py (secours Gemini), core/embeddings.py |
| `NOTION_TOKEN` | indexers/index_notion.py, core/diagnostic.py, core/configuration.py |
| `GITHUB_TOKEN` | core/main.py, core/serveur_mcp_github.py |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | connexions/oauth_generique.py, core/main.py (connexion OAuth GitHub par agent) |
| `TAVILY_API_KEY` | core/registre_outils.py (outil de recherche web) |
| `CLOUDCONVERT_API_KEY` | core/conversion_pdf.py (aperçu PDF pour Word/Excel/PowerPoint) |
| `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY_PEM_B64` | core/notifications_push.py (générées une seule fois via `scripts/generer_cles_vapid.py`) |
| `VERCEL_API_TOKEN` | core/generation_site.py (déploiement de sites générés) |
| `URL_RETOUR_APP` | connexions/notion.py, connexions/oauth_generique.py, api/agents.py — retour OAuth, doit correspondre à l'URL publique réelle du déploiement API |
| `RAILPACK_BUILD_APT_PACKAGES` / `RAILPACK_DEPLOY_APT_PACKAGES` | config Railpack native (voir aussi `railpack.json`) |

## Variable configurée mais non utilisée par le code (volontaire)

- `HF_API_TOKEN` — présente sur Railway, aucune référence dans le code
  actuellement. Intégration Hugging Face prévue mais pas encore branchée :
  nécessite un moyen de paiement/carte pour l'activer côté Hugging Face,
  pas encore en place. Laissée telle quelle en attendant, à récupérer plus
  tard une fois le moyen de paiement disponible.

## Variables obsolètes (ne plus chercher à les reporter)

Ne sont lues nulle part dans le code actuel :
- `OPENROUTER_API_KEY` (remplacé par `GOOGLE_API_KEY`, migration Gemini)
- `NOTION_PAGE_ID` (remplacé par la colonne `agents.notion_page_id`, multi-agent)
- `AGENT_ID` (existait comme secret de repli avant le multi-agent ; l'app
  lit désormais l'agent depuis l'auth utilisateur, plus depuis une URL)

## Fichiers de config Railway dans ce dépôt

- `Procfile` — `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
- `railpack.json` — paquets apt système (ffmpeg pour l'extraction
  vidéo, Pango/Cairo/GDK-Pixbuf pour WeasyPrint)
- `requirements.txt` — un seul, à la racine, dépendances Python

## Point encore ouvert

Le sous-domaine par agent (`son-agent.djiguigne.com`) nécessite un
enregistrement DNS wildcard (`*.djiguigne.com`) une fois le domaine
personnalisé branché dans Railway, plus la lecture de l'agent depuis le
sous-domaine de la requête plutôt que depuis l'auth utilisateur — reste à
coder, pas commencé.
