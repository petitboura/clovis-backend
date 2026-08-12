"""
Permissions de la hiérarchie de rôles (nous/établissement/enseignant/
étudiant), ajoutée le 2026-08-04 (demande Bourama).

Un seul point de vérité pour "qui a le droit de toucher à l'agent de qui"
-- réutilisé par api/agents.py (édition du system_prompt, documents,
bibliothèque) plutôt que de dupliquer la logique de rattachement à
chaque endpoint. Voir migrations/2026_08_04_roles_hierarchie.sql pour le
schéma (profiles.role / etablissement_id / enseignant_id).

Portée volontairement DIFFÉRENTE entre les deux fonctions (décision
Bourama, 2026-08-04) :
- comportement (system_prompt) : établissement -> ses enseignants
  UNIQUEMENT (jamais direct sur un étudiant) ; enseignant -> ses étudiants.
- bases de connaissances (documents/bibliothèque) : établissement -> ses
  enseignants ET les étudiants de ces enseignants (deux niveaux) ;
  enseignant -> ses étudiants.

"Tester" (ouvrir le chat d'un agent supervisé) ne passe PAS par ce
module : api/chat.py est déjà accessible à tout le monde (voir
utilisateur_optionnel), aucune vérification de propriétaire n'existe
pour parler à un agent -- un bouton "Tester" côté frontend suffit, sans
rien à ajouter côté droits.
"""

import logging
from typing import Optional, TypedDict

from api.auth import supabase

logging.basicConfig(level=logging.INFO)


class ProfilRole(TypedDict):
    user_id: str
    role: Optional[str]
    etablissement_id: Optional[str]
    enseignant_id: Optional[str]


def _lire_profil_role(user_id: str) -> Optional[ProfilRole]:
    try:
        res = (
            supabase.table("profiles")
            .select("user_id, role, etablissement_id, enseignant_id")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture rôle profil {user_id}) : {e}")
        return None
    return res.data if res else None


def _est_admin(user_id: str) -> bool:
    profil = _lire_profil_role(user_id)
    return bool(profil and profil.get("role") == "admin")


def _est_administrateur_designe(utilisateur_id: str, agent_id: Optional[str]) -> bool:
    """
    Table `agents_administrateurs` (2026-08-05, onglet "Administrer" de
    Mon espace) : administration confiée par Bourama sur un agent précis,
    indépendamment du système owner/rôle hiérarchique ci-dessous. `agent_id`
    est optionnel car certains appelants historiques ne le passent pas
    encore -- dans ce cas ce droit est simplement ignoré (pas de régression).
    """
    if not agent_id:
        return False
    try:
        res = (
            supabase.table("agents_administrateurs")
            .select("agent_id")
            .eq("agent_id", agent_id)
            .eq("user_id", utilisateur_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents_administrateurs {agent_id}/{utilisateur_id}) : {e}")
        return False
    return bool(res and res.data)


def peut_modifier_comportement(utilisateur_id: str, owner_id: str, agent_id: Optional[str] = None) -> bool:
    """
    system_prompt de l'agent appartenant à `owner_id` : le propriétaire
    lui-même, l'admin, l'administrateur désigné (agents_administrateurs),
    l'enseignant de cet étudiant, ou l'établissement de cet enseignant
    (jamais l'établissement direct sur un étudiant).
    """
    if utilisateur_id == owner_id:
        return True
    if _est_admin(utilisateur_id):
        return True
    if _est_administrateur_designe(utilisateur_id, agent_id):
        return True

    moi = _lire_profil_role(utilisateur_id)
    cible = _lire_profil_role(owner_id)
    if not moi or not cible:
        return False

    if moi.get("role") == "enseignant" and cible.get("role") == "etudiant":
        return cible.get("enseignant_id") == utilisateur_id
    if moi.get("role") == "etablissement" and cible.get("role") == "enseignant":
        return cible.get("etablissement_id") == utilisateur_id
    return False


def peut_gerer_base_connaissances(utilisateur_id: str, owner_id: str, agent_id: Optional[str] = None) -> bool:
    """
    Documents/bibliothèque (RAG) de l'agent appartenant à `owner_id` :
    même portée que peut_modifier_comportement (donc administrateur désigné
    inclus), SAUF pour l'établissement qui a ici un droit supplémentaire
    direct sur les étudiants de ses enseignants (deux niveaux), en plus de
    ses enseignants.
    """
    if peut_modifier_comportement(utilisateur_id, owner_id, agent_id):
        return True

    moi = _lire_profil_role(utilisateur_id)
    cible = _lire_profil_role(owner_id)
    if not moi or not cible:
        return False

    if moi.get("role") == "etablissement" and cible.get("role") == "etudiant":
        enseignant_id = cible.get("enseignant_id")
        if not enseignant_id:
            return False
        enseignant = _lire_profil_role(enseignant_id)
        return bool(enseignant and enseignant.get("etablissement_id") == utilisateur_id)
    return False
