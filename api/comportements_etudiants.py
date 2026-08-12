"""
Section "Mes comportements" (2026-08-06, demande Bourama) : l'étudiant
peut enregistrer PLUSIEURS instructions perso, pas un seul texte
fourre-tout ("on peut en mettre plusieurs hein, pas juste un"). Chacune
s'ajoute EN PLUS du system_prompt déjà résolu pour le message -- que ce
soit le généraliste de base, celui d'un enseignant (matière débloquée
via core/contenu_dynamique_matiere.py), ou le prompt forcé via "Sans
enseignant" -- jamais un remplacement. Voir l'injection dans
core/main.py::_construire_system_prompt (logique de lecture/écriture
partagée dans core/comportements_etudiants.py).

Affichage de la section côté frontend piloté par
agents.section_mes_comportements (comme agents.bouton_sans_enseignant) --
pas encore automatique, un simple interrupteur qu'on met nous-mêmes en
base. Ces endpoints restent néanmoins accessibles pour N'IMPORTE QUEL
agent même si section_mes_comportements est false : le flag ne gate QUE
l'affichage côté frontend, pas la lecture/écriture ici (même philosophie
que sans_enseignant côté chat -- inoffensif si jamais appelé pour un
agent qui n'affiche pas la section).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.comportements_etudiants import (
    lister_comportements,
    ajouter_comportement,
    modifier_comportement,
    supprimer_comportement,
)
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/agents/{agent_id}/mes-comportements", tags=["comportements_etudiants"])


class Comportement(BaseModel):
    id: str
    texte: str


class ComportementPayload(BaseModel):
    texte: str


@router.get("", response_model=list[Comportement])
def lire_mes_comportements(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    return lister_comportements(agent_id, utilisateur.id)


@router.post("", response_model=Comportement, status_code=201)
def ajouter_mon_comportement(agent_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.texte.strip():
        raise erreur_api(400, "TEXTE_REQUIS")
    return ajouter_comportement(agent_id, utilisateur.id, payload.texte)


@router.patch("/{comportement_id}", response_model=Comportement)
def modifier_mon_comportement(agent_id: str, comportement_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.texte.strip():
        raise erreur_api(400, "TEXTE_REQUIS")
    resultat = modifier_comportement(agent_id, utilisateur.id, comportement_id, payload.texte)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return resultat


@router.delete("/{comportement_id}", status_code=204)
def supprimer_mon_comportement(agent_id: str, comportement_id: str, utilisateur=Depends(utilisateur_courant)):
    if not supprimer_comportement(agent_id, utilisateur.id, comportement_id):
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
