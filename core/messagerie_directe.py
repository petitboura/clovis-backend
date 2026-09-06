"""
Extrait de api/roles.py le 06/09/2026, Bourama (Partie 9, découverte en
cours de route) : ces fonctions restent activement utilisées par l'outil
IA envoyer_message (core/outils_memoire_profil.py ->
core/serveur_mcp_generation.py), contrairement au reste de api/roles.py
et api/invitations_clovis.py, qui ne sont branchés sur aucune route
active et ont été déplacés en zone désactivée
(_desactive_roles_hierarchie/). Aucun changement de comportement,
uniquement un déplacement de code -- même logique, mêmes signatures.

Repose toujours sur l'ancienne hiérarchie de rôles
(profiles.role/etablissement_id/enseignant_id, migration
2026_08_04_roles_hierarchie.sql) via api.permissions_hierarchie._lire_profil_role
-- ce fichier ne fait PAS partie du nouveau système établissement de la
Partie 9 (voir core/etablissements.py), qui est indépendant et ne
réutilise rien d'ici.
"""

import logging
from typing import List, Optional

from api.auth import supabase
from api.permissions_hierarchie import _lire_profil_role


def _nom_affiche_ou_repli(user_id: str) -> str:
    try:
        res = (
            supabase.table("profiles")
            .select("nom_affiche")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (nom_affiche {user_id}) : {e}")
        res = None
    return ((res.data or {}).get("nom_affiche") if res else None) or "Sans nom"


def _etablissement_de_etudiant(etudiant: dict) -> Optional[str]:
    """
    L'étudiant n'a que `enseignant_id` en base, pas d'`etablissement_id`
    direct -- on remonte via son enseignant (même logique en deux niveaux
    que peut_gerer_base_connaissances dans permissions_hierarchie.py).
    """
    enseignant_id = etudiant.get("enseignant_id")
    if not enseignant_id:
        return None
    enseignant = _lire_profil_role(enseignant_id)
    return enseignant.get("etablissement_id") if enseignant else None


def _peut_echanger_messages(moi: dict, cible: dict) -> bool:
    """
    Établissement <-> enseignant (rattachement direct) ; enseignant <->
    son étudiant ; étudiant <-> étudiant du même établissement ; étudiant
    <-> son établissement (via son enseignant) -- élargi le 2026-08-04
    pour la messagerie enseignant/étudiant et l'outil IA envoyer_message.
    """
    role_moi, role_cible = moi.get("role"), cible.get("role")

    if role_moi == "etablissement" and role_cible == "enseignant":
        return cible.get("etablissement_id") == moi.get("user_id")
    if role_moi == "enseignant" and role_cible == "etablissement":
        return moi.get("etablissement_id") == cible.get("user_id")

    if role_moi == "enseignant" and role_cible == "etudiant":
        return cible.get("enseignant_id") == moi.get("user_id")
    if role_moi == "etudiant" and role_cible == "enseignant":
        return moi.get("enseignant_id") == cible.get("user_id")

    if role_moi == "etudiant" and role_cible == "etudiant":
        if moi.get("user_id") == cible.get("user_id"):
            return False
        etab_moi = _etablissement_de_etudiant(moi)
        return bool(etab_moi) and etab_moi == _etablissement_de_etudiant(cible)

    if role_moi == "etudiant" and role_cible == "etablissement":
        return _etablissement_de_etudiant(moi) == cible.get("user_id")
    if role_moi == "etablissement" and role_cible == "etudiant":
        return _etablissement_de_etudiant(cible) == moi.get("user_id")

    return False


def _profils_par_colonne(colonne: str, valeur: str, roles: tuple) -> List[dict]:
    try:
        res = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, role")
            .eq(colonne, valeur)
            .in_("role", roles)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (profils {colonne}={valeur}) : {e}")
        return []
    return res.data or []


def _contacts_autorises(moi: dict) -> List[dict]:
    """
    Liste les profils ({user_id, nom_affiche, role}) que `moi` a le droit
    de contacter d'après `_peut_echanger_messages` -- utilisée par
    l'outil IA envoyer_message (core/serveur_mcp_generation.py) pour
    résoudre un nom en destinataire, sans devoir tout parcourir.
    """
    role_moi, user_id = moi.get("role"), moi.get("user_id")
    resultats: List[dict] = []

    if role_moi == "etablissement":
        resultats += _profils_par_colonne("etablissement_id", user_id, ("enseignant",))
        # étudiants de tous ses enseignants
        for ens in _profils_par_colonne("etablissement_id", user_id, ("enseignant",)):
            resultats += _profils_par_colonne("enseignant_id", ens["user_id"], ("etudiant",))

    elif role_moi == "enseignant":
        etablissement_id = moi.get("etablissement_id")
        if etablissement_id:
            resultats.append(
                {
                    "user_id": etablissement_id,
                    "nom_affiche": _nom_affiche_ou_repli(etablissement_id),
                    "role": "etablissement",
                }
            )
        resultats += _profils_par_colonne("enseignant_id", user_id, ("etudiant",))

    elif role_moi == "etudiant":
        enseignant_id = moi.get("enseignant_id")
        enseignant = _lire_profil_role(enseignant_id) if enseignant_id else None
        if enseignant_id:
            resultats.append(
                {"user_id": enseignant_id, "nom_affiche": _nom_affiche_ou_repli(enseignant_id), "role": "enseignant"}
            )
        etablissement_id = enseignant.get("etablissement_id") if enseignant else None
        if etablissement_id:
            resultats.append(
                {
                    "user_id": etablissement_id,
                    "nom_affiche": _nom_affiche_ou_repli(etablissement_id),
                    "role": "etablissement",
                }
            )
            # tous les étudiants de l'établissement (tous enseignants confondus)
            for ens in _profils_par_colonne("etablissement_id", etablissement_id, ("enseignant",)):
                resultats += _profils_par_colonne("enseignant_id", ens["user_id"], ("etudiant",))

    return [r for r in resultats if r.get("user_id") != user_id]


def resoudre_destinataire_autorise(expediteur_id: str, nom_destinataire: str) -> tuple[Optional[str], Optional[str]]:
    """
    Résout `nom_destinataire` parmi les contacts autorisés de
    `expediteur_id`. Retourne (destinataire_id, erreur) -- l'un des deux
    vaut toujours None. Utilisée par l'outil IA envoyer_message.
    """
    moi = _lire_profil_role(expediteur_id)
    if not moi or not moi.get("role"):
        return None, "Cette fonctionnalité n'est pas disponible pour ce compte."
    moi["user_id"] = expediteur_id

    contacts = _contacts_autorises(moi)
    nom_normalise = nom_destinataire.strip().casefold()
    correspondances = [c for c in contacts if (c.get("nom_affiche") or "").strip().casefold() == nom_normalise]
    if not correspondances:
        correspondances = [c for c in contacts if nom_normalise in (c.get("nom_affiche") or "").strip().casefold()]

    if not correspondances:
        return None, f"Je ne trouve personne nommé {nom_destinataire} parmi tes contacts."
    if len(correspondances) > 1:
        noms = ", ".join(c["nom_affiche"] for c in correspondances)
        return None, f"Plusieurs personnes correspondent à {nom_destinataire} ({noms}) -- précise le nom complet."
    return correspondances[0]["user_id"], None


def _inserer_message(expediteur_id: str, destinataire_id: str, contenu: str, reponse_a: Optional[int] = None) -> dict:
    """
    Insertion brute dans messages_directs, sans vérification de droits
    (déjà faite par l'appelant) -- réutilisée par l'ancien POST
    /api/roles/messages (désactivé, voir _desactive_roles_hierarchie/) et
    par l'outil IA envoyer_message (core/serveur_mcp_generation.py).
    """
    res = (
        supabase.table("messages_directs")
        .insert(
            {
                "expediteur_id": expediteur_id,
                "destinataire_id": destinataire_id,
                "contenu": contenu.strip(),
                "reponse_a": reponse_a,
            }
        )
        .execute()
    )
    return res.data[0]
