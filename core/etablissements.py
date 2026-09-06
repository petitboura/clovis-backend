"""
Créé le 06/09/2026, Bourama : fondations du système établissement (Partie 9
du chantier "confiance pédagogique", voir Point 6 du document de vision --
uniquement la partie établissement, pas la cascade qui est la Partie 10).

Décision prise avec Bourama avant d'écrire ce fichier (constat d'audit n°1
du plan de travail) : nouvelles tables (`etablissements`,
`etablissements_rattachements`, `etablissements_publications`), aucun lien
avec l'ancien profiles.role/etablissement_id/enseignant_id (migration
2026-08-04, api/roles.py) ni avec api/permissions_hierarchie.py, laissé
intouché. Voir migrations/2026_09_06_etablissements.sql.

Trois états de rattachement (voir tableau du document de vision) :
- "suivi" : action immédiate, pas de validation. Contenu public + notif
  sur le contenu public uniquement. Ne compte pas pour la cascade.
- "demande_en_attente" : action "se connecter", en attente de validation
  par l'établissement.
- "accepte" : rattachement réel validé. Contenu privé en plus du public,
  notifié sur toute publication (publique ou privée). Compte pour la
  cascade (Partie 10, pas construite ici).
"""

import logging

from api.auth import supabase
from core.erreurs import erreur_api
from core.notifications import creer_notification


# ---------------------------------------------------------------------------
# Profil établissement
# ---------------------------------------------------------------------------

def declarer_etablissement(profile_id: str, nom: str, description: str | None = None,
                            site_web: str | None = None, contact: str | None = None) -> dict:
    """Déclare le compte `profile_id` comme établissement (case à cocher à
    l'inscription, ou plus tard depuis les paramètres). Un compte ne peut
    avoir qu'un seul profil établissement (contrainte unique profile_id)."""
    if not nom or not nom.strip():
        raise erreur_api(400, "ETABLISSEMENT_NOM_REQUIS")

    existant = obtenir_etablissement_par_profile(profile_id)
    if existant:
        raise erreur_api(409, "ETABLISSEMENT_DEJA_DECLARE")

    try:
        res = (
            supabase.table("etablissements")
            .insert({
                "profile_id": profile_id,
                "nom": nom.strip(),
                "description": description,
                "site_web": site_web,
                "contact": contact,
            })
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (declarer_etablissement profile_id={profile_id}) : {e}")
        raise erreur_api(500, "ETABLISSEMENT_DEJA_DECLARE")
    return res.data[0]


def modifier_profil_etablissement(etablissement_id: str, acteur_id: str, patch: dict) -> dict:
    """Modification partielle du profil établissement -- seuls les champs
    fournis (non None) sont mis à jour. Réservé au propriétaire du profil."""
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")
    if etablissement["profile_id"] != acteur_id:
        raise erreur_api(403, "SEUL_ETABLISSEMENT_PEUT_ACCEPTER")

    champs_autorises = {"nom", "description", "site_web", "contact", "actif"}
    mise_a_jour = {k: v for k, v in patch.items() if k in champs_autorises and v is not None}
    if not mise_a_jour:
        return etablissement

    res = supabase.table("etablissements").update(mise_a_jour).eq("id", etablissement_id).execute()
    return res.data[0] if res.data else etablissement


def obtenir_etablissement(etablissement_id: str) -> dict | None:
    res = supabase.table("etablissements").select("*").eq("id", etablissement_id).execute()
    return res.data[0] if res.data else None


def obtenir_etablissement_par_profile(profile_id: str) -> dict | None:
    res = supabase.table("etablissements").select("*").eq("profile_id", profile_id).execute()
    return res.data[0] if res.data else None


def lister_etablissements_publics() -> list[dict]:
    """Section publique -- n'importe qui peut parcourir les établissements
    actifs. Pas de pagination pour l'instant (volume attendu faible au
    lancement de cette fonctionnalité)."""
    res = (
        supabase.table("etablissements")
        .select("id, nom, description, site_web, contact, created_at")
        .eq("actif", True)
        .order("nom")
        .execute()
    )
    return res.data or []


# ---------------------------------------------------------------------------
# Rattachements (suivre / se connecter / accepter)
# ---------------------------------------------------------------------------

def _obtenir_rattachement(etablissement_id: str, utilisateur_id: str) -> dict | None:
    res = (
        supabase.table("etablissements_rattachements")
        .select("*")
        .eq("etablissement_id", etablissement_id)
        .eq("utilisateur_id", utilisateur_id)
        .execute()
    )
    return res.data[0] if res.data else None


def suivre_etablissement(etablissement_id: str, utilisateur_id: str) -> dict:
    """Action "suivre" : immédiate, jamais de validation. N'écrase jamais
    un état plus fort (demande_en_attente ou accepte) déjà existant."""
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")

    existant = _obtenir_rattachement(etablissement_id, utilisateur_id)
    if existant:
        return existant

    res = (
        supabase.table("etablissements_rattachements")
        .insert({"etablissement_id": etablissement_id, "utilisateur_id": utilisateur_id, "etat": "suivi"})
        .execute()
    )
    return res.data[0]


def demander_connexion(etablissement_id: str, utilisateur_id: str) -> dict:
    """Action "se connecter" : part comme une demande, doit être validée
    explicitement par l'établissement. Met à niveau un "suivi" existant,
    n'écrase jamais un "accepte" déjà obtenu."""
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")

    existant = _obtenir_rattachement(etablissement_id, utilisateur_id)
    if existant and existant["etat"] in ("demande_en_attente", "accepte"):
        return existant

    if existant:
        res = (
            supabase.table("etablissements_rattachements")
            .update({"etat": "demande_en_attente"})
            .eq("id", existant["id"])
            .execute()
        )
        rattachement = res.data[0]
    else:
        res = (
            supabase.table("etablissements_rattachements")
            .insert({"etablissement_id": etablissement_id, "utilisateur_id": utilisateur_id, "etat": "demande_en_attente"})
            .execute()
        )
        rattachement = res.data[0]

    creer_notification(
        user_id=etablissement["profile_id"],
        type_notif="etablissement_demande_connexion",
        titre="Nouvelle demande de rattachement",
        contenu=f"Quelqu'un demande à se connecter à {etablissement['nom']}.",
        lien="/bureau/etablissement/rattachements",
    )
    return rattachement


def accepter_rattachement(rattachement_id: str, acteur_id: str) -> dict:
    """Réservé à l'établissement concerné. Fait passer le rattachement en
    "accepte" (rattachement réel, contenu privé, compte pour la cascade)."""
    res = supabase.table("etablissements_rattachements").select("*").eq("id", rattachement_id).execute()
    rattachement = res.data[0] if res.data else None
    if not rattachement:
        raise erreur_api(404, "RATTACHEMENT_INTROUVABLE")

    etablissement = obtenir_etablissement(rattachement["etablissement_id"])
    if not etablissement or etablissement["profile_id"] != acteur_id:
        raise erreur_api(403, "SEUL_ETABLISSEMENT_PEUT_ACCEPTER")

    res = (
        supabase.table("etablissements_rattachements")
        .update({"etat": "accepte"})
        .eq("id", rattachement_id)
        .execute()
    )
    mise_a_jour = res.data[0]

    creer_notification(
        user_id=rattachement["utilisateur_id"],
        type_notif="etablissement_demande_acceptee",
        titre="Rattachement accepté",
        contenu=f"{etablissement['nom']} a accepté ta demande de rattachement.",
        lien="/etablissements",
    )
    return mise_a_jour


def lister_rattachements(etablissement_id: str, acteur_id: str, etat: str | None = None) -> list[dict]:
    """Listage par état, réservé à l'établissement concerné (voir ses
    propres demandes/abonnés/rattachés)."""
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")
    if etablissement["profile_id"] != acteur_id:
        raise erreur_api(403, "SEUL_ETABLISSEMENT_PEUT_LISTER_RATTACHEMENTS")

    requete = supabase.table("etablissements_rattachements").select("*").eq("etablissement_id", etablissement_id)
    if etat:
        requete = requete.eq("etat", etat)
    res = requete.order("created_at", desc=True).execute()
    return res.data or []


def lister_mes_rattachements(utilisateur_id: str) -> list[dict]:
    """Rattachements de l'utilisateur courant, avec le nom de chaque
    établissement pour l'affichage."""
    res = (
        supabase.table("etablissements_rattachements")
        .select("*, etablissements(id, nom)")
        .eq("utilisateur_id", utilisateur_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


# ---------------------------------------------------------------------------
# Publications au nom de l'établissement
# ---------------------------------------------------------------------------

def publier(etablissement_id: str, auteur_id: str, contenu: str, titre: str | None = None,
            visibilite: str = "publique") -> dict:
    """Publication au nom de l'établissement. L'établissement lui-même
    publie directement (validé tout de suite) ; une personne en
    rattachement accepté peut aussi publier, mais sa publication reste
    "en_attente" jusqu'à validation explicite de l'établissement (voir
    valider_publication)."""
    if not contenu or not contenu.strip():
        raise erreur_api(400, "PUBLICATION_CONTENU_REQUIS")
    if visibilite not in ("publique", "privee"):
        visibilite = "publique"

    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")

    est_etablissement = etablissement["profile_id"] == auteur_id
    if not est_etablissement:
        rattachement = _obtenir_rattachement(etablissement_id, auteur_id)
        if not rattachement or rattachement["etat"] != "accepte":
            raise erreur_api(403, "RATTACHEMENT_ACCEPTE_REQUIS_POUR_PUBLIER")

    statut = "valide" if est_etablissement else "en_attente"
    ligne = {
        "etablissement_id": etablissement_id,
        "auteur_id": auteur_id,
        "titre": titre,
        "contenu": contenu.strip(),
        "visibilite": visibilite,
        "statut": statut,
    }
    if statut == "valide":
        ligne["valide_le"] = "now()"
        ligne["valide_par"] = etablissement["profile_id"]

    res = supabase.table("etablissements_publications").insert(ligne).execute()
    publication = res.data[0]

    if statut == "valide":
        _notifier_fanout_publication(etablissement, publication)
    return publication


def valider_publication(publication_id: str, acteur_id: str, approuver: bool) -> dict:
    """Réservé à l'établissement concerné. Valide (diffuse) ou refuse une
    publication soumise par une personne en rattachement accepté."""
    res = supabase.table("etablissements_publications").select("*").eq("id", publication_id).execute()
    publication = res.data[0] if res.data else None
    if not publication:
        raise erreur_api(404, "PUBLICATION_INTROUVABLE")

    etablissement = obtenir_etablissement(publication["etablissement_id"])
    if not etablissement or etablissement["profile_id"] != acteur_id:
        raise erreur_api(403, "SEUL_ETABLISSEMENT_PEUT_VALIDER")

    nouveau_statut = "valide" if approuver else "refuse"
    mise_a_jour = {"statut": nouveau_statut}
    if approuver:
        mise_a_jour["valide_le"] = "now()"
        mise_a_jour["valide_par"] = acteur_id

    res = supabase.table("etablissements_publications").update(mise_a_jour).eq("id", publication_id).execute()
    publication = res.data[0]

    if approuver:
        _notifier_fanout_publication(etablissement, publication)
    return publication


def lister_publications_en_attente(etablissement_id: str, acteur_id: str) -> list[dict]:
    """Réservé à l'établissement concerné -- publications soumises par des
    personnes rattachées, en attente de validation."""
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")
    if etablissement["profile_id"] != acteur_id:
        raise erreur_api(403, "SEUL_ETABLISSEMENT_PEUT_VALIDER")

    res = (
        supabase.table("etablissements_publications")
        .select("*")
        .eq("etablissement_id", etablissement_id)
        .eq("statut", "en_attente")
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


def lister_publications(etablissement_id: str, viewer_id: str | None = None) -> list[dict]:
    """Publications validées, visibles par viewer_id selon son état de
    rattachement -- publique pour tout le monde (y compris non connecté),
    privée uniquement pour un rattachement accepté."""
    etablissement = obtenir_etablissement(etablissement_id)
    if not etablissement:
        raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")

    peut_voir_prive = False
    if viewer_id:
        rattachement = _obtenir_rattachement(etablissement_id, viewer_id)
        peut_voir_prive = bool(rattachement and rattachement["etat"] == "accepte")

    requete = (
        supabase.table("etablissements_publications")
        .select("*")
        .eq("etablissement_id", etablissement_id)
        .eq("statut", "valide")
    )
    if not peut_voir_prive:
        requete = requete.eq("visibilite", "publique")
    res = requete.order("created_at", desc=True).execute()
    return res.data or []


def _notifier_fanout_publication(etablissement: dict, publication: dict) -> None:
    """Notifie les personnes concernées par une publication qui vient
    d'être diffusée : "suivi" seulement si publique, "accepte" dans tous
    les cas (publique ou privée) -- voir tableau du document de vision."""
    etats_cibles = ["suivi", "accepte"] if publication["visibilite"] == "publique" else ["accepte"]
    res = (
        supabase.table("etablissements_rattachements")
        .select("utilisateur_id, etat")
        .eq("etablissement_id", etablissement["id"])
        .in_("etat", etats_cibles)
        .execute()
    )
    titre_publication = publication.get("titre") or "Nouvelle publication"
    for ligne in res.data or []:
        creer_notification(
            user_id=ligne["utilisateur_id"],
            type_notif="etablissement_publication",
            titre=f"{etablissement['nom']} a publié",
            contenu=titre_publication,
            lien=f"/etablissements/{etablissement['id']}",
        )
