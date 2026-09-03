"""
Bibliothèque personnelle d'un utilisateur (2026-08-01, demande Bourama :
nouvelle section "Mon espace" -- "tu peux y uploader autant de documents
que tu veux et ton IA les utilise pour te répondre, dans n'importe
quelle conversation, n'importe quel chat").

Mirroir volontaire des routes POST/GET/DELETE
/{agent_id}/bibliotheque de api/agents.py, mais niveau="utilisateur"
(voir core/bibliotheque_fichiers.py), scopé par l'utilisateur connecté
lui-même -- pas d'agent_id, pas de vérification de propriété d'agent
(c'est TOUJOURS "soi-même" ici).

Différence clé avec la bibliothèque niveau "agent" : le PDF est vectorisé
dans une table dédiée (documents_bibliotheque, scopée user_id), séparée
de la table `documents` (RAG agent, scopée agent_id) -- voir
core/bibliotheque_rag.py. L'outil de conversation correspondant,
consulter_bibliotheque, est disponible pour TOUS les agents sans
configuration par le créateur (voir core/mcp_tools.py).
"""

import asyncio
import logging
import os
import sys

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from postgrest.exceptions import APIError
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
from bibliotheque_fichiers import enregistrer_fichier, enregistrer_lien, lister_fichiers, supprimer_fichier  # noqa: E402
from file_attente_vectorisation import (  # noqa: E402
    necessite_vectorisation_fichier_privee,
    necessite_vectorisation_note,
    reinitialiser_pour_reessai,
)

router = APIRouter(prefix="/api/bibliotheque", tags=["bibliotheque-utilisateur"])

TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo, même limite que la bibliothèque niveau agent
BUCKET_BIBLIOTHEQUE_PUBLIQUE = "bibliotheque"  # même bucket que core/bibliotheque_fichiers.py, sous-dossier "publique/"


def _journaliser_ajout(contenu, type_mime, nom_fichier, description, ligne, utilisateur, request):
    """
    Factorisé le 25/08 (Bourama : "rendre les fichiers de la bibliothèque
    publique copiables vers sa bibliothèque privée") entre un upload
    classique (uploader_document ci-dessous) et une copie depuis la
    bibliothèque publique (copier_depuis_bibliotheque_publique plus bas) :
    journal -- identique dans les deux cas, seule la source du contenu
    diffère (upload direct vs téléchargement depuis le storage de la
    bibliothèque publique).

    RENOMMÉ le 29/08/2026 (file d'attente de vectorisation en arrière-
    plan, demande Bourama : upload en masse ou fichier long bloquait
    trop longtemps) : la vectorisation PDF/image/audio qui vivait ici
    est retirée -- enregistrer_fichier (appelé par les deux fonctions
    juste avant celle-ci) a déjà mis le fichier en statut_vectorisation=
    "en_attente" si besoin (voir necessite_vectorisation_fichier_privee),
    et core/file_attente_vectorisation.py s'en charge en arrière-plan.

    RENOMMÉ à nouveau le 02/09/2026 (demande Bourama : le partage par
    code n'est plus déclenché à l'AJOUT en bibliothèque, mais uniquement
    quand un fichier est RANGÉ dans un dossier que ce code partage --
    voir api/dossiers_bibliotheque.py::ranger et
    core/codes_partage.py::propager_fichier_range_dossier. Cette fonction
    ne fait donc plus que journaliser, la propagation est retirée d'ici.
    """
    journaliser(
        action="bibliotheque_perso.ajoute",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"description": description, "type_mime": type_mime},
        request=request,
    )
    return ligne


@router.post("", status_code=201)
async def uploader_document(
    request: Request,
    fichier: UploadFile = File(...),
    titre: str = Form(None),
    description: str = Form(None),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Ajoute un fichier à la bibliothèque personnelle de l'utilisateur
    connecté. Le fichier est stocké et renvoyé immédiatement ; un
    pdf/image/audio est en plus vectorisé en ARRIÈRE-PLAN (29/08, voir
    core/file_attente_vectorisation.py) pour que consulter_bibliotheque
    puisse répondre à partir de son contenu dès que c'est prêt -- les
    autres types restent retrouvables par nom/description via
    chercher_fichier uniquement.
    """
    # CORRECTION du 01/08 (Bourama : "plusieurs upload à la fois") :
    # description/titre ne sont plus obligatoires -- repli sur le nom du
    # fichier tel quel, pour ne pas forcer une saisie manuelle par
    # fichier quand on en envoie plusieurs d'un coup. chercher_fichier
    # (recherche par nom/description) reste utilisable, juste moins
    # fin sans description écrite à la main.
    # 17/08 (Bourama : "il faut qu'on puisse uploader tout") -- la
    # whitelist de types (pdf/image/audio/vidéo) a été retirée : seule
    # la taille est encore contrôlée. enregistrer_fichier ci-dessous
    # accepte déjà n'importe quel type_mime (voir sa docstring), la
    # vectorisation PDF plus bas ne dépend que du type réel du fichier,
    # pas d'une liste fermée.
    contenu = await fichier.read()
    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise erreur_api(400, "FICHIER_TROP_LOURD_50_MO_MAX")

    # CORRECTIF 2026-08-27 (bug remonté par Bourama : un fichier issu
    # d'un dossier importé gardait son chemin complet comme nom --
    # "Cours/Chimie/td1.pdf" au lieu de "td1.pdf". Cause côté navigateur,
    # déjà corrigée dans lib/api.ts:ajouterFichierBibliothequePersonnelle
    # -- filet de sécurité ici pour tout AUTRE client (app mobile, appel
    # API direct) qui enverrait encore un chemin par erreur : ne jamais
    # garder autre chose que le dernier segment, / et \ compris (Windows).
    nom_original = (fichier.filename or "fichier").replace("\\", "/").rsplit("/", 1)[-1]
    description_finale = (
        f"{titre.strip()} — {description.strip()}" if (titre or "").strip() and (description or "").strip()
        else (description or titre or "").strip() or nom_original
    )

    try:
        # CORRECTIF 02/09 (bug remonté par Bourama : upload perçu comme
        # lent) : enregistrer_fichier fait des appels Supabase Storage +
        # DB synchrones/bloquants -- appelé tel quel dans cette route
        # async, il bloquait tout le serveur (event loop) pendant toute
        # la durée de l'upload : plus aucune autre requête, y compris
        # les autres fichiers d'un envoi multiple, ne pouvait être
        # traitée en attendant. asyncio.to_thread le déporte sur un
        # thread pour ne plus bloquer.
        ligne = await asyncio.to_thread(
            enregistrer_fichier,
            contenu=contenu,
            nom_fichier=nom_original,
            type_mime=fichier.content_type,
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=description_finale,
            statut_vectorisation="en_attente" if necessite_vectorisation_fichier_privee(fichier.content_type) else "pret",
        )
    except APIError as e:
        # CORRECTIF 02/09 (bug remonté par Bourama : aucun traitement
        # d'erreur à l'upload, notamment pour les doublons désormais
        # refusés par un index unique Supabase -- code Postgres 23505).
        if getattr(e, "code", None) == "23505":
            raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PERSO", nom=nom_original)
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")
    except Exception:
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    return _journaliser_ajout(contenu, fichier.content_type, nom_original, description_finale, ligne, utilisateur, request)


@router.post("/copier-depuis-publique/{entree_id}", status_code=201)
async def copier_depuis_bibliotheque_publique(
    entree_id: str,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Copie un fichier de la bibliothèque publique vers la bibliothèque
    personnelle de l'utilisateur connecté (25/08, demande Bourama :
    "rendre les fichiers de la bibliothèque publique uploadables/
    copiables vers la bibliothèque privée"). Va chercher le fichier
    directement dans le storage (même bucket que core/bibliotheque_
    fichiers.py, sous-dossier "publique/", voir api/bibliotheque_
    publique.py) plutôt que de re-télécharger l'URL publique en HTTP --
    plus fiable, pas de dépendance réseau externe.

    Traitement ensuite IDENTIQUE à un upload classique (_indexer_et_
    propager) : vectorisation PDF/image/audio, journal, propagation aux
    codes de partage -- une copie doit se comporter exactement comme si
    l'utilisateur avait uploadé le fichier lui-même.
    """
    res = (
        supabase.table("bibliotheque_publique")
        .select("nom, description, nom_fichier, type_mime, chemin_stockage, statut")
        .eq("id", entree_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data or res.data["statut"] != "publie":
        raise erreur_api(404, "ENTREE_INTROUVABLE")

    entree = res.data
    try:
        contenu = supabase.storage.from_(BUCKET_BIBLIOTHEQUE_PUBLIQUE).download(entree["chemin_stockage"])
    except Exception as e:
        logging.error(f"ERREUR téléchargement fichier bibliothèque publique ({entree_id}) : {e}")
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    if len(contenu) == 0:
        raise erreur_api(400, "FICHIER_VIDE")

    nom_original = entree["nom_fichier"] or entree["nom"]
    description_finale = (entree["description"] or "").strip() or entree["nom"]

    try:
        # Même correctif que uploader_document ci-dessus (02/09) : thread
        # dédié pour ne pas bloquer le serveur, détection précise du
        # doublon (contrainte d'unicité par utilisateur sur nom_fichier).
        # CORRECTIF 02/09/2026 (demande Bourama : distinguer "depuis
        # public" des autres origines dans la bibliothèque perso) :
        # avant, une copie retombait sur l'origine par défaut
        # "bibliotheque", indistinguable d'un ajout direct.
        ligne = await asyncio.to_thread(
            enregistrer_fichier,
            contenu=contenu,
            nom_fichier=nom_original,
            type_mime=entree["type_mime"],
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=description_finale,
            statut_vectorisation="en_attente" if necessite_vectorisation_fichier_privee(entree["type_mime"]) else "pret",
            origine="publique",
        )
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PERSO", nom=nom_original)
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")
    except Exception:
        raise erreur_api(500, "ECHEC_DU_STOCKAGE_REESSAIE")

    return _journaliser_ajout(
        contenu, entree["type_mime"], nom_original, description_finale, ligne, utilisateur, request
    )


class AjouterLienPayload(BaseModel):
    url: str
    titre: str = None
    description: str = None


@router.post("/lien", status_code=201)
def ajouter_lien(
    payload: AjouterLienPayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """Pendant de uploader_document ci-dessus pour une entrée "lien" (voir enregistrer_lien)."""
    # Assoupli le 01/08 (Bourama : "pas de filtre au moment de l'upload",
    # ajout groupé fichiers+liens+texte en une seule action) : plus de
    # titre/description obligatoire, repli sur l'URL elle-même -- même
    # logique que uploader_document ci-dessus depuis la correction
    # "plusieurs upload à la fois".
    if not (payload.url or "").strip():
        raise erreur_api(400, "URL_MANQUANTE")

    description_finale = (
        f"{payload.titre.strip()} — {payload.description.strip()}"
        if (payload.titre or "").strip() and (payload.description or "").strip()
        else (payload.description or payload.titre or "").strip() or payload.url.strip()
    )

    try:
        ligne = enregistrer_lien(
            url=payload.url.strip(),
            nom_fichier=(payload.titre or payload.url).strip(),
            niveau="utilisateur",
            uploade_par=utilisateur.id,
            user_id=utilisateur.id,
            description=description_finale,
        )
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PERSO", nom=(payload.titre or payload.url).strip())
        raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DU_LIEN")
    except Exception:
        raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DU_LIEN")

    journaliser(
        action="bibliotheque_perso.ajoute",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"description": description_finale, "type_mime": "text/uri-list"},
        request=request,
    )

    return ligne


class AjouterTextePayload(BaseModel):
    contenu: str
    titre: str = None


@router.post("/texte", status_code=201)
def ajouter_texte(
    payload: AjouterTextePayload,
    request: Request,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Note de texte tapée/collée directement (2026-08-01, demande Bourama :
    "ajoute le cas des liens et du texte", "pas de filtre au moment de
    l'upload") -- stockée comme un fichier .txt ordinaire (même mécanisme
    que uploader_document, type_mime="text/plain" sert de marqueur côté
    frontend pour le sous-onglet "Texte"), puis vectorisée en
    ARRIÈRE-PLAN (29/08, voir core/file_attente_vectorisation.py) --
    pas besoin d'extraction contrairement à un PDF donc quasi instantané,
    mais quand même via la file d'attente pour rester cohérent.
    """
    contenu = (payload.contenu or "").strip()
    if not contenu:
        raise erreur_api(400, "TEXTE_VIDE")

    titre = (payload.titre or "").strip()
    nom_fichier = f"{titre or 'Note'}.txt"

    # CORRECTIF 02/09 : nouvelle contrainte d'unicité par utilisateur sur
    # nom_fichier (voir core/bibliotheque_fichiers.py). Sans titre, le nom
    # par défaut "Note.txt" est identique pour toutes les notes non
    # titrées d'un même utilisateur -- ce n'est pas un vrai doublon aux
    # yeux de l'utilisateur (il n'a rien nommé lui-même), donc on
    # auto-suffixe ("Note (2).txt", etc.) au lieu de le bloquer avec une
    # erreur. Avec un titre explicite en revanche, un vrai doublon doit
    # remonter l'erreur normalement.
    tentative = 1
    while True:
        try:
            ligne = enregistrer_fichier(
                contenu=contenu.encode("utf-8"),
                nom_fichier=nom_fichier,
                type_mime="text/plain",
                niveau="utilisateur",
                uploade_par=utilisateur.id,
                user_id=utilisateur.id,
                description=titre or (contenu[:80] + ("…" if len(contenu) > 80 else "")),
                statut_vectorisation="en_attente" if necessite_vectorisation_note() else "pret",
            )
            break
        except APIError as e:
            if getattr(e, "code", None) != "23505":
                raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DE_LA_NOTE")
            if titre or tentative >= 20:
                raise erreur_api(409, "NOM_DEJA_UTILISE_BIBLIOTHEQUE_PERSO", nom=nom_fichier)
            tentative += 1
            nom_fichier = f"Note ({tentative}).txt"
        except Exception:
            raise erreur_api(500, "ECHEC_DE_L_ENREGISTREMENT_DE_LA_NOTE")

    # Vectorisation en arrière-plan (29/08, voir core/file_attente_vectorisation.py) --
    # avant, indexer_texte_bibliotheque était appelé directement ici.

    journaliser(
        action="bibliotheque_perso.ajoute",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"description": titre, "type_mime": "text/plain"},
        request=request,
    )

    return ligne


@router.get("")
def lister(utilisateur=Depends(utilisateur_courant)):
    # Corrigé le 01/08 (Bourama) : d'abord une comparaison fragile sur le
    # texte de la description (incomplète, ratait audio/image/vidéo, ne
    # gardait que "Document..."), remplacée par le vrai filtre origine
    # (voir migration fichiers_uploades_origine + enregistrer_fichier).
    # CORRECTIF 02/09/2026 : les 5 onglets d'origine de l'écran Bibliothèque
    # (EspaceBibliotheque.tsx) incluent désormais "Uploadé dans un chat" --
    # donc plus aucun filtre d'origine ici, on remonte tout et c'est le
    # frontend qui répartit par origine. Les outils IA
    # (serveur_mcp_generation.py / serveur_mcp_espace.py) gardent eux
    # exclut_origine="chat" pour leur recherche dans "ta bibliothèque",
    # volontairement différent : une pièce jointe de conversation n'est
    # pas un document que l'IA doit ressortir comme si tu l'avais rangé.
    return lister_fichiers("utilisateur", user_id=utilisateur.id)


@router.post("/{fichier_id}/reessayer-vectorisation", status_code=204)
def reessayer_vectorisation(fichier_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Bouton "Réessayer" (03/09/2026, demande Bourama : un fichier en échec
    de vectorisation restait affiché avec un point rouge indéfiniment,
    seule "solution" = supprimer + réajouter -- voir core/file_attente_
    vectorisation.py). Vérifie la propriété comme pour la suppression
    ci-dessous, puis remet le fichier en file immédiatement.
    """
    res = supabase.table("fichiers_uploades").select("user_id, statut_vectorisation").eq("id", fichier_id).maybe_single().execute()
    if not res or not res.data:
        raise erreur_api(404, "FICHIER_INTROUVABLE")
    if res.data["user_id"] != utilisateur.id:
        raise erreur_api(403, "CE_FICHIER_NE_T_APPARTIENT_PAS")

    if not reinitialiser_pour_reessai("fichiers_uploades", fichier_id):
        raise erreur_api(409, "FICHIER_PAS_EN_ECHEC")


@router.delete("/{fichier_id}", status_code=204)
def supprimer(fichier_id: str, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Vérifie que le fichier appartient bien à l'utilisateur connecté avant
    de le supprimer -- contrairement à la bibliothèque niveau agent (où
    la vérification passe par le owner_id de l'agent), il faut ici lire
    la ligne fichiers_uploades elle-même.
    """
    try:
        res = supabase.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture fichier {fichier_id} avant suppression bibliothèque perso) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_SUPPRIMER_CE_FICHIER_POUR")

    if not res or not res.data:
        raise erreur_api(404, "FICHIER_INTROUVABLE")
    if res.data["user_id"] != utilisateur.id:
        raise erreur_api(403, "CE_FICHIER_NE_T_APPARTIENT_PAS")

    # documents_bibliotheque est en ON DELETE CASCADE sur fichier_id (voir
    # migration) : pas besoin de nettoyer les chunks vectorisés ici.
    supprimer_fichier(fichier_id)

    journaliser(
        action="bibliotheque_perso.supprime",
        user_id=utilisateur.id,
        cible_type="utilisateur",
        cible_id=utilisateur.id,
        details={"fichier_id": fichier_id},
        request=request,
    )
