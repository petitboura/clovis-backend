"""
Bibliothèque de fichiers uploadés, persistante, à 3 niveaux d'accès :
- "plateforme"  : uploadé par Bourama, visible par TOUS les agents
- "agent"       : uploadé par le créateur d'un agent, visible par tous
                   les utilisateurs de CET agent précis
- "utilisateur" : uploadé par un utilisateur dans le chat, visible par
                   lui seul

Remplace le comportement précédent où un fichier uploadé (image ou
document) n'était utilisé qu'une seule fois puis jeté (voir
api/uploads.py avant le 2026-07-22) : ici, tout fichier est conservé
dans Supabase Storage (bucket "bibliotheque") ET indexé dans la table
fichiers_uploades, pour qu'un agent puisse le retrouver et le
redonner/afficher plus tard, y compris dans une autre conversation.

Un seul type de fichier n'est pas privilégié : image, PDF, audio,
vidéo... tout passe par le même mécanisme, seule la description texte
(fournie à l'upload) permet à l'IA de savoir ce que contient le fichier
sans avoir besoin de l'ouvrir.
"""

import logging
import os
import uuid

from supabase import create_client

BUCKET = "bibliotheque"


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def enregistrer_lien(
    url: str,
    nom_fichier: str,
    niveau: str,
    uploade_par: str,
    agent_id: str = None,
    user_id: str = None,
    description: str = None,
) -> dict:
    """
    Variante de enregistrer_fichier pour une entrée "lien" (juste une URL,
    aucun fichier uploadé -- Bourama 01/08 : l'onglet "Lien" existait déjà
    côté filtre mais rien ne permettait vraiment d'en ajouter un). Pas
    d'upload Supabase Storage ici : url_publique EST l'URL donnée.
    chemin_stockage est NOT NULL en base mais inutilisé pour un lien --
    on y met l'URL aussi, pour rester traçable sans complexifier le schéma.
    type_mime="text/uri-list" sert de marqueur "ceci est un lien" pour
    categorieFichierBiblio côté frontend.
    """
    insertion = supabase.table("fichiers_uploades").insert({
        "niveau": niveau,
        "agent_id": agent_id,
        "user_id": user_id,
        "uploade_par": uploade_par,
        "chemin_stockage": url,
        "url_publique": url,
        "nom_fichier": nom_fichier,
        "type_mime": "text/uri-list",
        "description": description,
        "taille_octets": None,
    }).execute()
    return insertion.data[0]


def enregistrer_fichier(
    contenu: bytes,
    nom_fichier: str,
    type_mime: str,
    niveau: str,
    uploade_par: str,
    agent_id: str = None,
    user_id: str = None,
    description: str = None,
    origine: str = "bibliotheque",
) -> dict:
    """
    Stocke un fichier dans Supabase Storage et l'indexe dans
    fichiers_uploades. `niveau` doit être "plateforme", "agent" ou
    "utilisateur" (voir docstring du module) ; `agent_id`/`user_id` sont
    requis en cohérence avec le niveau (ex. niveau="agent" -> agent_id
    obligatoire) mais ce n'est pas vérifié ici -- c'est à l'appelant
    (route API) de garantir la cohérence selon qui uploade.
    `origine` (2026-08-01, "chat" ou "bibliotheque", voir migration
    fichiers_uploades_origine) : distingue un fichier envoyé en pièce
    jointe de conversation (jamais dans la liste "Mon espace >
    Bibliothèque", voir lister_fichiers) d'un fichier ajouté
    explicitement à une bibliothèque -- par défaut "bibliotheque", les 4
    appels depuis api/uploads.py (chat) passent "chat" explicitement.
    Renvoie la ligne insérée (avec son id et son url_publique).
    """
    extension = nom_fichier.rsplit(".", 1)[-1] if "." in nom_fichier else "bin"
    chemin_stockage = f"{niveau}/{uuid.uuid4()}.{extension}"

    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, contenu, {"content-type": type_mime}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload bibliothèque {chemin_stockage}) : {e}")
        raise

    url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    try:
        insertion = supabase.table("fichiers_uploades").insert({
            "niveau": niveau,
            "agent_id": agent_id,
            "user_id": user_id,
            "uploade_par": uploade_par,
            "chemin_stockage": chemin_stockage,
            "url_publique": url_publique,
            "nom_fichier": nom_fichier,
            "type_mime": type_mime,
            "description": description,
            "taille_octets": len(contenu),
            "origine": origine,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE fichiers_uploades ({chemin_stockage}) : {e}")
        raise

    return insertion.data[0]


def indexer_fichier_existant(
    url_publique: str,
    chemin_stockage: str,
    nom_fichier: str,
    type_mime: str,
    niveau: str,
    uploade_par: str,
    agent_id: str = None,
    user_id: str = None,
    description: str = None,
    taille_octets: int = None,
) -> dict:
    """
    Indexe dans fichiers_uploades un fichier DÉJÀ stocké ailleurs (ex.
    bucket images-publiques pour les images de chat) -- évite un second
    upload redondant vers le bucket "bibliotheque" quand le fichier
    existe déjà quelque part avec une URL publique utilisable.
    """
    try:
        insertion = supabase.table("fichiers_uploades").insert({
            "niveau": niveau,
            "agent_id": agent_id,
            "user_id": user_id,
            "uploade_par": uploade_par,
            "chemin_stockage": chemin_stockage,
            "url_publique": url_publique,
            "nom_fichier": nom_fichier,
            "type_mime": type_mime,
            "description": description,
            "taille_octets": taille_octets,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE fichiers_uploades (indexation {chemin_stockage}) : {e}")
        raise

    return insertion.data[0]


def chercher_fichiers(recherche: str, agent_id: str = None, user_id: str = None, limite: int = 10) -> list:
    """
    Cherche des fichiers accessibles dans le contexte courant (agent_id
    + user_id de la conversation en cours), tous niveaux confondus,
    triés du plus spécifique au plus large : utilisateur -> agent ->
    plateforme. `recherche` filtre sur le nom de fichier ou la
    description (recherche texte simple, pas de recherche sémantique
    pour l'instant).

    Un utilisateur non connecté (user_id=None) ne voit que les niveaux
    agent et plateforme -- pas d'erreur, juste moins de résultats.
    """
    niveaux_accessibles = ["plateforme"]
    if agent_id:
        niveaux_accessibles.append("agent")
    if user_id:
        niveaux_accessibles.append("utilisateur")

    requete = (
        supabase.table("fichiers_uploades")
        .select("*")
        .in_("niveau", niveaux_accessibles)
        .or_(f"nom_fichier.ilike.%{recherche}%,description.ilike.%{recherche}%")
        .limit(limite)
    )

    resultat = requete.execute()

    # Filtre applicatif : "agent" ne doit remonter que les fichiers du
    # bon agent, "utilisateur" que ceux du bon user -- .in_("niveau",...)
    # seul ne suffit pas à scoper correctement, ça ne fait que dire
    # quels NIVEAUX sont autorisés, pas QUEL agent/user précisément.
    fichiers = [
        f for f in resultat.data
        if f["niveau"] == "plateforme"
        or (f["niveau"] == "agent" and f["agent_id"] == agent_id)
        or (f["niveau"] == "utilisateur" and f["user_id"] == user_id)
    ]

    ordre_priorite = {"utilisateur": 0, "agent": 1, "plateforme": 2}
    fichiers.sort(key=lambda f: ordre_priorite[f["niveau"]])
    return fichiers


def lister_fichiers(niveau: str, agent_id: str = None, user_id: str = None, origine: str = None) -> list:
    """
    Liste exhaustive (pas une recherche par mot-clé) des fichiers d'un
    niveau précis -- utilisé pour l'écran de gestion du créateur
    ("ma bibliothèque pour cet agent"), pas par l'IA en conversation.
    `origine` (2026-08-01) : optionnel, filtre "chat" vs "bibliotheque"
    -- voir enregistrer_fichier.
    """
    requete = supabase.table("fichiers_uploades").select("*").eq("niveau", niveau)
    if agent_id:
        requete = requete.eq("agent_id", agent_id)
    if user_id:
        requete = requete.eq("user_id", user_id)
    if origine:
        requete = requete.eq("origine", origine)
    return requete.order("created_at", desc=True).execute().data


def supprimer_fichier(fichier_id: str) -> None:
    """
    Supprime un fichier de la bibliothèque : ligne en base ET objet
    Storage. Ne lève pas d'erreur si l'objet Storage est déjà absent
    (suppression déjà faite ailleurs, ou incohérence mineure) -- seule
    la suppression de la ligne en base est considérée critique.
    """
    ligne = supabase.table("fichiers_uploades").select("chemin_stockage").eq("id", fichier_id).execute()
    if not ligne.data:
        return

    chemin_stockage = ligne.data[0]["chemin_stockage"]
    try:
        supabase.storage.from_(BUCKET).remove([chemin_stockage])
    except Exception as e:
        logging.warning(f"Suppression Storage bibliothèque échouée ({chemin_stockage}), ligne supprimée quand même : {e}")

    supabase.table("fichiers_uploades").delete().eq("id", fichier_id).execute()
