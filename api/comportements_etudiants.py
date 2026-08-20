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
from core.bibliotheque_programme import TYPES_LIEN_COMPORTEMENT, proprietaire_lien_comportement
from core.comportements_etudiants import (
    lister_comportements,
    lister_comportements_par_lien,
    ajouter_comportement,
    modifier_comportement,
    attacher_comportement,
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
    lien_type: str | None = None
    lien_id: str | None = None


class ComportementPayload(BaseModel):
    texte: str
    # 18/08/2026, demande Bourama : nom d'affichage choisi par l'étudiant.
    # Vide/absent -> mode "auto" (nom généré côté serveur avec le skill).
    # Sur une modification, le frontend doit renvoyer le nom manuel actuel
    # s'il veut le préserver -- sinon il repasse en auto.
    nom: str | None = None
    # 20/08/2026, demande Bourama : "les comportements peuvent être créés
    # depuis le programme" -- rattachement optionnel dès la création.
    # Vérifié ci-dessous (proprietaire_lien_comportement) avant insertion,
    # jamais fait confiance au lien_id fourni tel quel.
    lien_type: str | None = None
    lien_id: str | None = None


class AttacherPayload(BaseModel):
    # None/None pour détacher explicitement.
    lien_type: str | None = None
    lien_id: str | None = None


def _verifier_lien(lien_type: str | None, lien_id: str | None, utilisateur_id: str) -> None:
    if lien_type is None and lien_id is None:
        return
    if lien_type is None or lien_id is None:
        raise erreur_api(400, "LIEN_INCOMPLET")
    if lien_type not in TYPES_LIEN_COMPORTEMENT:
        raise erreur_api(400, "TYPE_LIEN_INVALIDE")
    if proprietaire_lien_comportement(lien_type, lien_id) != utilisateur_id:
        raise erreur_api(404, "EMPLACEMENT_INTROUVABLE")


@router.get("", response_model=list[Comportement])
def lire_mes_comportements(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    return lister_comportements(agent_id, utilisateur.id)


@router.get("/par-lien/{lien_type}/{lien_id}", response_model=list[Comportement])
def lire_comportements_par_lien(agent_id: str, lien_type: str, lien_id: str, utilisateur=Depends(utilisateur_courant)):
    """Comportements de cet étudiant déjà attachés à CET emplacement
    précis -- pour l'afficher directement sur l'écran programme
    (chapitre, matière, examen, section...), 20/08/2026."""
    if lien_type not in TYPES_LIEN_COMPORTEMENT:
        raise erreur_api(400, "TYPE_LIEN_INVALIDE")
    return lister_comportements_par_lien(agent_id, utilisateur.id, lien_type, lien_id)


@router.post("", response_model=Comportement, status_code=201)
def ajouter_mon_comportement(agent_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.texte.strip():
        raise erreur_api(400, "TEXTE_REQUIS")
    _verifier_lien(payload.lien_type, payload.lien_id, utilisateur.id)
    return ajouter_comportement(
        agent_id, utilisateur.id, payload.texte, nom=payload.nom, lien_type=payload.lien_type, lien_id=payload.lien_id
    )


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


@router.patch("/{comportement_id}/lien", response_model=Comportement)
def attacher_mon_comportement(agent_id: str, comportement_id: str, payload: AttacherPayload, utilisateur=Depends(utilisateur_courant)):
    """Attache (ou détache si lien_type/lien_id sont None) un
    comportement déjà existant -- séparé de la modification du texte
    (20/08/2026, demande Bourama : "au moment de la création ou après
    tu peux l'attacher")."""
    _verifier_lien(payload.lien_type, payload.lien_id, utilisateur.id)
    resultat = attacher_comportement(agent_id, utilisateur.id, comportement_id, payload.lien_type, payload.lien_id)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return resultat


@router.delete("/{comportement_id}", status_code=204)
def supprimer_mon_comportement(agent_id: str, comportement_id: str, utilisateur=Depends(utilisateur_courant)):
    if not supprimer_comportement(agent_id, utilisateur.id, comportement_id):
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
