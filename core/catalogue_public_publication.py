"""
Gestion d'une ENTRÉE du catalogue public (bibliothèque publique) au nom
de l'utilisateur -- 09/09/2026, demande Bourama : "le llm doit pouvoir
gerer aussi le fichier dans le catalogue publique au nom de l'user" +
"il faut qu'il puisse les copier ou uploader dans sa bibliothèque
perso". Distinct de core/dossiers_catalogue_public.py (qui gère les
DOSSIERS, pas les fichiers/liens/notes eux-mêmes).

publier_fichier_public/publier_lien_public/publier_texte_public
dupliquent volontairement une partie de la logique d'insertion déjà
présente dans api/bibliotheque_publique.py (même convention que
core/catalogue_public_rag.py vis-à-vis de core/bibliotheque_rag.py :
"volontairement dupliqué plutôt qu'importé... pas de dépendance
croisée entre les deux circuits") -- ces routes attendent un
UploadFile/JSON HTTP, pas les octets déjà en main que fournit un outil
MCP (url_fichier/contenu_base64, comme ajouter_fichier côté perso).

modifier_entree_publique et supprimer_entree_publique/
copier_entree_publique_vers_perso sont en revanche le SEUL endroit qui
porte cette logique (aucune route API ne fait "modifier" aujourd'hui ;
"supprimer"/"copier" existaient déjà côté API -- voir
api/bibliotheque_publique.py::supprimer_de_bibliotheque_publique et
api/bibliotheque_utilisateur.py::copier_depuis_bibliotheque_publique --
réécrits ici à l'identique pour être appelés directement par l'outil
MCP, sans repasser par une requête HTTP interne).
"""

import logging
import uuid

from core.dossiers_catalogue_public import (
    supabase,
    _dossier as _obtenir_dossier_catalogue_public,
    ranger_fichier as _ranger_fichier_catalogue_public,
    peut_ajouter_contenu as _peut_ajouter_contenu_dossier,
)
from core.dossiers_publics_attaches import propager_fichier_public_range_dossier as _propager_fichier_public_range_dossier
from core.listes_bibliotheque_publique import normaliser_et_enregistrer
from core.file_attente_vectorisation import (
    necessite_vectorisation_fichier_publique,
    necessite_extraction_texte_publique,
    necessite_vectorisation_note,
    necessite_vectorisation_fichier_privee,
)
from core.bibliotheque_fichiers import enregistrer_fichier as _enregistrer_fichier_perso

BUCKET = "bibliotheque"
TAILLE_MAX_OCTETS = 50 * 1024 * 1024  # 50 Mo, même limite que le reste de la bibliothèque publique/perso


def _classer_si_autorise(fichier_id: str, dossier_id: str | None, utilisateur_id: str) -> None:
    if not (dossier_id or "").strip():
        return
    try:
        if _obtenir_dossier_catalogue_public(dossier_id) and _peut_ajouter_contenu_dossier(dossier_id, utilisateur_id):
            _ranger_fichier_catalogue_public(fichier_id, dossier_id)
            _propager_fichier_public_range_dossier(fichier_id, dossier_id)
    except Exception as e:
        logging.error(f"ERREUR classement dossier catalogue public (fichier_id={fichier_id}, dossier_id={dossier_id}) : {e}")


def _filtres_normalises(pays, niveau, categorie, classe, specialite) -> dict:
    return {
        "pays": normaliser_et_enregistrer("pays", pays),
        "niveau": normaliser_et_enregistrer("niveau", niveau),
        "categorie": normaliser_et_enregistrer("categorie", categorie),
        "classe": normaliser_et_enregistrer("classe", classe),
        "specialite": normaliser_et_enregistrer("specialite", specialite),
    }


def publier_fichier_public(
    ajoute_par: str, contenu: bytes, nom_fichier: str, type_mime: str,
    nom: str = "", description: str = "", dossier_id: str = None,
    pays: str = None, niveau: str = None, categorie: str = None, classe: str = None, specialite: str = None,
) -> dict:
    """Publie un fichier (octets déjà en main) dans le catalogue public. Voir docstring du module."""
    if len(contenu) == 0:
        raise ValueError("FICHIER_VIDE")
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise ValueError("FICHIER_TROP_LOURD_50_MO_MAX")
    nom_final = (nom or "").strip() or (nom_fichier or "Document").rsplit(".", 1)[0]
    extension = nom_fichier.rsplit(".", 1)[-1] if "." in (nom_fichier or "") else "bin"
    chemin_stockage = f"publique/{uuid.uuid4()}.{extension}"

    supabase.storage.from_(BUCKET).upload(chemin_stockage, contenu, {"content-type": type_mime or "application/octet-stream"})
    url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    donnees = {
        "ajoute_par": ajoute_par,
        "nom": nom_final,
        "description": (description or "").strip(),
        "nom_fichier": nom_fichier or "fichier",
        "chemin_stockage": chemin_stockage,
        "url_publique": url_publique,
        "type_mime": type_mime,
        "taille_octets": len(contenu),
        **(
            {"statut_vectorisation": "en_attente", "statut_extraction_texte": "non_applicable"}
            if necessite_vectorisation_fichier_publique(type_mime)
            else {"statut_vectorisation": "a_la_demande", "statut_extraction_texte": "en_attente"}
            if necessite_extraction_texte_publique(type_mime)
            else {"statut_vectorisation": "a_la_demande", "statut_extraction_texte": "non_applicable"}
        ),
        **_filtres_normalises(pays, niveau, categorie, classe, specialite),
    }
    ligne = supabase.table("bibliotheque_publique").insert(donnees).execute()
    entree = ligne.data[0]
    _classer_si_autorise(entree["id"], dossier_id, ajoute_par)
    return entree


def publier_lien_public(
    ajoute_par: str, url: str, nom: str = "", description: str = "", dossier_id: str = None,
    pays: str = None, niveau: str = None, categorie: str = None, classe: str = None, specialite: str = None,
) -> dict:
    """Publie un lien (pas de fichier réel, url_publique EST le lien) dans le catalogue public."""
    url_val = (url or "").strip()
    if not url_val:
        raise ValueError("URL_MANQUANTE")
    nom_final = (nom or "").strip() or url_val
    donnees = {
        "ajoute_par": ajoute_par,
        "nom": nom_final,
        "description": (description or "").strip(),
        "nom_fichier": nom_final,
        "url_publique": url_val,
        "type_mime": "text/uri-list",
        "statut_vectorisation": "pret",  # un lien n'est jamais vectorisé
        **_filtres_normalises(pays, niveau, categorie, classe, specialite),
    }
    ligne = supabase.table("bibliotheque_publique").insert(donnees).execute()
    entree = ligne.data[0]
    _classer_si_autorise(entree["id"], dossier_id, ajoute_par)
    return entree


def publier_texte_public(
    ajoute_par: str, contenu: str, nom: str = "", dossier_id: str = None,
    pays: str = None, niveau: str = None, categorie: str = None, classe: str = None, specialite: str = None,
) -> dict:
    """Publie une note de texte libre (stockée comme un .txt ordinaire) dans le catalogue public."""
    contenu_texte = (contenu or "").strip()
    if not contenu_texte:
        raise ValueError("TEXTE_VIDE")
    nom_final = (nom or "").strip() or (contenu_texte[:80] + ("…" if len(contenu_texte) > 80 else ""))
    contenu_octets = contenu_texte.encode("utf-8")
    nom_fichier = f"{nom_final}.txt"
    chemin_stockage = f"publique/{uuid.uuid4()}.txt"

    supabase.storage.from_(BUCKET).upload(chemin_stockage, contenu_octets, {"content-type": "text/plain"})
    url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    donnees = {
        "ajoute_par": ajoute_par,
        "nom": nom_final,
        "description": "",
        "nom_fichier": nom_fichier,
        "chemin_stockage": chemin_stockage,
        "url_publique": url_publique,
        "type_mime": "text/plain",
        "taille_octets": len(contenu_octets),
        "statut_vectorisation": "en_attente" if necessite_vectorisation_note() else "pret",
        **_filtres_normalises(pays, niveau, categorie, classe, specialite),
    }
    ligne = supabase.table("bibliotheque_publique").insert(donnees).execute()
    entree = ligne.data[0]
    _classer_si_autorise(entree["id"], dossier_id, ajoute_par)
    return entree


def modifier_entree_publique(
    entree_id: str, utilisateur_id: str, description: str = None,
    pays: str = None, niveau: str = None, categorie: str = None, classe: str = None, specialite: str = None,
) -> str | None:
    """
    Modifie la description et/ou les filtres d'une entrée déjà publiée
    (nouvelle capacité, 09/09/2026 -- n'existait dans AUCUNE route API
    avant cet ajout). Seul le contributeur d'origine peut modifier SA
    propre entrée (même règle que supprimer_entree_publique).

    Chaque paramètre non fourni (None) laisse le champ correspondant
    inchangé -- une chaîne vide explicite EFFACE le champ (ex.
    `pays=""` retire le filtre pays existant).

    Renvoie None si tout s'est bien passé, ou un message d'erreur
    (str) sinon.
    """
    res = supabase.table("bibliotheque_publique").select("ajoute_par").eq("id", entree_id).maybe_single().execute()
    if not res or not res.data:
        return "ENTREE_INTROUVABLE"
    if res.data["ajoute_par"] != utilisateur_id:
        return "CETTE_ENTREE_NE_T_APPARTIENT_PAS"

    maj = {}
    if description is not None:
        maj["description"] = description.strip()
    if pays is not None:
        maj["pays"] = normaliser_et_enregistrer("pays", pays) if pays.strip() else None
    if niveau is not None:
        maj["niveau"] = normaliser_et_enregistrer("niveau", niveau) if niveau.strip() else None
    if categorie is not None:
        maj["categorie"] = normaliser_et_enregistrer("categorie", categorie) if categorie.strip() else None
    if classe is not None:
        maj["classe"] = normaliser_et_enregistrer("classe", classe) if classe.strip() else None
    if specialite is not None:
        maj["specialite"] = normaliser_et_enregistrer("specialite", specialite) if specialite.strip() else None

    if not maj:
        return "AUCUNE_MODIFICATION_FOURNIE"

    supabase.table("bibliotheque_publique").update(maj).eq("id", entree_id).execute()
    return None


def supprimer_entree_publique(entree_id: str, utilisateur_id: str) -> bool:
    """
    Supprime une entrée du catalogue public. Réplique à l'identique
    api/bibliotheque_publique.py::supprimer_de_bibliotheque_publique
    (seul le contributeur d'origine peut retirer SA propre entrée) pour
    être appelable directement par l'outil MCP. Renvoie True si
    supprimée, False si introuvable ou si l'appelant n'en est pas
    l'auteur.
    """
    res = supabase.table("bibliotheque_publique").select("ajoute_par").eq("id", entree_id).maybe_single().execute()
    if not res or not res.data or res.data["ajoute_par"] != utilisateur_id:
        return False
    supabase.table("bibliotheque_publique").delete().eq("id", entree_id).execute()
    return True


def copier_entree_publique_vers_perso(entree_id: str, utilisateur_id: str) -> dict | str:
    """
    Copie un fichier du catalogue public vers la bibliothèque
    personnelle de `utilisateur_id`. Réplique à l'identique
    api/bibliotheque_utilisateur.py::copier_depuis_bibliotheque_publique
    pour être appelable directement par l'outil MCP (télécharge depuis
    le storage, jamais l'URL publique en HTTP -- plus fiable).

    Renvoie la ligne créée (dict) si tout va bien, ou un message
    d'erreur (str) sinon.
    """
    res = (
        supabase.table("bibliotheque_publique")
        .select("nom, description, nom_fichier, type_mime, chemin_stockage, statut")
        .eq("id", entree_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data or res.data["statut"] != "publie":
        return "ENTREE_INTROUVABLE"

    entree = res.data
    if not entree.get("chemin_stockage"):
        return "CE_DOCUMENT_EST_UN_LIEN_NON_COPIABLE"
    try:
        contenu = supabase.storage.from_(BUCKET).download(entree["chemin_stockage"])
    except Exception as e:
        logging.error(f"ERREUR téléchargement fichier bibliothèque publique ({entree_id}) : {e}")
        return "ECHEC_DU_STOCKAGE_REESSAIE"

    if len(contenu) == 0:
        return "FICHIER_VIDE"

    nom_original = entree["nom_fichier"] or entree["nom"]
    description_finale = (entree["description"] or "").strip() or entree["nom"]

    ligne = _enregistrer_fichier_perso(
        contenu=contenu,
        nom_fichier=nom_original,
        type_mime=entree["type_mime"],
        niveau="utilisateur",
        uploade_par=utilisateur_id,
        user_id=utilisateur_id,
        description=description_finale,
        statut_vectorisation="en_attente" if necessite_vectorisation_fichier_privee(entree["type_mime"]) else "pret",
        origine="publique",
    )
    return ligne
