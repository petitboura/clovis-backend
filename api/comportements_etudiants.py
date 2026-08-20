"""
Section "Mes comportements" (2026-08-06, demande Bourama) : l'étudiant
peut enregistrer PLUSIEURS instructions perso, pas un seul texte
fourre-tout ("on peut en mettre plusieurs hein, pas juste un"). Chacune
s'ajoute EN PLUS du system_prompt déjà résolu pour le message -- que ce
soit le généraliste de base, celui d'un enseignant (matière débloquée
via core/contenu_dynamique_matiere.py), ou le prompt forcé via "Sans
enseignant" -- jamais un remplacement.

Mécanisme "à la skill" (13/08/2026) : l'étudiant saisit uniquement le
texte long (`texte`) -- la `description` courte est générée
automatiquement côté serveur (core/comportements_etudiants.py), jamais
par l'étudiant. Voir l'injection dans core/main.py::_construire_system_prompt
et l'outil consulter_comportement dans core/serveur_mcp_generation.py.

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
    obtenir_comportement_skill,
    modifier_skill_comportement,
)
from core.erreurs import erreur_api

router = APIRouter(prefix="/api/agents/{agent_id}/mes-comportements", tags=["comportements_etudiants"])


class Comportement(BaseModel):
    id: str
    texte: str
    description: str
    nom: str


class ComportementPayload(BaseModel):
    texte: str
    # 18/08/2026, demande Bourama : nom d'affichage choisi par l'étudiant.
    # Vide/absent -> mode "auto" (nom généré côté serveur avec le skill).
    # Sur une modification, le frontend doit renvoyer le nom manuel actuel
    # s'il veut le préserver -- sinon il repasse en auto.
    nom: str | None = None


@router.get("", response_model=list[Comportement])
def lire_mes_comportements(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    return lister_comportements(agent_id, utilisateur.id)


@router.post("", response_model=Comportement, status_code=201)
def ajouter_mon_comportement(agent_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.texte.strip():
        raise erreur_api(400, "TEXTE_REQUIS")
    return ajouter_comportement(agent_id, utilisateur.id, payload.texte, nom=payload.nom)


@router.patch("/{comportement_id}", response_model=Comportement)
def modifier_mon_comportement(agent_id: str, comportement_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.texte.strip():
        raise erreur_api(400, "TEXTE_REQUIS")
    resultat = modifier_comportement(agent_id, utilisateur.id, comportement_id, payload.texte, nom=payload.nom)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return resultat


class SkillComportement(BaseModel):
    skill_md: str


class SkillPayload(BaseModel):
    skill_md: str


@router.get("/{comportement_id}/skill", response_model=SkillComportement)
def lire_skill_comportement(agent_id: str, comportement_id: str, utilisateur=Depends(utilisateur_courant)):
    """18/08/2026, demande Bourama : onglet "Voir le skill généré" --
    lecture À LA DEMANDE (pas dans la liste principale, même philosophie
    que consulter_comportement côté chat), pour ne pas alourdir la liste
    avec un skill_md par ligne alors qu'on ne l'affiche que sur clic."""
    skill_md = obtenir_comportement_skill(agent_id, utilisateur.id, comportement_id)
    if skill_md is None:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return {"skill_md": skill_md}


@router.patch("/{comportement_id}/skill", response_model=Comportement)
def modifier_skill_mon_comportement(agent_id: str, comportement_id: str, payload: SkillPayload, utilisateur=Depends(utilisateur_courant)):
    """Édition DIRECTE du skill (frontmatter + corps), sans passer par
    le texte brut -- voir core/comportements_etudiants.py::modifier_skill_comportement.
    400 si le frontmatter fourni n'est pas un skill valide (pas de
    ---...--- ou pas de description:), pour ne jamais stocker un skill
    cassé que le routeur/l'IA ne saurait plus lire."""
    try:
        resultat = modifier_skill_comportement(agent_id, utilisateur.id, comportement_id, payload.skill_md)
    except ValueError as e:
        raise erreur_api(400, str(e))
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return resultat


@router.delete("/{comportement_id}", status_code=204)
def supprimer_mon_comportement(agent_id: str, comportement_id: str, utilisateur=Depends(utilisateur_courant)):
    if not supprimer_comportement(agent_id, utilisateur.id, comportement_id):
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
