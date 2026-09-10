"""
Outil MCP pour gérer une ENTRÉE du catalogue public (bibliothèque
publique) au nom de l'utilisateur -- 09/09/2026, demande Bourama : "le
llm doit pouvoir gerer aussi le fichier dans le catalogue publique au
nom de l'user" + "il faut qu'il puisse les copier ou uploader dans sa
bilbiothèque perso". Nouvel outil séparé (demande explicite de
Bourama), distinct de gerer_document_bibliotheque (recherche/lecture
dans le catalogue, jamais son écriture) et de
gerer_dossier_catalogue_public (les DOSSIERS, pas les entrées elles-mêmes).

Toute la logique métier vit dans core/catalogue_public_publication.py
(réutilisée aussi par le futur outil équivalent côté serveur MCP
public, voir core/serveur_mcp_espace.py) -- ce fichier n'est qu'un fin
wrapper MCP (récupération user_id, décodage url_fichier/contenu_base64,
messages d'erreur).
"""

import base64
import logging

import requests

from core.outils_generation_commun import mcp_generation, Context
from core.catalogue_public_publication import (
    publier_fichier_public as _publier_fichier_public,
    publier_lien_public as _publier_lien_public,
    publier_texte_public as _publier_texte_public,
    modifier_entree_publique as _modifier_entree_publique,
    supprimer_entree_publique as _supprimer_entree_publique,
    copier_entree_publique_vers_perso as _copier_entree_publique_vers_perso,
    TAILLE_MAX_OCTETS as _TAILLE_MAX_OCTETS,
)


@mcp_generation.tool()
def gerer_entree_catalogue_public(
    action: str,
    ctx: Context,
    entree_id: str = "",
    nom: str = "",
    description: str = "",
    url: str = "",
    contenu: str = "",
    nom_fichier: str = "",
    type_mime: str = "",
    contenu_base64: str = "",
    url_fichier: str = "",
    dossier_id: str = "",
    pays: str = "",
    niveau: str = "",
    categorie: str = "",
    classe: str = "",
    specialite: str = "",
) -> str:
    """
    Gère une entrée (fichier, lien ou note) du CATALOGUE PUBLIC
    (section "Bibliothèque publique", ouvert à tout le monde) au nom de
    CET utilisateur. Pour LOCALISER/lire un document déjà publié (par
    n'importe qui), voir gerer_document_bibliotheque. Pour les
    DOSSIERS du catalogue public, voir gerer_dossier_catalogue_public.

    `action` doit être l'une de :
    - "publier_fichier" : publie un fichier dans le catalogue public.
      Paramètres : `nom_fichier`, `type_mime` (obligatoires, déduits du
      contexte comme pour gerer_document_bibliotheque/ajouter_fichier).
      Fournir SOIT `url_fichier` (lien réel d'un fichier déjà joint
      dans CETTE conversation) SOIT `contenu_base64`. `nom`,
      `description` optionnels. Limite : 50 Mo.
    - "publier_lien" : publie un lien (pas de fichier réel). Paramètre :
      `url` (obligatoire) ; `nom`, `description` optionnels.
    - "publier_texte" : publie une note de texte libre. Paramètre :
      `contenu` (obligatoire) ; `nom` optionnel.
    - "modifier" : modifie la description et/ou les filtres d'une
      entrée déjà publiée. Réservé au contributeur d'origine.
      Paramètre : `entree_id` ; `description`, `pays`, `niveau`,
      `categorie`, `classe`, `specialite` tous optionnels -- seuls ceux
      que tu fournis sont changés, une valeur vide explicite efface le
      champ.
    - "supprimer" : supprime DÉFINITIVEMENT une entrée du catalogue
      public. Réservé au contributeur d'origine. Paramètre :
      `entree_id`. SENSIBLE : demande toujours confirmation à
      l'utilisateur avant d'être exécuté, quelle que soit la
      formulation de sa demande.
    - "copier_vers_perso" : copie un fichier déjà publié dans le
      catalogue public (n'importe lequel, pas seulement les siens) vers
      la bibliothèque PERSONNELLE de cet utilisateur -- comme s'il
      l'avait uploadé lui-même. Ne fonctionne pas pour un lien (rien à
      copier). Paramètre : `entree_id`.

    Pour les 3 actions "publier_*" et "modifier" : `dossier_id`
    (dossier PUBLIC, obtenu via gerer_dossier_catalogue_public),
    `pays`, `niveau`, `categorie`, `classe`, `specialite` sont tous
    optionnels -- à ne remplir QUE si l'étudiant les mentionne
    clairement, jamais devinés.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "publier_fichier":
        type_mime_val = (type_mime or "").strip().lower()
        if not type_mime_val:
            return "Erreur : type de fichier manquant."
        url_fichier_val = (url_fichier or "").strip()
        contenu_base64_val = (contenu_base64 or "").strip()
        if not url_fichier_val and not contenu_base64_val:
            return "Erreur : fournis url_fichier (lien réel d'un fichier déjà joint dans la conversation) ou contenu_base64."
        if url_fichier_val:
            try:
                reponse = requests.get(url_fichier_val, timeout=30)
                reponse.raise_for_status()
                contenu_fichier = reponse.content
            except Exception as e:
                logging.error(f"ERREUR gerer_entree_catalogue_public (publier_fichier, url_fichier={url_fichier_val}) : {e}")
                return "Erreur : impossible de récupérer le fichier à cette URL."
        else:
            try:
                contenu_fichier = base64.b64decode(contenu_base64_val, validate=True)
            except Exception:
                return "Erreur : contenu_base64 invalide (doit être du base64 valide)."
        if len(contenu_fichier) > _TAILLE_MAX_OCTETS:
            return "Erreur : fichier trop lourd (50 Mo max)."
        try:
            entree = _publier_fichier_public(
                ajoute_par=user_id, contenu=contenu_fichier, nom_fichier=(nom_fichier or "fichier").strip(),
                type_mime=type_mime_val, nom=nom, description=description, dossier_id=(dossier_id or "").strip() or None,
                pays=pays, niveau=niveau, categorie=categorie, classe=classe, specialite=specialite,
            )
        except ValueError as e:
            if str(e) == "FICHIER_VIDE":
                return "Erreur : fichier vide."
            return "Erreur : impossible de publier ce fichier, réessaie."
        except Exception as e:
            logging.error(f"ERREUR gerer_entree_catalogue_public (publier_fichier) : {e}")
            if getattr(e, "code", None) == "23505":
                return "Erreur : ce nom est déjà utilisé dans le catalogue public, choisis-en un autre."
            return "Erreur : impossible de publier ce fichier, réessaie."
        return f"Fichier publié dans le catalogue public [id: {entree['id']}]."

    if action == "publier_lien":
        try:
            entree = _publier_lien_public(
                ajoute_par=user_id, url=url, nom=nom, description=description, dossier_id=(dossier_id or "").strip() or None,
                pays=pays, niveau=niveau, categorie=categorie, classe=classe, specialite=specialite,
            )
        except ValueError:
            return "Erreur : url manquante."
        except Exception as e:
            logging.error(f"ERREUR gerer_entree_catalogue_public (publier_lien) : {e}")
            if getattr(e, "code", None) == "23505":
                return "Erreur : ce nom est déjà utilisé dans le catalogue public, choisis-en un autre."
            return "Erreur : impossible de publier ce lien, réessaie."
        return f"Lien publié dans le catalogue public [id: {entree['id']}]."

    if action == "publier_texte":
        try:
            entree = _publier_texte_public(
                ajoute_par=user_id, contenu=contenu, nom=nom, dossier_id=(dossier_id or "").strip() or None,
                pays=pays, niveau=niveau, categorie=categorie, classe=classe, specialite=specialite,
            )
        except ValueError:
            return "Erreur : texte vide."
        except Exception as e:
            logging.error(f"ERREUR gerer_entree_catalogue_public (publier_texte) : {e}")
            if getattr(e, "code", None) == "23505":
                return "Erreur : ce nom est déjà utilisé dans le catalogue public, choisis-en un autre."
            return "Erreur : impossible de publier cette note, réessaie."
        return f"Note publiée dans le catalogue public [id: {entree['id']}]."

    if action == "modifier":
        if not (entree_id or "").strip():
            return "Erreur : entree_id manquant."
        # Convention : un paramètre laissé vide ("") signifie "ne pas
        # toucher ce champ", pas "l'effacer" -- même logique que tous
        # les autres paramètres optionnels de ce fichier. Effacer un
        # filtre existant n'est pas possible via cet outil pour
        # l'instant.
        try:
            erreur = _modifier_entree_publique(
                entree_id, user_id,
                description=description.strip() or None if description else None,
                pays=pays.strip() or None if pays else None,
                niveau=niveau.strip() or None if niveau else None,
                categorie=categorie.strip() or None if categorie else None,
                classe=classe.strip() or None if classe else None,
                specialite=specialite.strip() or None if specialite else None,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_entree_catalogue_public (modifier) : {e}")
            return "Erreur : impossible de modifier cette entrée, réessaie."
        if erreur == "ENTREE_INTROUVABLE":
            return "Cette entrée du catalogue public est introuvable."
        if erreur == "CETTE_ENTREE_NE_T_APPARTIENT_PAS":
            return "Erreur : tu ne peux modifier que les entrées que tu as toi-même publiées."
        if erreur == "AUCUNE_MODIFICATION_FOURNIE":
            return "Erreur : indique au moins une chose à modifier (description ou un filtre)."
        return "Entrée du catalogue public modifiée."

    if action == "supprimer":
        if not (entree_id or "").strip():
            return "Erreur : entree_id manquant."
        try:
            ok = _supprimer_entree_publique(entree_id, user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_entree_catalogue_public (supprimer) : {e}")
            return "Erreur : impossible de supprimer cette entrée, réessaie."
        if not ok:
            return "Erreur : cette entrée est introuvable, ou tu n'en es pas l'auteur."
        return "Entrée supprimée du catalogue public."

    if action == "copier_vers_perso":
        if not (entree_id or "").strip():
            return "Erreur : entree_id manquant."
        try:
            resultat = _copier_entree_publique_vers_perso(entree_id, user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_entree_catalogue_public (copier_vers_perso) : {e}")
            if getattr(e, "code", None) == "23505":
                return "Erreur : un fichier de ce nom existe déjà dans ta bibliothèque personnelle."
            return "Erreur : impossible de copier ce document, réessaie."
        if resultat == "ENTREE_INTROUVABLE":
            return "Cette entrée du catalogue public est introuvable."
        if resultat == "CE_DOCUMENT_EST_UN_LIEN_NON_COPIABLE":
            return "Erreur : ce document est un simple lien, rien à copier."
        if resultat in ("ECHEC_DU_STOCKAGE_REESSAIE", "FICHIER_VIDE"):
            return "Erreur : impossible de copier ce document, réessaie."
        return "Document copié dans ta bibliothèque personnelle."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : publier_fichier, publier_lien, "
        "publier_texte, modifier, supprimer, copier_vers_perso."
    )
