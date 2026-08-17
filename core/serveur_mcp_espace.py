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
core/codes_partage.py, core/bibliotheque_programme.py).

Liens bibliothèque <-> programme <-> comportements (16/08/2026, demande
Bourama) : voir core/bibliotheque_programme.py pour la logique
(classement many-to-many bibliothèque/programme, propriétaire des liens
de comportement). documents_programme (ancien mécanisme titre+lien
rattaché uniquement à un chapitre) n'est pas touché ni remplacé -- ce
qui suit s'ajoute en parallèle, la coexistence est assumée.

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
import uuid

from mcp.server.mcpserver import MCPServer as FastMCP, Context
from mcp.types import ToolAnnotations
from supabase import create_client

from core.mcp_auth_public import (
    VerificateurJetonSupabase,
    construire_auth_settings,
    user_id_depuis_contexte as _user_id_verifie,
)
from core.programme_llm import (
    lister_mes_programmes_legers as _lister_mes_programmes_legers,
    obtenir_structure_programme as _obtenir_structure_programme,
    obtenir_chapitres_matiere as _obtenir_chapitres_matiere,
    obtenir_contenu_chapitre as _obtenir_contenu_chapitre,
    obtenir_examens_programme as _obtenir_examens_programme,
)
from core.programme_ecriture import (
    ajouter_programme as _ajouter_programme,
    modifier_programme as _modifier_programme,
    supprimer_programme as _supprimer_programme,
    ajouter_matiere as _ajouter_matiere,
    modifier_matiere as _modifier_matiere,
    supprimer_matiere as _supprimer_matiere,
    ajouter_chapitre as _ajouter_chapitre,
    modifier_chapitre as _modifier_chapitre,
    supprimer_chapitre as _supprimer_chapitre,
    ajouter_document as _ajouter_document_programme,
    modifier_document as _modifier_document_programme,
    supprimer_document as _supprimer_document_programme,
    ajouter_exercice as _ajouter_exercice_programme,
    modifier_exercice as _modifier_exercice_programme,
    supprimer_exercice as _supprimer_exercice_programme,
    ajouter_examen as _ajouter_examen,
    modifier_examen as _modifier_examen,
    supprimer_examen as _supprimer_examen,
    annuler_derniere_modification as _annuler_derniere_modification,
)
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
from core.description_multimedia import (
    decrire_image_bibliotheque as _decrire_image_bibliotheque,
    transcrire_audio_bibliotheque as _transcrire_audio_bibliotheque,
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
from core.bibliotheque_programme import (
    classer_document as _classer_document,
    declasser_document as _declasser_document,
    lister_emplacements_document as _lister_emplacements_document,
    proprietaire_lien_comportement as _proprietaire_lien_comportement,
    libelle_emplacement as _libelle_emplacement,
    TYPES_EMPLACEMENT_BIBLIOTHEQUE,
    TYPES_LIEN_COMPORTEMENT,
)
from main import chat as _chat_generateur  # core/main.py:chat() -- import bare comme dans api/chat.py (core/ deja sur sys.path a ce point, voir api/main.py : api.chat importe avant core.serveur_mcp_espace)
from core.confirmations_mcp import (
    creer_confirmation as _creer_confirmation,
    recuperer_confirmation as _recuperer_confirmation,
    supprimer_confirmation as _supprimer_confirmation,
)

_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_SECRET = os.environ.get("SUPABASE_SECRET")
_supabase = create_client(_SUPABASE_URL, _SUPABASE_SECRET)

# Clovis mono-agent : voir docstring en tête de fichier. Fixe, jamais un
# paramètre exposé aux outils ci-dessous.
AGENT_ID_ESPACE = "clovis"

# 17/08 (Bourama : "il faut qu'on puisse uploader tout") -- whitelist
# retirée, comme côté api/bibliotheque_utilisateur.py (à garder en
# phase si ça change là-bas). Seule la taille reste contrôlée.
_TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo

mcp_espace = FastMCP(
    name="espace",
    token_verifier=VerificateurJetonSupabase(),
    auth=construire_auth_settings("/mcp/espace"),
)


def _user_id_authentifie(ctx: Context) -> str | None:
    """
    Point d'entrée UNIQUE pour récupérer l'identité de l'appelant dans ce
    fichier. Rebranché (voir Partie 2, core/mcp_auth_public.py) sur la
    vraie vérification de jeton OAuth Supabase -- l'identité vient
    exclusivement du jeton déjà validé par la librairie mcp avant
    l'exécution de l'outil, plus jamais d'un query param "user_id" fourni
    en clair par l'appelant.
    """
    return _user_id_verifie(ctx)


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
    lignes = []
    for f in fichiers:
        ligne = (
            f"- id={f['id']} | {f.get('description') or f.get('nom_fichier')} "
            f"({f.get('type_mime', 'inconnu')}, ajouté le {f.get('created_at', '?')})"
        )
        emplacements = _lister_emplacements_document(f["id"])
        if emplacements:
            ligne += " | classé dans : " + ", ".join(e["libelle"] for e in emplacements)
        lignes.append(ligne)
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
def ajouter_document_bibliotheque(
    nom_fichier: str, type_mime: str, contenu_base64: str, titre: str, description: str, ctx: Context,
    type_emplacement: str = "", emplacement_id: str = "",
) -> str:
    """
    Ajoute un fichier (PDF, image, audio ou vidéo) à la bibliothèque
    personnelle de cet utilisateur -- même effet que s'il l'avait
    uploadé lui-même depuis "Mon espace". `nom_fichier` : nom du fichier
    avec son extension (ex. "cours_svt.pdf"). `type_mime` : type MIME
    exact du fichier (ex. "application/pdf", "image/png", "audio/mpeg",
    "video/mp4", ou tout autre type MIME -- n'importe quel type de
    fichier est accepté). `contenu_base64` : contenu du fichier encodé en
    base64 (jamais de contenu brut binaire). `titre`/`description` :
    optionnels, repli sur le nom du fichier si absents. Limite : 50 Mo.
    `type_emplacement`/`emplacement_id` : optionnels -- si fournis
    ("programme"/"matiere"/"chapitre"/"exercice"/"examen" + son id),
    classe directement ce document à cet endroit du programme dès
    l'ajout (équivalent à appeler classer_document_dans_programme
    juste après).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    type_mime = (type_mime or "").strip().lower()
    if not type_mime:
        return "Erreur : type de fichier manquant."

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
    elif type_mime.startswith("image/"):
        try:
            description_image = _decrire_image_bibliotheque(contenu, type_mime)
            if description_image:
                _indexer_texte_bibliotheque(description_image, fichier_id=ligne["id"], user_id=user_id)
        except Exception as e:
            logging.error(f"ERREUR vectorisation image ajouter_document_bibliotheque (fichier_id={ligne['id']}) : {e}")
    elif type_mime.startswith("audio/"):
        try:
            transcription_audio = _transcrire_audio_bibliotheque(contenu, nom_original)
            if transcription_audio:
                _indexer_texte_bibliotheque(transcription_audio, fichier_id=ligne["id"], user_id=user_id)
        except Exception as e:
            logging.error(f"ERREUR vectorisation audio ajouter_document_bibliotheque (fichier_id={ligne['id']}) : {e}")

    try:
        _propager_fichier_bibliotheque(user_id, contenu, nom_original, type_mime, description_finale)
    except Exception as e:
        logging.error(f"ERREUR propagation ajouter_document_bibliotheque : {e}")

    message = f"Fichier ajouté (id {ligne['id']})."
    if type_emplacement and emplacement_id:
        if type_emplacement not in TYPES_EMPLACEMENT_BIBLIOTHEQUE:
            message += f" Attention : type d'emplacement invalide ({type_emplacement}), pas classé dans le programme."
        else:
            resultat = _classer_document(user_id, ligne["id"], type_emplacement, emplacement_id)
            if resultat["ok"]:
                libelle = _libelle_emplacement(type_emplacement, emplacement_id) or emplacement_id
                message += f" Classé dans : {libelle}."
            else:
                message += f" Attention : pas classé dans le programme ({resultat['erreur']})"
    return message


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


# --- Classement des documents dans le programme (16/08, demande
# Bourama) : un document de la bibliothèque peut être classé à un ou
# plusieurs emplacements du programme (programme entier / matière /
# chapitre) -- c'est ce classement qui fait qu'un document "ajouté dans
# le programme" apparaît dans la bibliothèque avec un libellé, et
# inversement qu'un document de la bibliothèque peut être rangé dans le
# programme.

@mcp_espace.tool()
def classer_document_dans_programme(fichier_id: str, type_emplacement: str, emplacement_id: str, ctx: Context) -> str:
    """
    Classe un document de la bibliothèque personnelle à un emplacement
    du programme de cet utilisateur. `type_emplacement` : "programme",
    "matiere", "chapitre", "exercice" ou "examen". `emplacement_id` : id
    de cet élément précis du programme. Un même document peut être classé à plusieurs
    emplacements (appeler cet outil plusieurs fois) ; reclasser au même
    endroit ne crée pas de doublon.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    if type_emplacement not in TYPES_EMPLACEMENT_BIBLIOTHEQUE:
        return f"Erreur : type d'emplacement invalide, utilise l'un de {TYPES_EMPLACEMENT_BIBLIOTHEQUE}."
    resultat = _classer_document(user_id, fichier_id, type_emplacement, emplacement_id)
    if not resultat["ok"]:
        return f"Erreur : {resultat['erreur']}"
    libelle = _libelle_emplacement(type_emplacement, emplacement_id) or emplacement_id
    return f"Document classé dans : {libelle}."


@mcp_espace.tool()
def retirer_document_du_programme(fichier_id: str, type_emplacement: str, emplacement_id: str, ctx: Context) -> str:
    """
    Retire un document de la bibliothèque d'un emplacement du programme
    (le document reste dans la bibliothèque, seul ce classement précis
    disparaît). Mêmes paramètres que classer_document_dans_programme.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    resultat = _declasser_document(user_id, fichier_id, type_emplacement, emplacement_id)
    if not resultat["ok"]:
        return f"Erreur : {resultat['erreur']}"
    return "Document retiré de cet emplacement du programme."


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
    lignes = []
    for c in comportements:
        ligne = f"- id={c['id']} | {c['description']}\n  texte : {c['texte']}"
        if c.get("lien_type") and c.get("lien_id"):
            libelle = _libelle_emplacement(c["lien_type"], c["lien_id"]) if c["lien_type"] in TYPES_EMPLACEMENT_BIBLIOTHEQUE else None
            ligne += f"\n  lié à : {libelle or (c['lien_type'] + ' ' + c['lien_id'])}"
        lignes.append(ligne)
    return "\n".join(lignes)


@mcp_espace.tool()
def ajouter_comportement_espace(texte: str, ctx: Context, type_lien: str = "", lien_id: str = "") -> str:
    """
    Enregistre une nouvelle instruction personnelle pour cet utilisateur
    (section "Mes comportements"). S'ajoute EN PLUS des comportements
    déjà existants, ne les remplace pas. `type_lien`/`lien_id` :
    optionnels -- si fournis, rattache ce comportement à un endroit
    précis du programme ("programme"/"matiere"/"chapitre"/"document"/
    "exercice"/"examen" + son id), comme si l'utilisateur avait rempli
    une section dédiée à cet endroit du programme. Sans ces deux
    paramètres, comportement générique comme avant (s'applique partout).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    texte = (texte or "").strip()
    if not texte:
        return "Erreur : texte requis."
    type_lien_final, lien_id_final = None, None
    if type_lien and lien_id:
        if type_lien not in TYPES_LIEN_COMPORTEMENT:
            return f"Erreur : type de lien invalide, utilise l'un de {TYPES_LIEN_COMPORTEMENT}."
        if _proprietaire_lien_comportement(type_lien, lien_id) != user_id:
            return "Erreur : cet emplacement du programme est introuvable ou ne t'appartient pas."
        type_lien_final, lien_id_final = type_lien, lien_id
    try:
        ligne = _ajouter_comportement(AGENT_ID_ESPACE, user_id, texte, type_lien_final, lien_id_final)
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_comportement_espace : {e}")
        return "Erreur : impossible d'enregistrer ce comportement, réessaie."
    message = f"Comportement enregistré (id {ligne['id']}) : {ligne['description']}"
    if type_lien_final:
        libelle = _libelle_emplacement(type_lien_final, lien_id_final) if type_lien_final in TYPES_EMPLACEMENT_BIBLIOTHEQUE else None
        message += f" (lié à : {libelle or (type_lien_final + ' ' + lien_id_final)})"
    return message


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


# --- Programme académique ----------------------------------------------
# Ajouté le 16/08/2026 (demande Bourama) : Claude doit pouvoir naviguer
# dans l'app comme s'il était notre IA -- pas seulement "Mon espace"
# (bibliothèque/mémoire/comportements/historique), mais aussi la gestion
# du programme scolaire (programmes/matières/chapitres/documents/
# exercices/examens). Aucune logique dupliquée : ce sont les mêmes
# fonctions core.programme_ecriture / core.programme_llm que
# core/serveur_mcp_generation.py (serveur interne) utilise déjà pour ce
# même besoin côté agent Clovis -- seule l'enveloppe MCP change ici
# (auth OAuth réelle via _user_id_authentifie au lieu du query param
# interne user_id, décorateur @mcp_espace.tool()). Docstrings et
# comportement des outils repris à l'identique de mcp_generation.

@mcp_espace.tool()
def lister_mes_programmes(ctx: Context) -> str:
    """
    Liste légère (id, niveau, nom) de TOUS les programmes de cet
    utilisateur -- point de départ obligatoire avant tout autre outil
    "programme" : ils ont tous besoin d'un programme_id/matiere_id/
    chapitre_id en entrée, jamais à deviner. Appelle cet outil en premier
    dès que l'utilisateur parle de son programme/ses matières/ses
    chapitres sans te donner d'id, pour savoir quels programmes existent
    et récupérer leurs ids. Ne contient PAS les matières/chapitres à
    l'intérieur (voir consulter_programme une fois l'id du programme
    choisi).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        programmes = _lister_mes_programmes_legers(user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lister_mes_programmes : {e}")
        return "Erreur : impossible de lister les programmes, réessaie."
    if not programmes:
        return "Aucun programme enregistré pour l'instant."
    return "\n".join(
        f"- {p['niveau']}" + (f" — {p['nom']}" if p.get("nom") else "") + f" (id: {p['id']})"
        for p in programmes
    )


@mcp_espace.tool()
def consulter_programme(programme_id: str, ctx: Context) -> str:
    """
    Lit les matières (avec leurs limites de cadre officiel si
    renseignées) d'un programme que cet étudiant a créé lui-même
    (section "Programme" de son espace), à partir de son id. Ne contient
    PAS les chapitres de ces matières, ni les examens/devoirs : une fois
    que tu as choisi une matière précise dans cette liste, utilise
    consulter_matiere_programme pour voir ses chapitres ; pour les
    examens/devoirs de ce programme (qui peuvent couvrir plusieurs
    matières/chapitres à la fois), utilise consulter_examens_programme.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        structure = _obtenir_structure_programme(user_id, programme_id)
        if structure is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return structure
    except Exception as e:
        logging.error(f"ERREUR outil consulter_programme : {e}")
        return "Erreur : impossible de consulter ce programme, réessaie."


@mcp_espace.tool()
def ajouter_programme(niveau: str, ctx: Context, nom: str = "") -> str:
    """
    Crée un nouveau programme (ex: "Terminale S", "3ème") pour CET
    utilisateur, dans sa section "Programme". `niveau` est le texte
    libre du niveau scolaire, `nom` un label optionnel s'il en donne un.
    Utilise cet outil quand l'utilisateur veut structurer une nouvelle
    année/classe, pas pour ajouter une matière à un programme déjà
    existant (voir ajouter_matiere). N'utilise JAMAIS cet outil sur une
    supposition -- si tu n'es pas sûr que l'utilisateur veut vraiment
    créer un nouveau programme, demande-lui de préciser avant d'agir.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _ajouter_programme(user_id, niveau, nom or None)
        return f"Programme créé (id {ligne['id']}) : {ligne['niveau']}" + (f" — {ligne['nom']}" if ligne.get("nom") else "")
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_programme : {e}")
        return "Erreur : impossible de créer ce programme, réessaie."


@mcp_espace.tool()
def modifier_programme(programme_id: str, ctx: Context, niveau: str = "", nom: str = "") -> str:
    """
    Modifie le niveau et/ou le nom d'un programme existant de CET
    utilisateur. Laisse un champ vide ("") pour ne pas le changer -- ne
    touche QUE les champs fournis. Ne modifie pas les matières/chapitres
    à l'intérieur (voir modifier_matiere, modifier_chapitre).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _modifier_programme(user_id, programme_id, niveau or None, nom if nom else None)
        if ligne is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Programme modifié : {ligne.get('niveau')}" + (f" — {ligne['nom']}" if ligne.get("nom") else "")
    except Exception as e:
        logging.error(f"ERREUR outil modifier_programme : {e}")
        return "Erreur : impossible de modifier ce programme, réessaie."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_programme(programme_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un programme de CET utilisateur, ainsi que
    TOUT son contenu (matières, chapitres, documents, exercices).
    SENSIBLE : demande toujours confirmation avant exécution, quelle que
    soit la formulation de la demande.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_programme(user_id, programme_id)
        if not ok:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Programme supprimé, avec tout son contenu."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_programme : {e}")
        return "Erreur : impossible de supprimer ce programme, réessaie."


@mcp_espace.tool()
def ajouter_matiere(programme_id: str, nom: str, ctx: Context, limites: str = "") -> str:
    """
    Ajoute une matière à un programme existant de CET utilisateur (ex:
    "Mathématiques" dans son programme "Terminale S"). `limites` est une
    description optionnelle du cadre officiel (pour savoir ce qui est
    "hors programme"). N'utilise JAMAIS cet outil sur une supposition --
    si l'utilisateur n'a pas clairement demandé d'ajouter CETTE matière
    à CE programme, demande-lui de confirmer avant d'agir.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _ajouter_matiere(user_id, programme_id, nom, limites or None)
        if ligne is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Matière ajoutée (id {ligne['id']}) : {ligne['nom']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_matiere : {e}")
        return "Erreur : impossible d'ajouter cette matière, réessaie."


@mcp_espace.tool()
def modifier_matiere(matiere_id: str, ctx: Context, nom: str = "", limites: str = "") -> str:
    """
    Modifie le nom et/ou les limites de cadre officiel d'une matière
    existante de CET utilisateur. Laisse un champ vide ("") pour ne pas
    le changer.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _modifier_matiere(user_id, matiere_id, nom or None, limites if limites else None)
        if ligne is None:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Matière modifiée : {ligne.get('nom')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_matiere : {e}")
        return "Erreur : impossible de modifier cette matière, réessaie."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_matiere(matiere_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT une matière de CET utilisateur, avec tous
    ses chapitres/documents/exercices. SENSIBLE : demande toujours
    confirmation avant exécution.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_matiere(user_id, matiere_id)
        if not ok:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Matière supprimée, avec tout son contenu."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_matiere : {e}")
        return "Erreur : impossible de supprimer cette matière, réessaie."


@mcp_espace.tool()
def ajouter_chapitre(matiere_id: str, nom: str, ctx: Context, ordre: int = 0, limites: str = "") -> str:
    """
    Ajoute un chapitre à une matière existante de CET utilisateur.
    `ordre` contrôle sa position d'affichage (0 = premier). `limites`
    est une description optionnelle du cadre officiel pour ce chapitre.
    N'utilise JAMAIS cet outil sur une supposition -- si l'utilisateur
    n'a pas clairement demandé d'ajouter CE chapitre à CETTE matière,
    demande-lui de confirmer avant d'agir.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _ajouter_chapitre(user_id, matiere_id, nom, ordre, limites or None)
        if ligne is None:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Chapitre ajouté (id {ligne['id']}) : {ligne['nom']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_chapitre : {e}")
        return "Erreur : impossible d'ajouter ce chapitre, réessaie."


@mcp_espace.tool()
def modifier_chapitre(chapitre_id: str, ctx: Context, nom: str = "", ordre: int = -1, limites: str = "") -> str:
    """
    Modifie le nom, l'ordre d'affichage et/ou les limites d'un chapitre
    existant de CET utilisateur. Laisse `nom`/`limites` vides ("") et
    `ordre` à -1 pour ne pas changer le champ correspondant.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _modifier_chapitre(user_id, chapitre_id, nom or None, ordre if ordre >= 0 else None, limites if limites else None)
        if ligne is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Chapitre modifié : {ligne.get('nom')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_chapitre : {e}")
        return "Erreur : impossible de modifier ce chapitre, réessaie."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_chapitre(chapitre_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un chapitre de CET utilisateur, avec ses
    documents/exercices. SENSIBLE : demande toujours confirmation avant
    exécution.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_chapitre(user_id, chapitre_id)
        if not ok:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Chapitre supprimé, avec son contenu."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_chapitre : {e}")
        return "Erreur : impossible de supprimer ce chapitre, réessaie."


@mcp_espace.tool()
def ajouter_document_programme(chapitre_id: str, titre: str, url_ou_contenu: str, ctx: Context) -> str:
    """
    Ajoute un document à un chapitre du programme de CET utilisateur :
    `url_ou_contenu` est SOIT un lien (ex: une URL de cours en ligne),
    SOIT un texte direct (ex: un résumé de cours écrit dans le
    message). Pour un fichier déjà présent dans sa bibliothèque, utilise
    plutôt classer_document_dans_programme.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _ajouter_document_programme(user_id, chapitre_id, titre, url_ou_contenu)
        if ligne is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Document ajouté (id {ligne['id']}) : {ligne['titre']}"
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_document_programme : {e}")
        return "Erreur : impossible d'ajouter ce document, réessaie."


@mcp_espace.tool()
def modifier_document_programme(document_id: str, ctx: Context, titre: str = "", url_ou_contenu: str = "") -> str:
    """
    Modifie le titre et/ou le contenu (texte ou lien) d'un document
    existant du programme de CET utilisateur. Laisse un champ vide ("")
    pour ne pas le changer.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _modifier_document_programme(user_id, document_id, titre or None, url_ou_contenu or None)
        if ligne is None:
            return "Ce document est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Document modifié : {ligne.get('titre')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_document_programme : {e}")
        return "Erreur : impossible de modifier ce document, réessaie."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_document_programme(document_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un document du programme de CET utilisateur.
    SENSIBLE : demande toujours confirmation avant exécution.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_document_programme(user_id, document_id)
        if not ok:
            return "Ce document est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Document supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_document_programme : {e}")
        return "Erreur : impossible de supprimer ce document, réessaie."


@mcp_espace.tool()
def ajouter_exercice_programme(chapitre_id: str, enonce: str, ctx: Context) -> str:
    """
    Ajoute un exercice (rattaché à UN SEUL chapitre) au programme de CET
    utilisateur. Pour un exercice/devoir couvrant PLUSIEURS chapitres,
    utilise ajouter_examen à la place.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _ajouter_exercice_programme(user_id, chapitre_id, enonce)
        if ligne is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return f"Exercice ajouté (id {ligne['id']})."
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_exercice_programme : {e}")
        return "Erreur : impossible d'ajouter cet exercice, réessaie."


@mcp_espace.tool()
def modifier_exercice_programme(exercice_id: str, enonce: str, ctx: Context) -> str:
    """
    Remplace l'énoncé COMPLET d'un exercice existant du programme de CET
    utilisateur.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ligne = _modifier_exercice_programme(user_id, exercice_id, enonce)
        if ligne is None:
            return "Cet exercice est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Exercice modifié."
    except Exception as e:
        logging.error(f"ERREUR outil modifier_exercice_programme : {e}")
        return "Erreur : impossible de modifier cet exercice, réessaie."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_exercice_programme(exercice_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un exercice du programme de CET utilisateur.
    SENSIBLE : demande toujours confirmation avant exécution.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_exercice_programme(user_id, exercice_id)
        if not ok:
            return "Cet exercice est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Exercice supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_exercice_programme : {e}")
        return "Erreur : impossible de supprimer cet exercice, réessaie."


@mcp_espace.tool()
def ajouter_examen(titre: str, type: str, chapitre_ids: list[str], ctx: Context) -> str:
    """
    Crée un examen/devoir/problème composite pour CET utilisateur,
    couvrant UN OU PLUSIEURS chapitres (potentiellement de matières
    différentes, dans le même programme). `type` doit valoir "examen",
    "devoir" ou "probleme_composite". `chapitre_ids` est la liste des
    ids de chapitres concernés -- tous doivent appartenir à cet
    utilisateur.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    if type not in ("examen", "devoir", "probleme_composite"):
        return 'Erreur : `type` doit valoir "examen", "devoir" ou "probleme_composite".'
    try:
        ligne = _ajouter_examen(user_id, titre, type, chapitre_ids)
        if ligne is None:
            return "Un ou plusieurs chapitres sont introuvables, ou ne correspondent pas à cet utilisateur."
        return f"Examen créé (id {ligne['id']}) : {ligne['titre']} ({len(chapitre_ids)} chapitre(s))."
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_examen : {e}")
        return "Erreur : impossible de créer cet examen, réessaie."


@mcp_espace.tool()
def modifier_examen(examen_id: str, ctx: Context, titre: str = "", type: str = "", chapitre_ids: list[str] | None = None) -> str:
    """
    Modifie le titre, le type et/ou la liste des chapitres couverts
    d'un examen existant de CET utilisateur. Laisse `titre`/`type`
    vides ("") et `chapitre_ids` non fourni pour ne pas changer le champ
    correspondant -- fournir `chapitre_ids` REMPLACE la liste entière,
    pas un ajout.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    if type and type not in ("examen", "devoir", "probleme_composite"):
        return 'Erreur : `type` doit valoir "examen", "devoir" ou "probleme_composite".'
    try:
        ligne = _modifier_examen(user_id, examen_id, titre or None, type or None, chapitre_ids)
        if ligne is None:
            return "Cet examen est introuvable, ou un chapitre fourni ne correspond pas à cet utilisateur."
        return f"Examen modifié : {ligne.get('titre')}"
    except Exception as e:
        logging.error(f"ERREUR outil modifier_examen : {e}")
        return "Erreur : impossible de modifier cet examen, réessaie."


@mcp_espace.tool(annotations=ToolAnnotations(destructive_hint=True))
def supprimer_examen(examen_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un examen/devoir/problème composite de CET
    utilisateur (ne supprime PAS les chapitres qu'il couvrait, juste
    l'examen lui-même). SENSIBLE : demande toujours confirmation avant
    exécution.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_examen(user_id, examen_id)
        if not ok:
            return "Cet examen est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return "Examen supprimé."
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_examen : {e}")
        return "Erreur : impossible de supprimer cet examen, réessaie."


@mcp_espace.tool()
def annuler_derniere_modification(ctx: Context) -> str:
    """
    Annule le DERNIER ajout ou la dernière modification de programme
    faite par toi (via ajouter_programme, modifier_matiere,
    ajouter_chapitre, etc.) pour CET utilisateur -- ne concerne PAS les
    suppressions, qui demandent déjà une confirmation avant d'être
    exécutées. À utiliser quand l'utilisateur dit explicitement vouloir
    annuler/revenir en arrière sur ta dernière écriture.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        resultat = _annuler_derniere_modification(user_id)
        if resultat is None:
            return "Rien à annuler : aucune modification récente trouvée."
        return f"Dernière modification annulée ({resultat['type_cible']}, action initiale : {resultat['action']})."
    except Exception as e:
        logging.error(f"ERREUR outil annuler_derniere_modification : {e}")
        return "Erreur : impossible d'annuler, réessaie."


@mcp_espace.tool()
def consulter_matiere_programme(matiere_id: str, ctx: Context) -> str:
    """
    Lit les chapitres (avec leurs limites de cadre officiel si
    renseignées) d'UNE matière précise d'un programme de cet
    utilisateur, à partir de son id. Ne contient PAS le contenu des
    chapitres (documents/exercices) : une fois que tu as choisi un
    chapitre précis dans cette liste, utilise
    consulter_chapitre_programme. Utilise cet outil seulement après
    avoir consulté consulter_programme et choisi la matière qui
    t'intéresse -- jamais à l'aveugle sans connaître l'id de la matière
    au préalable.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        chapitres = _obtenir_chapitres_matiere(user_id, matiere_id)
        if chapitres is None:
            return "Cette matière est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return chapitres
    except Exception as e:
        logging.error(f"ERREUR outil consulter_matiere_programme : {e}")
        return "Erreur : impossible de consulter cette matière, réessaie."


@mcp_espace.tool()
def consulter_chapitre_programme(chapitre_id: str, ctx: Context) -> str:
    """
    Lit le contenu réel (documents + exercices) d'UN chapitre précis
    d'un programme de cet utilisateur, à partir de son id. Utilise cet
    outil seulement après avoir consulté consulter_matiere_programme et
    choisi le chapitre qui t'intéresse dans sa liste -- jamais à
    l'aveugle sans connaître l'id du chapitre au préalable.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        contenu = _obtenir_contenu_chapitre(user_id, chapitre_id)
        if contenu is None:
            return "Ce chapitre est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return contenu
    except Exception as e:
        logging.error(f"ERREUR outil consulter_chapitre_programme : {e}")
        return "Erreur : impossible de consulter ce chapitre, réessaie."


@mcp_espace.tool()
def consulter_examens_programme(programme_id: str, ctx: Context) -> str:
    """
    Lit les examens/devoirs (titre, type, chapitres couverts) d'un
    programme de cet utilisateur, à partir de son id. Un examen peut
    couvrir plusieurs chapitres à la fois, c'est pourquoi il se
    consulte au niveau du programme entier et non via
    consulter_chapitre_programme. Aucun contenu/énoncé détaillé n'existe
    pour un examen -- seulement son titre, son type et les chapitres
    qu'il couvre.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        texte = _obtenir_examens_programme(user_id, programme_id)
        if texte is None:
            return "Ce programme est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
        return texte
    except Exception as e:
        logging.error(f"ERREUR outil consulter_examens_programme : {e}")
        return "Erreur : impossible de consulter les examens de ce programme, réessaie."


# --- Discuter avec Clovis ------------------------------------------------
# Ajouté le 17/08/2026 (demande Bourama) : jusqu'ici ce fichier ne gérait
# que les données annexes de Clovis (bibliothèque/mémoire/comportements/
# historique/programme) -- rien ne permettait à Claude d'envoyer un
# message dans une conversation et de voir la réponse réelle de Clovis,
# comme le ferait l'utilisateur depuis l'app. C'est ce que couvrent
# discuter_avec_clovis et confirmer_action_clovis ci-dessous, en
# réutilisant tel quel core.main.chat() (même fonction que api/chat.py,
# aucune logique dupliquée).
#
# Point d'attention traité ici (voir migrations/2026_08_17_confirmations_mcp_espace.sql
# et core/confirmations_mcp.py) : quand Clovis veut utiliser un outil
# sensible en répondant, chat() s'arrête et renvoie un evenement
# "confirmation_requise" contenant etat_reprise -- qui embarque
# table_routage, donc des secrets en clair (clé API Tavily, jetons
# Notion/GitHub). Cet état ne sort JAMAIS de ce fichier : il est stocké
# côté serveur (confirmations_mcp_espace), et seul un id + un résumé
# lisible sont renvoyés à Claude.
#
# LIMITE CONNUE (héritée de core/main.py, pas propre à ce fichier) : le
# chemin de reprise après confirmation ne persiste PAS l'échange dans
# historique_conversations/conversations (voir docstring de chat(),
# paramètre reprise) -- une conversation qui passe par une confirmation
# n'apparaîtra donc pas complètement dans lister_conversations_historique/
# lire_conversation_historique pour son dernier échange.

def _historique_pour_conversation(conversation_id: str, user_id: str) -> list[dict]:
    """
    Recharge l'historique d'un fil au format attendu par chat()
    (liste de {"role", "content"}) -- même requête que
    lire_conversation_historique, sans le formatage texte.
    """
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
        logging.error(f"ERREUR _historique_pour_conversation : {e}")
        return []
    return [{"role": l["role"], "content": l["content"]} for l in lignes]


def _derouler_chat(**kwargs_chat) -> tuple[str, dict | None]:
    """
    Consomme entièrement le générateur chat() et renvoie soit
    (texte_final, None), soit ("", evenement_confirmation) si Clovis
    s'est arrêté pour demander une confirmation avant d'aller plus loin.
    """
    reponse = []
    for evenement in _chat_generateur(**kwargs_chat):
        if evenement.get("type") == "reponse":
            reponse.append(evenement.get("texte", ""))
        elif evenement.get("type") == "confirmation_requise":
            return "", evenement
        # les autres types (statut, outil_resultat, raisonnement, sources,
        # meta...) ne sont pas utiles à Claude ici, volontairement ignorés
    return "".join(reponse), None


@mcp_espace.tool()
def discuter_avec_clovis(message: str, ctx: Context, conversation_id: str = "") -> str:
    """
    Envoie un message à Clovis et renvoie sa vraie réponse, exactement
    comme si cet utilisateur avait tapé ce message dans l'app -- pas une
    simulation. `conversation_id` optionnel : fourni, continue ce fil
    précis (l'historique est rechargé automatiquement, inutile d'appeler
    lire_conversation_historique avant) ; absent, démarre un nouveau fil
    (son id est indiqué au début de la réponse pour pouvoir continuer la
    discussion ensuite).

    Si Clovis veut utiliser un outil sensible pour répondre, cet outil
    s'arrête et te demande de confirmer via confirmer_action_clovis avant
    de continuer -- toujours redemander confirmation à l'utilisateur
    humain dans ce cas, ne jamais décider seul.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    message = (message or "").strip()
    if not message:
        return "Erreur : message vide."

    nouveau_fil = not conversation_id
    conv_id = conversation_id or str(uuid.uuid4())
    historique = [] if nouveau_fil else _historique_pour_conversation(conv_id, user_id)

    try:
        texte, confirmation = _derouler_chat(
            message_utilisateur=message,
            historique=historique,
            user_id=user_id,
            agent_id=AGENT_ID_ESPACE,
            conversation_id=conv_id,
        )
    except Exception as e:
        logging.error(f"ERREUR outil discuter_avec_clovis : {e}")
        return "Erreur : Clovis n'a pas pu répondre, réessaie."

    entete = f"[conversation_id: {conv_id}{' (nouveau fil)' if nouveau_fil else ''}]\n\n"

    if confirmation:
        id_confirmation = _creer_confirmation(
            proprietaire_id=user_id,
            nom_outil=confirmation.get("nom_outil", ""),
            message=confirmation.get("message", ""),
            arguments=confirmation.get("arguments", {}),
            etat_reprise=confirmation.get("etat_reprise", {}),
        )
        if not id_confirmation:
            return entete + "Erreur : Clovis voulait demander une confirmation mais elle n'a pas pu être enregistrée, réessaie."
        return (
            entete
            + f"{confirmation.get('message', 'Clovis veut effectuer une action.')}\n"
            + f"Arguments : {confirmation.get('arguments', {})}\n\n"
            + f"Demande confirmation à l'utilisateur, puis utilise confirmer_action_clovis "
            + f"avec id_confirmation=\"{id_confirmation}\" et approuve=true/false."
        )

    return entete + (texte or "(Clovis n'a rien répondu.)")


@mcp_espace.tool()
def confirmer_action_clovis(id_confirmation: str, approuve: bool, ctx: Context) -> str:
    """
    Confirme ou annule une action que Clovis voulait effectuer, signalée
    par discuter_avec_clovis (id_confirmation fourni à ce moment-là).
    `approuve` : true pour laisser Clovis exécuter l'action et continuer
    sa réponse, false pour l'annuler (Clovis répond alors sans
    l'utiliser). Ne jamais mettre approuve=true sans confirmation
    explicite de l'utilisateur humain.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    ligne = _recuperer_confirmation(id_confirmation, user_id)
    if not ligne:
        return "Erreur : cette confirmation est introuvable, déjà traitée, ou a expiré (15 min)."

    try:
        texte, confirmation = _derouler_chat(reprise={"etat_reprise": ligne["etat_reprise"], "approuve": approuve})
    except Exception as e:
        logging.error(f"ERREUR outil confirmer_action_clovis : {e}")
        return "Erreur : la reprise a échoué, réessaie."
    finally:
        _supprimer_confirmation(id_confirmation)  # usage unique, dans tous les cas

    if confirmation:
        id_suivant = _creer_confirmation(
            proprietaire_id=user_id,
            nom_outil=confirmation.get("nom_outil", ""),
            message=confirmation.get("message", ""),
            arguments=confirmation.get("arguments", {}),
            etat_reprise=confirmation.get("etat_reprise", {}),
        )
        if not id_suivant:
            return "Erreur : Clovis voulait redemander une confirmation mais elle n'a pas pu être enregistrée, réessaie."
        return (
            f"{confirmation.get('message', 'Clovis veut effectuer une autre action.')}\n"
            + f"Arguments : {confirmation.get('arguments', {})}\n\n"
            + f"Demande confirmation à l'utilisateur, puis utilise confirmer_action_clovis "
            + f"avec id_confirmation=\"{id_suivant}\" et approuve=true/false."
        )

    return texte or "(Clovis n'a rien répondu.)"
