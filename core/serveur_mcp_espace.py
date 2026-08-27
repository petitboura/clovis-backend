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

consulter_bibliotheque (recherche RAG dans la bibliothèque perso) et
lire_document_bibliotheque_en_entier existaient déjà côté agent interne
(core/serveur_mcp_generation.py) -- dupliqués ici le 18/08/2026 (demande
explicite de Bourama), même chose pour consulter_comportement,
chercher_dans_base_connaissances, lire_article_connaissance,
liste_articles_connaissance et consulter_matiere_active. Ce fichier
couvre donc désormais : bibliothèque (recherche + gestion complète),
mémoire (lire/modifier/effacer), comportements (lister/consulter/
ajouter/modifier/supprimer), historique (lecture seule, fils de
conversation), base de connaissances de l'agent (lecture seule), et
matière active débloquée par l'utilisateur.

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

import requests

from mcp.server.mcpserver import MCPServer as FastMCP, Context, Image
from mcp.types import ToolAnnotations
from supabase import create_client

from core.mcp_auth_public import (
    VerificateurJetonSupabase,
    construire_auth_settings,
    user_id_depuis_contexte as _user_id_verifie,
)
from core.pages_notion_llm import (
    lister_mes_pages_racines_legeres as _lister_mes_pages_racines_legeres,
    obtenir_page as _obtenir_page,
    ajouter_page as _ajouter_page_notion,
    modifier_page as _modifier_page_notion,
    supprimer_page as _supprimer_page_notion,
    ajouter_bloc as _ajouter_bloc_notion,
    modifier_bloc as _modifier_bloc_notion,
    supprimer_bloc as _supprimer_bloc_notion,
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
    indexer_transcription_bibliotheque as _indexer_transcription_bibliotheque,
    chercher_bibliotheque as _chercher_bibliotheque,
    lire_document_bibliotheque_en_entier as _lire_document_bibliotheque_en_entier,
    formater_source_bibliotheque as _formater_source_bibliotheque,
)
from core.description_multimedia import (
    decrire_image_bibliotheque as _decrire_image_bibliotheque,
    transcrire_audio_bibliotheque as _transcrire_audio_bibliotheque,
)
from core.generation_images import generer_image as _generer_image
from core.codes_partage import (
    propager_fichier_bibliotheque as _propager_fichier_bibliotheque,
    propager_lien_bibliotheque as _propager_lien_bibliotheque,
    obtenir_comportement_skill_recu as _obtenir_comportement_skill_recu,
)
from core.comportements_etudiants import (
    lister_comportements as _lister_comportements,
    ajouter_comportement as _ajouter_comportement,
    modifier_comportement as _modifier_comportement,
    attacher_comportement as _attacher_comportement,
    supprimer_comportement as _supprimer_comportement,
    obtenir_comportement_skill as _obtenir_comportement_skill,
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
from core.dossiers_bibliotheque import (
    _proprietaire_dossier,
    creer_dossier as _creer_dossier,
    lister_dossiers as _lister_dossiers,
    lister_fichiers_ids_dossier as _lister_fichiers_ids_dossier,
    ranger_fichier as _ranger_fichier,
    renommer_dossier as _renommer_dossier,
    retirer_fichier as _retirer_fichier,
    supprimer_dossier as _supprimer_dossier,
)
from main import chat as _chat_generateur  # core/main.py:chat() -- import bare comme dans api/chat.py (core/ deja sur sys.path a ce point, voir api/main.py : api.chat importe avant core.serveur_mcp_espace)
from core.confirmations_mcp import (
    creer_confirmation as _creer_confirmation,
    recuperer_confirmation as _recuperer_confirmation,
    supprimer_confirmation as _supprimer_confirmation,
)
from retriever import chercher_candidats as _chercher_candidats
from contenu_dynamique_matiere import resoudre_system_prompt as _resoudre_system_prompt_matiere

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

# RAPPEL NON NEGOCIABLE (Bourama, 18/08) -- POUR NE PAS OUBLIER :
# tout NOUVEL outil ajoute n'importe ou dans ce depot (ici, dans
# core/serveur_mcp_generation.py, ou ailleurs) doit systematiquement
# faire l'objet d'une question explicite a Bourama : "cet outil doit-il
# aussi etre expose sur le serveur MCP PUBLIC (ce fichier, mcp_espace) ?"
# -- jamais suppose oui, jamais suppose non, jamais ajoute ici sans
# validation prealable. Objectif : ne jamais en oublier un par
# inattention au fil des sessions futures.


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
# consulter_bibliotheque et lire_document_bibliotheque_en_entier
# existaient déjà côté agent interne (core/serveur_mcp_generation.py),
# dupliqués ici le 18/08 (demande explicite de Bourama) pour qu'un
# client MCP externe puisse aussi chercher par contenu dans la
# bibliothèque, pas seulement lister/gérer ses entrées.

@mcp_espace.tool(
    name="clovis_consulter_bibliotheque",
    title="Consulter la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def consulter_bibliotheque(question: str, ctx: Context) -> str:
    """
    Cherche dans la bibliothèque personnelle de documents de cet
    utilisateur (section "Bibliothèque" de "Mon espace") les passages
    les plus pertinents pour répondre à `question`. Renvoie les extraits
    trouvés, chacun accompagné du nom et du lien de son document
    d'origine -- si tu juges utile de montrer un document en entier
    plutôt que de le résumer, inclus son lien dans ta réponse
    (![...](url) pour une image, [...](url) pour les autres types).
    Renvoie un message si rien de pertinent n'a été trouvé.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        resultats = _chercher_bibliotheque(question, user_id=user_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_bibliotheque : {e}")
        return "Erreur : la recherche dans la bibliothèque a échoué, réessaie."
    if not resultats:
        return "Rien de pertinent trouvé dans la bibliothèque pour cette question."
    blocs = []
    for r in resultats:
        bloc = r["contenu"]
        source = _formater_source_bibliotheque(r)
        if source:
            bloc += f"\n{source}"
        blocs.append(bloc)
    return "\n\n---\n\n".join(blocs)


@mcp_espace.tool(
    name="clovis_lire_document_bibliotheque_en_entier",
    title="Lire un document en entier",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lire_document_bibliotheque_en_entier(fichier_id: str, ctx: Context) -> str:
    """
    Renvoie le texte COMPLET d'un document de la bibliothèque
    personnelle, identifié par son `fichier_id` (vu via
    consulter_bibliotheque ou lister_bibliotheque). À utiliser quand les
    extraits de consulter_bibliotheque ne suffisent pas et qu'il te faut
    le contenu intégral du document.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        texte = _lire_document_bibliotheque_en_entier(fichier_id, user_id=user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lire_document_bibliotheque_en_entier : {e}")
        return "Erreur : impossible de lire ce document, réessaie."
    if texte is None:
        return "Document introuvable, ou ne t'appartient pas."
    return texte


@mcp_espace.tool(
    name="clovis_lister_bibliotheque",
    title="Lister la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_bibliotheque(ctx: Context, limit: int = 20, offset: int = 0) -> str:
    """
    Liste les documents/liens/notes de la bibliothèque personnelle de cet
    utilisateur (section "Bibliothèque" de "Mon espace"), sans effectuer
    de recherche par contenu (voir consulter_bibliotheque pour ça).
    Renvoie pour chaque entrée : id, description, type, date d'ajout.
    Résultats paginés : `limit` (défaut 20, max 100) entrées à partir de
    `offset` (défaut 0). Si d'autres entrées existent au-delà, un rappel
    est ajouté en fin de réponse avec l'offset suivant à utiliser.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    try:
        fichiers = _lister_fichiers("utilisateur", user_id=user_id, origine="bibliotheque")
    except Exception as e:
        logging.error(f"ERREUR outil lister_bibliotheque : {e}")
        return "Erreur : impossible de lister la bibliothèque, réessaie."
    if not fichiers:
        return "Bibliothèque vide pour l'instant."
    total = len(fichiers)
    page = fichiers[offset:offset + limit]
    lignes = []
    for f in page:
        ligne = (
            f"- {f.get('description') or f.get('nom_fichier')} "
            f"({f.get('type_mime', 'inconnu')}, ajouté le {f.get('created_at', '?')})"
        )
        emplacements = _lister_emplacements_document(f["id"])
        if emplacements:
            ligne += " | classé dans : " + ", ".join(e["libelle"] for e in emplacements)
        ligne += f" [id: {f['id']}]"
        lignes.append(ligne)
    resultat = "\n".join(lignes)
    if offset + limit < total:
        resultat += (
            f"\n\n({offset + 1}-{offset + len(page)} sur {total}. "
            f"Pour la suite : offset={offset + limit}.)"
        )
    return resultat


@mcp_espace.tool(
    name="clovis_ajouter_lien_bibliotheque",
    title="Ajouter un lien à la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_texte_bibliotheque",
    title="Ajouter une note à la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_document_bibliotheque",
    title="Ajouter un fichier à la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def ajouter_document_bibliotheque(
    nom_fichier: str, type_mime: str, ctx: Context,
    titre: str = "", description: str = "",
    contenu_base64: str = "", url_fichier: str = "",
    type_emplacement: str = "", emplacement_id: str = "",
) -> str:
    """
    Ajoute un fichier (PDF, image, audio ou vidéo) à la bibliothèque
    personnelle de cet utilisateur -- même effet que s'il l'avait
    uploadé lui-même depuis "Mon espace".

    IMPORTANT -- ne jamais demander nom_fichier ni type_mime à
    l'utilisateur, ce sont des détails techniques qu'il n'a pas à
    connaître : déduis-les TOUJOURS toi-même du contexte déjà présent
    dans la conversation. `nom_fichier` (avec son extension, ex.
    "cours_svt.pdf") : reprends le nom donné entre crochets juste après
    un upload ("[Document joint : cours_svt.pdf]", "[Image jointe :
    ...]", etc.), ou à défaut l'extension visible à la fin de
    `url_fichier` lui-même. `type_mime` (ex. "application/pdf",
    "image/png", "audio/mpeg", "video/mp4") : déduis-le de cette même
    extension (mapping standard extension -> type MIME) -- n'importe
    quel type de fichier est accepté, pas seulement ceux cités en
    exemple. Ces deux champs restent obligatoires pour l'outil, mais
    c'est TOI qui les remplis, jamais l'utilisateur.

    SI AUCUN FICHIER N'A ÉTÉ JOINT DU TOUT dans cette conversation
    (aucun "[Document joint : ...]", "[Image jointe : ...]", etc. --
    donc ni nom ni URL disponibles nulle part) : n'appelle PAS cet
    outil et ne demande surtout pas de lien ou de contenu base64 (trop
    technique). Dis simplement à l'utilisateur d'uploader/joindre le
    fichier dans la conversation, rien d'autre -- une fois joint, tu
    pourras l'ajouter directement sans lui redemander quoi que ce soit.

    Fournir SOIT `url_fichier` SOIT `contenu_base64` (jamais les deux à
    vide) : `url_fichier` -- lien réel d'un fichier déjà joint dans
    CETTE conversation (celui donné entre crochets "[Lien réel du
    fichier : ...]" après un upload chat) -- à privilégier
    systématiquement quand ce lien est disponible, le fichier est alors
    récupéré directement par le serveur, sans jamais faire transiter son
    contenu par le modèle. `contenu_base64` -- contenu du fichier encodé
    en base64 (jamais de contenu brut binaire), seulement si aucun lien
    réel n'existe déjà (fichier généré ou fourni autrement). `titre`/
    `description` : vraiment optionnels, propose-les si tu veux mais ne
    bloque jamais dessus -- repli automatique sur le nom du fichier si
    absents. Limite : 50 Mo. `type_emplacement`/`emplacement_id` :
    optionnels -- si fournis
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

    url_fichier = (url_fichier or "").strip()
    contenu_base64 = (contenu_base64 or "").strip()
    if not url_fichier and not contenu_base64:
        return "Erreur : fournis url_fichier (lien réel d'un fichier déjà joint dans la conversation) ou contenu_base64."

    if url_fichier:
        try:
            reponse = requests.get(url_fichier, timeout=30)
            reponse.raise_for_status()
            contenu = reponse.content
        except Exception as e:
            logging.error(f"ERREUR outil ajouter_document_bibliotheque (url_fichier={url_fichier}) : {e}")
            return "Erreur : impossible de récupérer le fichier à cette URL."
    else:
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
            segments_audio = _transcrire_audio_bibliotheque(contenu, nom_original)
            if segments_audio:
                _indexer_transcription_bibliotheque(segments_audio, fichier_id=ligne["id"], user_id=user_id)
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


@mcp_espace.tool(
    name="clovis_supprimer_document_bibliotheque",
    title="Supprimer un document de la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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

@mcp_espace.tool(
    name="clovis_classer_document_dans_programme",
    title="Classer un document dans le programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_retirer_document_du_programme",
    title="Retirer un document du programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


# --- Dossiers de la bibliothèque personnelle (22/08, demande Bourama) --
# Organisation en dossiers/sous-dossiers, séparée du classement dans le
# programme ci-dessus. Un fichier peut être dans plusieurs dossiers à la
# fois. Voir core/dossiers_bibliotheque.py pour la logique complète et
# le comportement de suppression (un fichier plus rattaché à aucun autre
# dossier est supprimé avec le dossier, confirmé par Bourama).

@mcp_espace.tool(
    name="clovis_lister_dossiers_bibliotheque",
    title="Lister les dossiers de la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_dossiers_bibliotheque(ctx: Context) -> str:
    """
    Liste tous les dossiers de la bibliothèque personnelle de cet
    utilisateur, avec leur arborescence (dossier parent) et le nombre de
    fichiers directement rangés dans chacun. Pour voir le CONTENU d'un
    dossier précis (fichiers + sous-dossiers), utilise
    consulter_dossier_bibliotheque avec son id.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        dossiers = _lister_dossiers(user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lister_dossiers_bibliotheque : {e}")
        return "Erreur : impossible de lister les dossiers, réessaie."
    if not dossiers:
        return "Aucun dossier pour l'instant."
    par_id = {d["id"]: d for d in dossiers}
    lignes = []
    for d in dossiers:
        parent = par_id.get(d["dossier_parent_id"])
        chemin = f"{parent['nom']} > {d['nom']}" if parent else d["nom"]
        nb_fichiers = len(_lister_fichiers_ids_dossier(d["id"]))
        lignes.append(f"- {chemin} [id: {d['id']}] ({nb_fichiers} fichier(s) direct(s))")
    return "\n".join(lignes)


@mcp_espace.tool(
    name="clovis_consulter_dossier_bibliotheque",
    title="Consulter un dossier de la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def consulter_dossier_bibliotheque(dossier_id: str, ctx: Context) -> str:
    """
    Liste le contenu direct d'un dossier précis de la bibliothèque
    personnelle : ses sous-dossiers et ses fichiers (description, type,
    id). Ne descend pas récursivement dans les sous-dossiers, rappelle
    cet outil avec l'id d'un sous-dossier pour y entrer.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        return "Ce dossier est introuvable."
    if proprietaire != user_id:
        return "Ce dossier ne t'appartient pas."
    try:
        dossiers = _lister_dossiers(user_id)
        sous_dossiers = [d for d in dossiers if d["dossier_parent_id"] == dossier_id]
        fichier_ids = _lister_fichiers_ids_dossier(dossier_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_dossier_bibliotheque : {e}")
        return "Erreur : impossible de consulter ce dossier, réessaie."

    lignes = []
    for sd in sous_dossiers:
        lignes.append(f"- [dossier] {sd['nom']} [id: {sd['id']}]")
    for f_id in fichier_ids:
        try:
            res = _supabase.table("fichiers_uploades").select("nom_fichier, description, type_mime").eq("id", f_id).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR outil consulter_dossier_bibliotheque (lecture fichier {f_id}) : {e}")
            continue
        if not res or not res.data:
            continue
        f = res.data
        lignes.append(f"- [fichier] {f.get('description') or f.get('nom_fichier')} ({f.get('type_mime', 'inconnu')}) [id: {f_id}]")
    if not lignes:
        return "Ce dossier est vide."
    return "\n".join(lignes)


@mcp_espace.tool(
    name="clovis_ajouter_dossier_bibliotheque",
    title="Créer un dossier dans la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def ajouter_dossier_bibliotheque(nom: str, dossier_parent_id: str = "", ctx: Context = None) -> str:
    """
    Crée un dossier dans la bibliothèque personnelle de cet utilisateur.
    `dossier_parent_id` (optionnel) : id d'un dossier existant pour créer
    un SOUS-dossier dedans ; laisse vide pour un dossier à la racine.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    nom = (nom or "").strip()
    if not nom:
        return "Erreur : nom de dossier manquant."
    parent_id = (dossier_parent_id or "").strip() or None
    if parent_id:
        proprietaire = _proprietaire_dossier(parent_id)
        if proprietaire is None:
            return "Erreur : le dossier parent indiqué est introuvable."
        if proprietaire != user_id:
            return "Erreur : ce dossier parent ne t'appartient pas."
    try:
        dossier = _creer_dossier(user_id, nom, parent_id)
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_dossier_bibliotheque : {e}")
        return "Erreur : impossible de créer ce dossier, réessaie."
    return f"Dossier « {nom} » créé [id: {dossier['id']}]."


@mcp_espace.tool(
    name="clovis_renommer_dossier_bibliotheque",
    title="Renommer un dossier de la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def renommer_dossier_bibliotheque(dossier_id: str, nouveau_nom: str, ctx: Context) -> str:
    """Renomme un dossier de la bibliothèque personnelle de cet utilisateur."""
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        return "Ce dossier est introuvable."
    if proprietaire != user_id:
        return "Ce dossier ne t'appartient pas."
    nouveau_nom = (nouveau_nom or "").strip()
    if not nouveau_nom:
        return "Erreur : nouveau nom manquant."
    try:
        _renommer_dossier(dossier_id, nouveau_nom)
    except Exception as e:
        logging.error(f"ERREUR outil renommer_dossier_bibliotheque : {e}")
        return "Erreur : impossible de renommer ce dossier, réessaie."
    return f"Dossier renommé en « {nouveau_nom} »."


@mcp_espace.tool(
    name="clovis_ranger_fichier_dans_dossier",
    title="Ranger un fichier dans un dossier",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def ranger_fichier_dans_dossier(fichier_id: str, dossier_id: str, ctx: Context) -> str:
    """
    Range un fichier de la bibliothèque personnelle dans un dossier. Un
    fichier peut être rangé dans plusieurs dossiers à la fois : ranger
    un fichier déjà présent ailleurs l'ajoute simplement à ce dossier en
    plus, sans le retirer des autres.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        return "Ce dossier est introuvable."
    if proprietaire != user_id:
        return "Ce dossier ne t'appartient pas."
    try:
        res = _supabase.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR outil ranger_fichier_dans_dossier (lecture fichier) : {e}")
        return "Erreur : impossible de ranger ce fichier, réessaie."
    if not res or not res.data:
        return "Ce fichier est introuvable."
    if res.data["user_id"] != user_id:
        return "Ce fichier ne t'appartient pas."
    try:
        _ranger_fichier(fichier_id, dossier_id)
    except Exception as e:
        logging.error(f"ERREUR outil ranger_fichier_dans_dossier : {e}")
        return "Erreur : impossible de ranger ce fichier, réessaie."
    return "Fichier rangé dans le dossier."


@mcp_espace.tool(
    name="clovis_retirer_fichier_du_dossier",
    title="Retirer un fichier d'un dossier",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def retirer_fichier_du_dossier(fichier_id: str, dossier_id: str, ctx: Context) -> str:
    """
    Retire un fichier d'un dossier précis (le fichier reste dans la
    bibliothèque et dans ses autres dossiers éventuels : seul ce
    rattachement précis disparaît).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        return "Ce dossier est introuvable."
    if proprietaire != user_id:
        return "Ce dossier ne t'appartient pas."
    try:
        _retirer_fichier(fichier_id, dossier_id)
    except Exception as e:
        logging.error(f"ERREUR outil retirer_fichier_du_dossier : {e}")
        return "Erreur : impossible de retirer ce fichier, réessaie."
    return "Fichier retiré du dossier."


@mcp_espace.tool(
    name="clovis_supprimer_dossier_bibliotheque",
    title="Supprimer un dossier de la bibliothèque",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
def supprimer_dossier_bibliotheque(dossier_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un dossier de la bibliothèque personnelle
    (et ses sous-dossiers). Un fichier encore rattaché à au moins un
    autre dossier est conservé (juste détaché de celui-ci) ; un fichier
    qui n'était rattaché à AUCUN autre dossier est supprimé en même
    temps que le dossier. SENSIBLE : le client doit confirmer avec
    l'utilisateur avant d'appeler cet outil.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    proprietaire = _proprietaire_dossier(dossier_id)
    if proprietaire is None:
        return "Ce dossier est introuvable."
    if proprietaire != user_id:
        return "Ce dossier ne t'appartient pas."
    try:
        _supprimer_dossier(dossier_id)
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_dossier_bibliotheque : {e}")
        return "Erreur : impossible de supprimer ce dossier, réessaie."
    return "Dossier supprimé."


# --- Mémoire (résumé long-terme, "Ma mémoire" de "Mon espace") --------
# Colonne `summary` de conversation_summaries (celle que lit/écrit
# core/main.py::_charger_resume_memoire, celle affichée par
# MaMemoire.tsx via /api/memoire). À NE PAS confondre avec la colonne
# `donnees` (JSON) utilisée par consulter_memoire_utilisateur /
# mettre_a_jour_memoire_utilisateur dans serveur_mcp_generation.py :
# deux mécanismes distincts sur la même table, celui-ci est le seul
# visible dans "Mon espace" côté utilisateur -- découverte faite en
# auditant le dépôt (16/08), signalée à Bourama.

@mcp_espace.tool(
    name="clovis_lire_memoire",
    title="Lire la mémoire",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_memoire",
    title="Modifier la mémoire",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_effacer_memoire",
    title="Effacer la mémoire",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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

@mcp_espace.tool(
    name="clovis_consulter_comportement",
    title="Consulter un skill",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def consulter_comportement(comportement_id: str, ctx: Context) -> str:
    """
    Lit le contenu COMPLET (frontmatter + instructions) d'une instruction
    personnelle -- appelée "skill" dans TOUTE l'interface Clovis,
    "comportement" seulement en interne -- que cet utilisateur l'ait
    écrite lui-même (section "Mes comportements"), ou qu'il l'ait reçue
    d'un autre utilisateur via un code (id préfixé "recu:") -- à partir
    de son id, vu via lister_comportements.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        if comportement_id.startswith("recu:"):
            skill_md = _obtenir_comportement_skill_recu(user_id, comportement_id)
        else:
            skill_md = _obtenir_comportement_skill(AGENT_ID_ESPACE, user_id, comportement_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_comportement : {e}")
        return "Erreur : impossible de consulter ce comportement, réessaie."
    if skill_md is None:
        return "Ce comportement est introuvable (id invalide, ou ne t'appartient pas)."
    return skill_md


@mcp_espace.tool(
    name="clovis_lister_comportements",
    title="Lister les skills",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_comportements(ctx: Context, limit: int = 20, offset: int = 0) -> str:
    """
    Liste les instructions personnelles que cet utilisateur a écrites
    lui-même (section "Mes comportements" de "Mon espace") pour Clovis --
    appelées "skill(s)" dans TOUTE l'interface Clovis, "comportement"
    seulement en interne. Utilise cet outil dès que l'utilisateur demande
    "mes skills", "quels sont mes skills", "montre-moi mes skills/mes
    comportements", etc. -- une vraie demande d'énumération, à ne pas
    confondre avec ses compétences/talents personnels (aucun rapport).
    Renvoie pour chacune : id, description courte, emplacement lié le
    cas échéant -- PAS le texte complet (utilise consulter_comportement
    avec l'id pour lire un comportement précis en entier).
    Résultats paginés : `limit` (défaut 20, max 100) entrées à partir de
    `offset` (défaut 0). Si d'autres entrées existent au-delà, un rappel
    est ajouté en fin de réponse avec l'offset suivant à utiliser.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    try:
        comportements = _lister_comportements(AGENT_ID_ESPACE, user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lister_comportements : {e}")
        return "Erreur : impossible de lister les comportements, réessaie."
    if not comportements:
        return "Aucun comportement enregistré pour l'instant."
    total = len(comportements)
    page = comportements[offset:offset + limit]
    lignes = []
    for c in page:
        ligne = f"- {c['description']}"
        if c.get("lien_type") and c.get("lien_id"):
            libelle = _libelle_emplacement(c["lien_type"], c["lien_id"]) if c["lien_type"] in TYPES_LIEN_COMPORTEMENT else None
            ligne += f"\n  lié à : {libelle or (c['lien_type'] + ' ' + c['lien_id'])}"
        ligne += f"\n  [id: {c['id']}]"
        lignes.append(ligne)
    resultat = "\n".join(lignes)
    if offset + limit < total:
        resultat += (
            f"\n\n({offset + 1}-{offset + len(page)} sur {total}. "
            f"Pour la suite : offset={offset + limit}.)"
        )
    return resultat


@mcp_espace.tool(
    name="clovis_ajouter_comportement_espace",
    title="Ajouter un skill",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def ajouter_comportement_espace(texte: str, ctx: Context, type_lien: str = "", lien_id: str = "") -> str:
    """
    Enregistre une nouvelle instruction personnelle pour cet utilisateur
    (section "Mes comportements", appelée "skill" dans l'interface).
    S'ajoute EN PLUS des comportements déjà existants, ne les remplace
    pas. `type_lien`/`lien_id` : optionnels -- si fournis, rattache ce
    comportement à un endroit précis du programme ("programme"/"matiere"/
    "chapitre"/"document"/"exercice"/"examen" + son id), comme si
    l'utilisateur avait rempli une section dédiée à cet endroit du
    programme. Sans ces deux paramètres, comportement générique comme
    avant (s'applique partout).
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
        libelle = _libelle_emplacement(type_lien_final, lien_id_final) if type_lien_final in TYPES_LIEN_COMPORTEMENT else None
        message += f" (lié à : {libelle or (type_lien_final + ' ' + lien_id_final)})"
    return message


@mcp_espace.tool(
    name="clovis_modifier_comportement_espace",
    title="Modifier un skill",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def modifier_comportement_espace(comportement_id: str, texte: str, ctx: Context) -> str:
    """
    Remplace le texte complet d'un comportement existant de cet
    utilisateur (appelé "skill" dans l'interface), à partir de son id
    (voir lister_comportements).
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


@mcp_espace.tool(
    name="clovis_attacher_comportement_espace",
    title="Attacher/détacher un skill",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def attacher_comportement_espace(comportement_id: str, ctx: Context, type_lien: str = "", lien_id: str = "") -> str:
    """
    Attache un comportement (appelé "skill" dans l'interface) DÉJÀ
    EXISTANT à un endroit du programme
    ("programme"/"matiere"/"chapitre"/"document"/"exercice"/"examen"/
    "section" + son id) -- séparé de ajouter_comportement_espace exprès
    (20/08/2026, demande Bourama : "au moment de la création ou après
    tu peux l'attacher"). Laisse type_lien/lien_id VIDES pour détacher
    (rendre ce comportement générique à nouveau, s'applique partout).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    type_lien_final, lien_id_final = None, None
    if type_lien and lien_id:
        if type_lien not in TYPES_LIEN_COMPORTEMENT:
            return f"Erreur : type de lien invalide, utilise l'un de {TYPES_LIEN_COMPORTEMENT}."
        if _proprietaire_lien_comportement(type_lien, lien_id) != user_id:
            return "Erreur : cet emplacement du programme est introuvable ou ne t'appartient pas."
        type_lien_final, lien_id_final = type_lien, lien_id
    try:
        ligne = _attacher_comportement(AGENT_ID_ESPACE, user_id, comportement_id, type_lien_final, lien_id_final)
    except Exception as e:
        logging.error(f"ERREUR outil attacher_comportement_espace : {e}")
        return "Erreur : impossible d'attacher ce comportement, réessaie."
    if ligne is None:
        return "Ce comportement est introuvable."
    if type_lien_final:
        libelle = _libelle_emplacement(type_lien_final, lien_id_final)
        return f"Comportement attaché à : {libelle or (type_lien_final + ' ' + lien_id_final)}"
    return "Comportement détaché (redevenu générique)."


@mcp_espace.tool(
    name="clovis_supprimer_comportement_espace",
    title="Supprimer un skill",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
def supprimer_comportement_espace(comportement_id: str, ctx: Context) -> str:
    """
    Supprime DÉFINITIVEMENT un comportement de cet utilisateur (appelé
    "skill" dans l'interface), à partir de son id (voir
    lister_comportements). SENSIBLE : le client doit confirmer avec
    l'utilisateur avant d'appeler cet outil.
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


@mcp_espace.tool(
    name="clovis_lister_conversations_historique",
    title="Lister les conversations passées",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_conversations_historique(ctx: Context, limit: int = 20, offset: int = 0) -> str:
    """
    Liste les fils de discussion distincts entre cet utilisateur et
    Clovis (section "Historique"), le plus récemment actif en premier.
    Renvoie pour chacun : conversation_id ("legacy" pour les échanges
    d'avant l'historique par fil), titre (début du premier message),
    dernière activité.
    Résultats paginés : `limit` (défaut 20, max 100) entrées à partir de
    `offset` (défaut 0). Si d'autres entrées existent au-delà, un rappel
    est ajouté en fin de réponse avec l'offset suivant à utiliser.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
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
    total = len(resultats)
    page = resultats[offset:offset + limit]
    resultat = "\n".join(
        f"- {titre} (dernière activité : {activite}) [conversation_id: {cle}]"
        for cle, titre, activite in page
    )
    if offset + limit < total:
        resultat += (
            f"\n\n({offset + 1}-{offset + len(page)} sur {total}. "
            f"Pour la suite : offset={offset + limit}.)"
        )
    return resultat


@mcp_espace.tool(
    name="clovis_lire_conversation_historique",
    title="Lire une conversation passée",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


# --- Base de connaissances de l'agent (lecture seule) -------------------
# Ajouté le 18/08/2026 (demande Bourama) : mêmes outils que côté agent
# interne (core/serveur_mcp_generation.py), pour qu'un client MCP
# externe puisse aussi chercher dans le contenu préparé à l'avance par
# l'équipe Clovis (pas propre à un utilisateur -- agent_id fixe
# "clovis" comme partout ailleurs dans ce fichier).

@mcp_espace.tool(
    name="clovis_chercher_dans_base_connaissances",
    title="Chercher dans la base de connaissances",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def chercher_dans_base_connaissances(question: str, ctx: Context) -> str:
    """
    Cherche dans la base de connaissances de l'agent (documents et
    instructions spécifiques préparés par l'équipe Clovis) les passages
    pertinents pour répondre à `question`. À utiliser quand la question
    touche un sujet précis où un contenu de référence a pu être préparé
    à l'avance. Renvoie les extraits trouvés ou un message si rien de
    pertinent.
    """
    try:
        candidats = _chercher_candidats(question, agent_id=AGENT_ID_ESPACE)
        morceaux = [c["contenu"] for c in candidats.get("prompts", [])] + [
            c["contenu"] for c in candidats.get("documents", [])
        ]
    except Exception as e:
        logging.error(f"ERREUR outil chercher_dans_base_connaissances : {e}")
        return "Erreur : la recherche a échoué, réessaie."
    if not morceaux:
        return "Rien de pertinent trouvé dans la base de connaissances pour cette question."
    return "\n\n---\n\n".join(morceaux)


@mcp_espace.tool(
    name="clovis_lire_article_connaissance",
    title="Lire un article de connaissance",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lire_article_connaissance(nom: str, ctx: Context) -> str:
    """
    Renvoie le texte COMPLET (pas juste des extraits) d'un article de la
    base de connaissances de l'agent, identifié par son `nom` exact. Si
    `nom` est inconnu, utilise d'abord chercher_dans_base_connaissances
    pour l'identifier, ou liste_articles_connaissance pour voir les noms
    disponibles.
    """
    try:
        res = (
            _supabase.table("documents")
            .select("contenu, position")
            .eq("agent_id", AGENT_ID_ESPACE)
            .eq("nom", nom)
            .order("position", desc=False, nullsfirst=False)
            .execute()
        )
        morceaux = res.data or []
    except Exception as e:
        logging.error(f"ERREUR outil lire_article_connaissance : {e}")
        return "Erreur : la lecture de l'article a échoué, réessaie."
    if not morceaux:
        return f"Aucun article nommé '{nom}' trouvé dans la base de connaissances."
    return " ".join(m["contenu"] for m in morceaux)


@mcp_espace.tool(
    name="clovis_liste_articles_connaissance",
    title="Lister les articles de connaissance",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def liste_articles_connaissance(ctx: Context) -> str:
    """
    Liste les noms de tous les articles disponibles dans la base de
    connaissances de l'agent -- à utiliser avant lire_article_connaissance
    si le nom exact de l'article recherché n'est pas connu.
    """
    try:
        res = (
            _supabase.table("documents")
            .select("nom")
            .eq("agent_id", AGENT_ID_ESPACE)
            .execute()
        )
        noms = sorted({r["nom"] for r in (res.data or [])})
    except Exception as e:
        logging.error(f"ERREUR outil liste_articles_connaissance : {e}")
        return "Erreur : la liste des articles a échoué, réessaie."
    if not noms:
        return "Aucun article dans la base de connaissances pour l'instant."
    return "\n".join(noms)


@mcp_espace.tool(
    name="clovis_obtenir_fichier_connaissance",
    title="Obtenir le fichier d'un article",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def obtenir_fichier_connaissance(nom: str, ctx: Context) -> str:
    """
    Renvoie le FICHIER original (pas son texte recopié) d'un article de
    la base de connaissances, sous forme d'un lien vers le fichier tel
    qu'il a été déposé -- à utiliser quand l'utilisateur veut le fichier
    lui-même en pièce jointe, pas juste lire son contenu (pour ça,
    lire_article_connaissance). Si `nom` est inconnu, utilise
    liste_articles_connaissance pour voir les noms disponibles.
    """
    try:
        res = (
            _supabase.table("documents")
            .select("nom")
            .eq("agent_id", AGENT_ID_ESPACE)
            .eq("nom", nom)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR outil obtenir_fichier_connaissance : {e}")
        return "Erreur : la récupération du fichier a échoué, réessaie."
    if not res.data:
        return f"Aucun article nommé '{nom}' trouvé dans la base de connaissances."
    url = f"{_SUPABASE_URL}/storage/v1/object/public/documents-agents/{AGENT_ID_ESPACE}/{nom}"
    return f"Fichier : {url}"


# --- Contenu pédagogique débloqué (matière active) -----------------------
# Ajouté le 18/08/2026 (demande Bourama), même logique que côté agent
# interne (core/serveur_mcp_generation.py).

@mcp_espace.tool(
    name="clovis_consulter_matiere_active",
    title="Consulter la matière active",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def consulter_matiere_active(message_utilisateur: str, ctx: Context) -> str:
    """
    Consulte le contenu pédagogique spécifique (cours, consignes d'un
    enseignant) débloqué par cet utilisateur pour la matière la plus
    pertinente par rapport à `message_utilisateur`. À utiliser si la
    question ressemble à une question de cours et que l'utilisateur a pu
    débloquer une matière avec un code. Ce contenu est un COMPLÉMENT aux
    instructions habituelles, pas un remplacement. Peut renvoyer un
    message générique si aucune matière n'est débloquée.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        return _resoudre_system_prompt_matiere(message_utilisateur, AGENT_ID_ESPACE, user_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_matiere_active : {e}")
        return "Erreur : impossible de consulter le contenu de la matière, réessaie."


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

@mcp_espace.tool(
    name="clovis_lister_mes_programmes",
    title="Lister les programmes",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_consulter_programme",
    title="Consulter un programme",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_programme",
    title="Ajouter un programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_programme",
    title="Modifier un programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_supprimer_programme",
    title="Supprimer un programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_matiere",
    title="Ajouter une matière",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_matiere",
    title="Modifier une matière",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_supprimer_matiere",
    title="Supprimer une matière",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_chapitre",
    title="Ajouter un chapitre",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_chapitre",
    title="Modifier un chapitre",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_supprimer_chapitre",
    title="Supprimer un chapitre",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_document_programme",
    title="Ajouter un document au programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_document_programme",
    title="Modifier un document du programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_supprimer_document_programme",
    title="Supprimer un document du programme",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_exercice_programme",
    title="Ajouter un exercice",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_exercice_programme",
    title="Modifier un exercice",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_supprimer_exercice_programme",
    title="Supprimer un exercice",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_ajouter_examen",
    title="Ajouter un examen",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_modifier_examen",
    title="Modifier un examen",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_supprimer_examen",
    title="Supprimer un examen",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_annuler_derniere_modification",
    title="Annuler la dernière modification",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_consulter_matiere_programme",
    title="Consulter les chapitres d'une matière",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_consulter_chapitre_programme",
    title="Consulter un chapitre",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_consulter_examens_programme",
    title="Consulter les examens",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
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


# --- Section "Notion-like" (Partie 2, lot 1/5) -- navigation pages/blocs --
# Ajouté le 20/08/2026 (demande Bourama : "il faut que l'IA puisse y
# naviguer et s'orienter, pareil avec le MCP public"). Mêmes outils que
# côté agent interne (core/serveur_mcp_generation.py), logique partagée
# via core/pages_notion_llm.py -- voir ce fichier pour le choix de
# périmètre (pas d'historique/annulation pour ce lot, pas de partage par
# code).


@mcp_espace.tool(
    name="clovis_lister_mes_pages",
    title="Lister mes pages",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def lister_mes_pages(ctx: Context) -> str:
    """
    Liste légère (id, titre) des pages RACINES (sans page parente) de cet
    utilisateur, dans sa section "Notion-like". Point de départ
    obligatoire avant tout autre outil "page" s'il n'a pas déjà un id
    précis en tête. Ne contient PAS les sous-pages ni les blocs (voir
    clovis_consulter_page une fois une page choisie).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        pages = _lister_mes_pages_racines_legeres(user_id)
    except Exception as e:
        logging.error(f"ERREUR outil lister_mes_pages : {e}")
        return "Erreur : impossible de lister les pages, réessaie."
    if not pages:
        return "Aucune page créée pour l'instant."
    return "\n".join(f"- {p['titre']} (id: {p['id']})" for p in pages)


@mcp_espace.tool(
    name="clovis_consulter_page",
    title="Consulter une page",
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def consulter_page(page_id: str, ctx: Context) -> str:
    """
    Lit le contenu d'une page précise : ses sous-pages (id + titre, pour
    y naviguer ensuite) et ses blocs (id + type + texte, dans l'ordre).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        contenu = _obtenir_page(user_id, page_id)
    except Exception as e:
        logging.error(f"ERREUR outil consulter_page : {e}")
        return "Erreur : impossible de lire cette page, réessaie."
    if contenu is None:
        return "Cette page est introuvable (id invalide, ou ne correspond pas à cet utilisateur)."
    return contenu


@mcp_espace.tool(
    name="clovis_ajouter_page",
    title="Ajouter une page",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def ajouter_page(titre: str, ctx: Context, parent_id: str = "") -> str:
    """
    Crée une nouvelle page dans la section "Notion-like" de cet
    utilisateur. Si `parent_id` est fourni, la nouvelle page devient une
    sous-page de celle-ci -- sinon elle est créée à la racine.
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        page = _ajouter_page_notion(user_id, titre, parent_id or None)
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_page : {e}")
        return "Erreur : impossible de créer la page, réessaie."
    if page is None:
        return "Erreur : parent_id invalide ou ne correspond pas à cet utilisateur."
    return f"Page créée : {page['titre'] or '(sans titre)'} (id: {page['id']})."


@mcp_espace.tool(
    name="clovis_modifier_page",
    title="Modifier une page",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def modifier_page(page_id: str, titre: str, ctx: Context) -> str:
    """Renomme une page existante (id vu via clovis_lister_mes_pages ou clovis_consulter_page)."""
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        page = _modifier_page_notion(user_id, page_id, titre)
    except Exception as e:
        logging.error(f"ERREUR outil modifier_page : {e}")
        return "Erreur : impossible de modifier cette page, réessaie."
    if page is None:
        return "Cette page est introuvable ou ne correspond pas à cet utilisateur."
    return f"Page renommée : {page['titre']}."


@mcp_espace.tool(
    name="clovis_supprimer_page",
    title="Supprimer une page",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
def supprimer_page(page_id: str, ctx: Context) -> str:
    """Supprime DÉFINITIVEMENT une page, ainsi que ses sous-pages et ses blocs."""
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_page_notion(user_id, page_id)
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_page : {e}")
        return "Erreur : impossible de supprimer cette page, réessaie."
    if not ok:
        return "Cette page est introuvable ou ne correspond pas à cet utilisateur."
    return "Page supprimée."


@mcp_espace.tool(
    name="clovis_ajouter_bloc",
    title="Ajouter un bloc",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
def ajouter_bloc(page_id: str, type: str, texte: str, ctx: Context, ordre: int = 0) -> str:
    """
    Ajoute un bloc de contenu à une page (rattaché à une seule page).
    `type` : texte, titre, liste_puces, liste_numerotee, case_a_cocher,
    citation ou separateur (repli sur "texte" si autre chose).
    """
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        bloc = _ajouter_bloc_notion(user_id, page_id, type, texte, ordre)
    except Exception as e:
        logging.error(f"ERREUR outil ajouter_bloc : {e}")
        return "Erreur : impossible d'ajouter ce bloc, réessaie."
    if bloc is None:
        return "Erreur : page_id invalide ou ne correspond pas à cet utilisateur."
    return f"Bloc ajouté (id: {bloc['id']})."


@mcp_espace.tool(
    name="clovis_modifier_bloc",
    title="Modifier un bloc",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
)
def modifier_bloc(bloc_id: str, texte: str, ctx: Context) -> str:
    """Remplace le texte d'un bloc existant (id vu via clovis_consulter_page)."""
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        bloc = _modifier_bloc_notion(user_id, bloc_id, texte)
    except Exception as e:
        logging.error(f"ERREUR outil modifier_bloc : {e}")
        return "Erreur : impossible de modifier ce bloc, réessaie."
    if bloc is None:
        return "Ce bloc est introuvable ou ne correspond pas à cet utilisateur."
    return "Bloc modifié."


@mcp_espace.tool(
    name="clovis_supprimer_bloc",
    title="Supprimer un bloc",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True),
)
def supprimer_bloc(bloc_id: str, ctx: Context) -> str:
    """Supprime DÉFINITIVEMENT un bloc."""
    user_id = _user_id_authentifie(ctx)
    if not user_id:
        return "Erreur : utilisateur non authentifié."
    try:
        ok = _supprimer_bloc_notion(user_id, bloc_id)
    except Exception as e:
        logging.error(f"ERREUR outil supprimer_bloc : {e}")
        return "Erreur : impossible de supprimer ce bloc, réessaie."
    if not ok:
        return "Ce bloc est introuvable ou ne correspond pas à cet utilisateur."
    return "Bloc supprimé."


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


@mcp_espace.tool(
    name="clovis_discuter_avec_clovis",
    title="Discuter avec Clovis",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


@mcp_espace.tool(
    name="clovis_confirmer_action_clovis",
    title="Confirmer une action",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
)
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


# Toujours actif : Pollinations (gratuit, sans clé) par défaut, bascule
# automatique vers Together AI (payant, meilleure qualité) si
# TOGETHER_API_KEY est configurée -- voir core/generation_images.py.
# Duplication volontaire de l'outil generer_image de
# core/serveur_mcp_generation.py (même convention que le reste de ce
# fichier -- voir docstring en tête), ajoutée le 19/08/2026 (demande
# explicite de Bourama).
#
# CORRECTIF 19/08/2026 : l'outil renvoyait juste l'URL publique en
# texte -- un client MCP externe (Claude) ne l'affiche jamais comme
# une vraie pièce jointe à partir d'un simple lien dans du texte. Il
# faut lui renvoyer les octets de l'image elle-même. _generer_image()
# a déjà uploadé l'image dans Supabase Storage au moment où elle
# renvoie l'URL ; on retélécharge ces octets ici (sans toucher à
# generation_images.py, qui a d'autres appelants -- api/generation.py,
# core/main.py, calcul_symbolique.py, serveur_mcp_generation.py --
# qui n'ont besoin que de l'URL) et on les enveloppe avec la classe
# `Image` du SDK MCP (mcp.server.mcpserver.Image). Le framework
# convertit automatiquement une instance `Image` renvoyée par un
# outil en bloc ImageContent (base64) dans la réponse MCP -- vérifié
# dans func_metadata.py du package mcp==2.0.0 réellement utilisé par
# ce dépôt.
#
# CORRECTIF 19/08/2026 (2) : côté claude.ai/Desktop, le bloc image
# d'un outil MCP externe s'affiche en miniature repliée dans l'appel
# d'outil, sans bouton de téléchargement (comportement du client, pas
# corrigeable ici -- confirmé via issue GitHub
# anthropics/claude-code#53256). On renvoie donc en plus l'URL
# publique en texte à côté de l'image : un `list`/`tuple` renvoyé par
# un outil est éclaté élément par élément par _convert_to_content
# (func_metadata.py) -- Image -> ImageContent, str -> TextContent --
# donc les deux blocs arrivent dans la même réponse, sans rien changer
# à la vraie pièce jointe déjà affichée.
@mcp_espace.tool(
    name="clovis_generer_image",
    title="Générer une image",
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True),
    structured_output=False,
)
def generer_image(prompt: str) -> list[Image | str] | str:
    """
    Génère une image à partir d'une description textuelle. Renvoie
    l'image elle-même (vraie pièce jointe affichée directement) ET
    un lien de téléchargement direct juste après (miniature d'aperçu
    petite et sans téléchargement côté client MCP -- le lien force le
    téléchargement au clic, en taille réelle).
    """
    try:
        url = _generer_image(prompt)
    except Exception as e:
        logging.error(f"ERREUR outil generer_image (mcp_espace) : {e}")
        return "Erreur : la génération de l'image a échoué, réessaie."

    # CORRECTIF 19/08/2026 (3) : url (renvoyée par
    # generation_images.get_public_url) est un lien public "inline" --
    # cliqué, il ouvre l'image dans le navigateur au lieu de la
    # télécharger. Supabase Storage force le téléchargement
    # (Content-Disposition: attachment) via le paramètre de requête
    # ?download=<nom> -- vérifié dans storage3 (SyncBucketProxy.
    # get_public_url, options={"download": ...}), reproduit ici par
    # simple concaténation plutôt que de rappeler get_public_url pour
    # ne pas dépendre du chemin de stockage interne. url ne contient
    # jamais de "?" existant (chemin uuid.png simple, voir
    # generation_images.py), donc l'ajout est sûr sans parsing. Ne
    # touche pas à generation_images.py : ses autres appelants
    # (frontend, PDF, etc.) ont besoin du lien inline normal, pas d'un
    # téléchargement forcé.
    url_telechargement = f"{url}?download=image_clovis.png"

    try:
        reponse = requests.get(url, timeout=30)
        reponse.raise_for_status()
        return [Image(data=reponse.content, format="png"), url_telechargement]
    except Exception as e:
        # L'image a bien été générée et uploadée (on a l'URL), seul le
        # re-téléchargement pour l'afficher a échoué -- on retombe sur
        # le lien texte plutôt que de faire échouer tout l'outil pour
        # rien, l'image reste consultable via l'URL.
        logging.error(f"ERREUR re-téléchargement image pour affichage (mcp_espace) : {e}")
        return url_telechargement
