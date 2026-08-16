"""
Serveur MCP public "Mon espace" (Partie 3 du chantier MCP externe --
Bourama, 16/08/2026), pour Clovis exclusivement (dépôts clovis-backend +
classgpt-frontend, sans rapport avec djiguigne-backend/djiguigne-ai).

But : exposer à un client MCP externe (Claude, connecté par
l'utilisateur en dehors de l'app Clovis) les fonctionnalités de "Mon
espace" -- Bibliothèque, Ma mémoire, Mes comportements, Historique --
pour qu'il puisse les consulter/gérer sans repasser par l'interface web.

Différence fondamentale avec core/serveur_mcp_generation.py (serveur MCP
INTERNE, utilisé par la boucle d'appel d'outils du LLM de Clovis
lui-même pendant une conversation dans l'app, monté en localhost
uniquement -- voir _url_generation dans registre_outils.py) : ce
serveur-ci est destiné à être monté sur une route PUBLIQUE (voir Partie
1, squelette du serveur MCP public) derrière une authentification
externe réelle (voir Partie 2), pas juste des query params user_id/
agent_id injectés serveur-côté. Tant que les Parties 1/2 ne sont pas
branchées, ce fichier est autonome et suppose que `ctx` fournit déjà un
user_id de confiance (voir _user_id_authentifie ci-dessous -- à
brancher sur le vrai mécanisme d'auth externe une fois la Partie 2
prête, actuellement un simple relais du query param en attendant).

Convention reprise des serveurs MCP existants (voir docstring de
core/registre_outils.py et de serveur_mcp_generation.py) : la logique
Supabase est dupliquée ici plutôt qu'importée depuis les fichiers
api/*.py -- seuls les modules core/*.py (jamais api/*.py) sont
réutilisés, quand ils existent déjà (core/comportements_etudiants.py,
core/bibliotheque_fichiers.py, core/bibliotheque_rag.py,
core/codes_partage.py).

Clovis est mono-agent (agent Lirinus de l'ancien système
établissement/enseignant/étudiant : n'existe pas ici, résidu
djiguigne/api/roles.py obsolète depuis l'isolation du 12/08) --
AGENT_ID_ESPACE ci-dessous est une constante fixe ("clovis"), jamais un
paramètre fourni par l'appelant, cohérent avec AGENT_ID_PAR_DEFAUT déjà
en dur ailleurs (core/main.py, core/diagnostic.py, core/configuration.py,
core/retriever.py).

consulter_bibliotheque (recherche RAG dans la bibliothèque perso) existe
déjà dans core/serveur_mcp_generation.py et reste LA SEULE porte
d'entrée en lecture par contenu -- volontairement PAS dupliqué ici. Ce
fichier ne couvre que ce qui manquait : gestion (lister/ajouter/
supprimer) de la bibliothèque, mémoire (lire/modifier/effacer),
comportements (lister/ajouter/modifier/supprimer), historique (lecture
seule, fils de conversation).

Outils sensibles (écriture/suppression) : pas de boucle LLM interne ici
pour intercepter un appel avant exécution comme le fait core/main.py
via OUTILS_SENSIBLES (core/registre_outils.py) -- ce mécanisme est
propre à l'agent interne, pas transposable. Remplacé par l'annotation
MCP standard `destructive_hint=True` (voir mcp.types.ToolAnnotations),
signal que tout client MCP correctement implémenté (dont Claude) utilise
pour demander confirmation à l'utilisateur avant d'appeler l'outil.
"""

import logging
import os
import tempfile

from mcp.server.mcpserver import MCPServer as FastMCP, Context
from mcp.types import ToolAnnotations
from supabase import create_client

from core.bibliotheque_fichiers import (
    enregistrer_fichier as _enregistrer_fichier,
    enregistrer_lien as _enregistrer_lien,
    lister_fichiers as _lister_fichiers,
    supprimer_fichier as _supprimer_fichier,
)
from core.bibliotheque_rag import (
    indexer_pdf_bibliotheque as _indexer_pdf_bibliotheque,
    indexer_texte_bibliotheque as _indexer_texte_bibliotheque,
)
from core.codes_partage import (
    propager_fichier_bibliotheque as _propager_fichier_bibliotheque,
    propager_lien_bibliotheque as _propager_lien_bibliotheque,
)
from core.comportements_etudiants import (
    lister_comportements as _lister_comportements,
    ajouter_comportement as _ajouter_comportement,
    modifier_comportement as _modifier_comportement,
    supprimer_comportement as _supprimer_comportement,
)

_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
_supabase = create_client(_SUPABASE_URL, _SUPABASE_SECRET)

# Clovis mono-agent : voir docstring en tête de fichier. Fixe, jamais un
# paramètre exposé aux outils ci-dessous.
AGENT_ID_ESPACE = "clovis"

# Mêmes contraintes que api/bibliotheque_utilisateur.py (à garder en
# phase si elles changent là-bas).
_TYPES_AUTORISES = {
    "application/pdf",
    "image/jpeg", "image/png", "image/webp",
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4",
    "video/mp4", "video/webm", "video/quicktime",
}
_TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo

mcp_espace = FastMCP(name="espace")


def _user_id_authentifie(ctx: Context) -> str | None:
    """
    Point d'entrée UNIQUE pour récupérer l'identité de l'appelant dans ce
    fichier -- à brancher sur le vrai mécanisme d'auth externe une fois
    la Partie 2 prête. Pour l'instant, relais du query param "user_id"
    (même mécanique que planifier_rappel/envoyer_message dans
    serveur_mcp_generation.py), MAIS ce fichier n'est pas censé être
    monté en localhost comme le serveur interne : ne pas exposer cette
    route publiquement tant que la Partie 2 n'a pas remplacé cette
    fonction par une vérification réelle (token/session), sous peine de
    laisser n'importe quel appelant lire/modifier les données de
    n'importe quel user_id fourni en clair.
    """
    try:
        return ctx.request_context.request.query_params.get("user_id")
    except Exception:
        return None


# --- Bibliothèque personnelle -----------------------------------------
# consulter_bibliotheque (recherche RAG) existe déjà dans
# core/serveur_mcp_generation.py, volontairement pas dupliqué ici.

@mcp_espace.tool()
def lister_bibliotheque(ctx: Context) -> str:
    """
    Liste les documents/liens/notes de la bibliothèque personnelle de cet
    utilisateur (section "Bibliothèque" de "Mon espace"), sans effectuer
    de recherche par contenu (voir consulter_bibliotheque pour ça).
    Renvoie pour chaque entrée : id, description, type, date d'ajout.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        fichiers = _lister_fichiers("utilisateur", user_id=user_id, origine="bibliotheque")
    except Exception as e:
        logging.error(f"ERREUR outil lister_bibliotheque : {e}")
        return "Erreur : impossible de lister la bibliothèque, réessaie."
    if not fichiers:
        return "Bibliothèque vide pour l'instant."
    lignes = [
        f"- id={f['id']} | {f.get('description') or f.get('nom_fichier')} "
        f"({f.get('type_mime', 'inconnu')}, ajouté le {f.get('created_at', '?')})"
        for f in fichiers
    ]
    return "\n".join(lignes)


@mcp_espace.tool()
def ajouter_lien_bibliotheque(url: str, titre: str, ctx: Context) -> str:
    """
    Ajoute un lien à la bibliothèque personnelle de cet utilisateur.
    `url` : l'adresse à enregistrer. `titre` : nom donné à cette entrée
    (utilise l'URL elle-même si aucun titre pertinent n'est fourni).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    url = (url or "").strip()
    if not url:
        return "Erreur : url manquante."
    titre_final = (titre or url).strip()
    try:
        ligne = _enregistrer_lien(
            url=url,
            nom_fichier=titre_final,
            niveau="utilisateur",
            uploade_par=user_id,
            user_id=user_id,
            description=titre_final,
        )
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_lien_bibliotheque : {e}")
        return "Erreur : impossible d'enregistrer ce lien, réessaie."
    try:
        _propager_lien_bibliotheque(user_id, url, titre_final, titre_final)
    except Exception as e:
        logging.error(f"ERREUR propagation ajouter_lien_bibliotheque : {e}")
    return f"Lien ajouté (id {ligne['id']})."


@mcp_espace.tool()
def ajouter_texte_bibliotheque(contenu: str, titre: str, ctx: Context) -> str:
    """
    Ajoute une note de texte libre à la bibliothèque personnelle de cet
    utilisateur (immédiatement consultable par consulter_bibliotheque).
    `contenu` : le texte à enregistrer. `titre` : nom donné à cette note.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    contenu = (contenu or "").strip()
    if not contenu:
        return "Erreur : contenu vide."
    titre = (titre or "").strip()
    nom_fichier = f"{titre or 'Note'}.txt"
    description = titre or (contenu[:80] + ("…" if len(contenu) > 80 else ""))
    try:
        ligne = _enregistrer_fichier(
            contenu=contenu.encode("utf-8"),
            nom_fichier=nom_fichier,
            type_mime="text/plain",
            niveau="utilisateur",
            uploade_par=user_id,
            user_id=user_id,
            description=description,
        )
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_texte_bibliotheque : {e}")
        return "Erreur : impossible d'enregistrer cette note, réessaie."
    try:
        _indexer_texte_bibliotheque(contenu, fichier_id=ligne["id"], user_id=user_id)
    except Exception as e:
        logging.error(f"ERREUR vectorisation ajouter_texte_bibliotheque : {e}")
    try:
        _propager_fichier_bibliotheque(user_id, contenu.encode("utf-8"), nom_fichier, "text/plain", titre or None)
    except Exception as e:
        logging.error(f"ERREUR propagation ajouter_texte_bibliotheque : {e}")
    return f"Note ajoutée (id {ligne['id']})."


@mcp_espace.tool()
def ajouter_document_bibliotheque(nom_fichier: str, type_mime: str, contenu_base64: str, titre: str, description: str, ctx: Context) -> str:
    """
    Ajoute un fichier (PDF, image, audio ou vidéo) à la bibliothèque
    personnelle de cet utilisateur -- même effet que s'il l'avait
    uploadé lui-même depuis "Mon espace". `nom_fichier` : nom du fichier
    avec son extension (ex. "cours_svt.pdf"). `type_mime` : type MIME
    exact du fichier (ex. "application/pdf", "image/png", "audio/mpeg",
    "video/mp4" -- types autorisés : PDF, JPEG/PNG/WebP, MP3/WAV/OGG/M4A,
    MP4/WebM/MOV). `contenu_base64` : contenu du fichier encodé en
    base64 (jamais de contenu brut binaire). `titre`/`description` :
    optionnels, repli sur le nom du fichier si absents. Limite : 50 Mo.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    type_mime = (type_mime or "").strip().lower()
    if type_mime not in _TYPES_AUTORISES:
        return f"Erreur : type de fichier non supporté ({type_mime or 'absent'})."

    import base64
    try:
        contenu = base64.b64decode(contenu_base64, validate=True)
    except Exception:
        return "Erreur : contenu_base64 invalide (doit être du base64 valide)."

    if len(contenu) == 0:
        return "Erreur : fichier vide."
    if len(contenu) > _TAILLE_MAX_OCTETS:
        return "Erreur : fichier trop lourd (50 Mo max)."

    nom_original = (nom_fichier or "fichier").strip()
    titre = (titre or "").strip()
    description = (description or "").strip()
    description_finale = (
        f"{titre} — {description}" if titre and description
        else (description or titre or nom_original)
    )

    try:
        ligne = _enregistrer_fichier(
            contenu=contenu,
            nom_fichier=nom_original,
            type_mime=type_mime,
            niveau="utilisateur",
            uploade_par=user_id,
            user_id=user_id,
            description=description_finale,
        )
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_document_bibliotheque : {e}")
        return "Erreur : impossible d'enregistrer ce fichier, réessaie."

    if type_mime == "application/pdf":
        chemin_temp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(contenu)
                chemin_temp = tmp.name
            _indexer_pdf_bibliotheque(chemin_temp, fichier_id=ligne["id"], user_id=user_id)
        except Exception as e:
            # Non bloquant, même logique que api/bibliotheque_utilisateur.py :
            # le fichier est déjà stocké, seule la recherche par contenu
            # (consulter_bibliotheque) sera indisponible pour celui-ci.
            logging.error(f"ERREUR vectorisation ajouter_document_bibliotheque (fichier_id={ligne['id']}) : {e}")
        finally:
            if chemin_temp:
                try:
                    os.remove(chemin_temp)
                except OSError:
                    pass

    try:
        _propager_fichier_bibliotheque(user_id, contenu, nom_original, type_mime, description_finale)
    except Exception as e:
        logging.error(f"ERREUR propagation ajouter_document_bibliotheque : {e}")

    return f"Fichier ajouté (id {ligne['id']})."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_document_bibliotheque(fichier_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un document/lien/note de la bibliothèque
    personnelle de cet utilisateur, à partir de son id (voir
    lister_bibliotheque). SENSIBLE : le client doit confirmer avec
    l'utilisateur avant d'appeler cet outil.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        res = (
            _supabase.table("fichiers_uploades")
            .select("user_id")
            .eq("id", fichier_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_document_bibliotheque (lecture) : {e}")
        return "Erreur : impossible de supprimer ce document, réessaie."
    if not res or not res.data:
        return "Ce document est introuvable."
    if res.data["user_id"] != user_id:
        return "Ce document ne t'appartient pas."
    try:
        _supprimer_fichier(fichier_id)
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_document_bibliotheque (suppression) : {e}")
        return "Erreur : impossible de supprimer ce document, réessaie."
    return "Document supprimé."


# --- Mémoire (résumé long-terme, "Ma mémoire" de "Mon espace") --------
# Colonne `summary` de conversation_summaries (celle que lit/écrit
# core/main.py::_charger_resume_memoire, celle affichée par
# MaMemoire.tsx via /api/memoire). À NE PAS confondre avec la colonne
# `donnees` (JSON) utilisée par consulter_memoire_utilisateur /
# mettre_a_jour_memoire_utilisateur dans serveur_mcp_generation.py :
# deux mécanismes distincts sur la même table, celui-ci est le seul
# visible dans "Mon espace" côté utilisateur -- découverte faite en
# auditant le dépôt (16/08), signalée à Bourama.

@mcp_espace.tool()
def lire_memoire(ctx: Context) -> str:
    """
    Lit le résumé long-terme que Clovis garde de cet utilisateur (section
    "Ma mémoire" de "Mon espace"), valable pour toutes ses conversations.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        res = (
            _supabase.table("conversation_summaries")
            .select("summary")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR outil lire_memoire : {e}")
        return "Erreur : impossible de lire la mémoire, réessaie."
    resume = (res.data or {}).get("summary") or "" if res else ""
    return resume or "Rien en mémoire pour l'instant."


@mcp_espace.tool()
def modifier_memoire(resume: str, ctx: Context) -> str:
    """
    Réécrit intégralement le résumé long-terme que Clovis garde de cet
    utilisateur (remplace le texte existant, ne le complète pas).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        _supabase.table("conversation_summaries").upsert({
            "user_id": user_id,
            "summary": (resume or "").strip(),
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR outil modifier_memoire : {e}")
        return "Erreur : impossible d'enregistrer la mémoire, réessaie."
    return "Mémoire mise à jour."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def effacer_memoire(ctx: Context) -> str:
    """
    Efface DÉFINITIVEMENT le résumé long-terme que Clovis garde de cet
    utilisateur ("oublie tout ce que tu sais de moi"). SENSIBLE : le
    client doit confirmer avec l'utilisateur avant d'appeler cet outil.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        _supabase.table("conversation_summaries").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"ERREUR outil effacer_memoire : {e}")
        return "Erreur : impossible d'effacer la mémoire, réessaie."
    return "Mémoire effacée."


# --- Mes comportements (agent_id fixe "clovis") ------------------------

@mcp_espace.tool()
def lister_comportements(ctx: Context) -> str:
    """
    Liste les instructions personnelles que cet utilisateur a écrites
    lui-même (section "Mes comportements" de "Mon espace") pour Clovis.
    Renvoie pour chacune : id, description courte, texte complet.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        comportements = _lister_comportements(AGENT_ID_ESPACE, user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lister_comportements : {e}")
        return "Erreur : impossible de lister les comportements, réessaie."
    if not comportements:
        return "Aucun comportement enregistré pour l'instant."
    lignes = [
        f"- id={c['id']} | {c['description']}\n  texte : {c['texte']}"
        for c in comportements
    ]
    return "\n".join(lignes)


@mcp_espace.tool()
def ajouter_comportement_espace(texte: str, ctx: Context) -> str:
    """
    Enregistre une nouvelle instruction personnelle pour cet utilisateur
    (section "Mes comportements"). S'ajoute EN PLUS des comportements
    déjà existants, ne les remplace pas.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    texte = (texte or "").strip()
    if not texte:
        return "Erreur : texte requis."
    try:
        ligne = _ajouter_comportement(AGENT_ID_ESPACE, user_id, texte)
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_comportement_espace : {e}")
        return "Erreur : impossible d'enregistrer ce comportement, réessaie."
    return f"Comportement enregistré (id {ligne['id']}) : {ligne['description']}"


@mcp_espace.tool()
def modifier_comportement_espace(comportement_id: str, texte: str, ctx: Context) -> str:
    """
    Remplace le texte complet d'un comportement existant de cet
    utilisateur, à partir de son id (voir lister_comportements).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    texte = (texte or "").strip()
    if not texte:
        return "Erreur : texte requis."
    try:
        ligne = _modifier_comportement(AGENT_ID_ESPACE, user_id, comportement_id, texte)
    except Exception as e:
        logging.error(f"ERREUR outil modifier_comportement_espace : {e}")
        return "Erreur : impossible de modifier ce comportement, réessaie."
    if ligne is None:
        return "Ce comportement est introuvable."
    return f"Comportement modifié : {ligne['description']}"


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_comportement_espace(comportement_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un comportement de cet utilisateur, à partir
    de son id (voir lister_comportements). SENSIBLE : le client doit
    confirmer avec l'utilisateur avant d'appeler cet outil.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_comportement(AGENT_ID_ESPACE, user_id, comportement_id)
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_comportement_espace : {e}")
        return "Erreur : impossible de supprimer ce comportement, réessaie."
    if not ok:
        return "Ce comportement est introuvable."
    return "Comportement supprimé."


# --- Historique (lecture seule, agent_id fixe "clovis") ----------------
# Clovis mono-agent : lister_conversations (multi-agent, tableau de bord
# djiguigne) n'a pas de sens ici -- seuls les fils de discussion avec
# l'unique agent Clovis sont exposés (voir SidebarChatLite.tsx côté
# frontend, qui consomme exactement ce même regroupement).

_LONGUEUR_MAX_TITRE = 42


@mcp_espace.tool()
def lister_conversations_historique(ctx: Context) -> str:
    """
    Liste les fils de discussion distincts entre cet utilisateur et
    Clovis (section "Historique"), le plus récemment actif en premier.
    Renvoie pour chacun : conversation_id ("legacy" pour les échanges
    d'avant l'historique par fil), titre (début du premier message),
    dernière activité.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        lignes = (
            _supabase.table("historique_conversations")
            .select("conversation_id, role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", AGENT_ID_ESPACE)
            .order("created_at")
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR outil lister_conversations_historique : {e}")
        return "Erreur : impossible de charger l'historique, réessaie."

    if not lignes:
        return "Aucune conversation dans l'historique pour l'instant."

    fils: dict = {}
    for ligne in lignes:
        cle = ligne["conversation_id"] or "legacy"
        if cle not in fils:
            fils[cle] = {"premier_message_user": None, "derniere_activite": ligne["created_at"]}
        if ligne["role"] == "user" and fils[cle]["premier_message_user"] is None:
            fils[cle]["premier_message_user"] = ligne["content"]
        fils[cle]["derniere_activite"] = ligne["created_at"]

    resultats = []
    for cle, fil in fils.items():
        if cle == "legacy":
            titre = "Avant l'historique par conversation"
        else:
            titre = (fil["premier_message_user"] or "Conversation sans titre").strip()
            if len(titre) > _LONGUEUR_MAX_TITRE:
                titre = titre[:_LONGUEUR_MAX_TITRE].rstrip() + "…"
        resultats.append((cle, titre, fil["derniere_activite"]))

    resultats.sort(key=lambda r: r[2], reverse=True)
    return "\n".join(
        f"- conversation_id={cle} | {titre} (dernière activité : {activite})"
        for cle, titre, activite in resultats
    )


@mcp_espace.tool()
def lire_conversation_historique(conversation_id: str, ctx: Context) -> str:
    """
    Contenu complet d'un fil de discussion précis entre cet utilisateur
    et Clovis, à partir de son conversation_id (voir
    lister_conversations_historique -- utilise littéralement "legacy"
    pour recharger les échanges d'avant l'historique par fil).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        requete = (
            _supabase.table("historique_conversations")
            .select("role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", AGENT_ID_ESPACE)
        )
        if conversation_id == "legacy":
            requete = requete.is_("conversation_id", "null")
        else:
            requete = requete.eq("conversation_id", conversation_id)
        lignes = requete.order("created_at").execute().data or []
    except Exception as e:
        logging.error(f"ERREUR outil lire_conversation_historique : {e}")
        return "Erreur : impossible de charger cette conversation, réessaie."

    if not lignes:
        return "Cette conversation est introuvable ou vide."

    return "\n".join(f"[{l['role']}] {l['content']}" for l in lignes)
