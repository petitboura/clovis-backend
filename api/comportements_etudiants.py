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

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel

from api.auth import utilisateur_courant

# Fonctionnalité "Programme" désactivée et isolée le 29/08/2026 (demande
# Bourama, voir _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md).
# Le rattachement d'un comportement à un emplacement du programme
# (lien_type/lien_id) dépendait de core/bibliotheque_programme.py, retiré
# du code actif. Stub volontaire ci-dessous : plus aucun type de lien
# n'est autorisé (TYPES_LIEN_COMPORTEMENT vide), et les libellés/anciens
# liens déjà en base sont résolus à None au lieu de planter -- le reste
# de "Mes comportements" continue de fonctionner normalement.
TYPES_LIEN_COMPORTEMENT: tuple[str, ...] = ()


def libelle_emplacement(lien_type: str | None, lien_id: str | None) -> str | None:
    return None


def proprietaire_lien_comportement(lien_type: str | None, lien_id: str | None) -> str | None:
    return None
from core.comportements_etudiants import (
    lister_comportements,
    lister_comportements_par_lien,
    ajouter_comportement,
    importer_comportement_depuis_skill_md,
    modifier_comportement,
    attacher_comportement,
    supprimer_comportement,
    obtenir_comportement_skill,
    modifier_skill_comportement,
    activer_desactiver_comportement,
    publier_comportement_public,
    lister_comportements_publics,
    activer_comportement_public,
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
    lien_libelle: str | None = None
    actif: bool = True
    # 22/08/2026, demande Bourama : distinguer les 4 origines d'un skill
    # dans "Mes skills" (créé / téléchargé du public / attaché / audit).
    # Peuvent être vrais en même temps qu'un lien_type/lien_id -- voir
    # core/comportements_etudiants.py::lister_comportements.
    depuis_audit: bool = False
    depuis_public: bool = False
    # 22/08/2026, demande Bourama ("les audits regroupés par matière") :
    # renseigné uniquement pour un skill lié à un chapitre -- permet au
    # frontend de regrouper les skills d'audit par matière sans requête
    # supplémentaire. None pour tout le reste (lien matière/programme/
    # autre, ou pas de lien).
    matiere_id: str | None = None
    matiere_nom: str | None = None


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


def _avec_libelle(ligne: dict) -> dict:
    """Ajoute lien_libelle (20/08, pour affichage direct dans "Mes
    comportements" sans que le frontend ait à refaire un appel par
    comportement lié)."""
    if ligne.get("lien_type") and ligne.get("lien_id"):
        ligne = {**ligne, "lien_libelle": libelle_emplacement(ligne["lien_type"], ligne["lien_id"])}
    return ligne


@router.get("", response_model=list[Comportement])
def lire_mes_comportements(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    return [_avec_libelle(c) for c in lister_comportements(agent_id, utilisateur.id)]


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
    return _avec_libelle(
        ajouter_comportement(
            agent_id, utilisateur.id, payload.texte, nom=payload.nom, lien_type=payload.lien_type, lien_id=payload.lien_id
        )
    )


@router.post("/importer", response_model=Comportement, status_code=201)
async def importer_mon_comportement(
    agent_id: str,
    fichier: UploadFile = File(...),
    nom: str = Form(...),
    utilisateur=Depends(utilisateur_courant),
):
    """25/08/2026, demande Bourama : uploader un fichier .md directement
    dans "Mes comportements", gardé TEL QUEL (pas de régénération via
    l'IA) -- voir importer_comportement_depuis_skill_md."""
    if not nom.strip():
        raise erreur_api(400, "NOM_REQUIS")
    if not (fichier.filename or "").lower().endswith(".md"):
        raise erreur_api(400, "FICHIER_MD_REQUIS")

    contenu = (await fichier.read()).decode("utf-8", errors="replace").strip()
    if not contenu:
        raise erreur_api(400, "FICHIER_VIDE")

    return _avec_libelle(importer_comportement_depuis_skill_md(agent_id, utilisateur.id, nom, contenu))


@router.patch("/{comportement_id}", response_model=Comportement)
def modifier_mon_comportement(agent_id: str, comportement_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.texte.strip():
        raise erreur_api(400, "TEXTE_REQUIS")
    resultat = modifier_comportement(agent_id, utilisateur.id, comportement_id, payload.texte, nom=payload.nom)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return _avec_libelle(resultat)


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
    return _avec_libelle(resultat)


@router.delete("/{comportement_id}", status_code=204)
def supprimer_mon_comportement(agent_id: str, comportement_id: str, utilisateur=Depends(utilisateur_courant)):
    if not supprimer_comportement(agent_id, utilisateur.id, comportement_id):
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")


class ActifPayload(BaseModel):
    actif: bool


@router.patch("/{comportement_id}/actif", response_model=Comportement)
def activer_desactiver_mon_comportement(
    agent_id: str, comportement_id: str, payload: ActifPayload, utilisateur=Depends(utilisateur_courant)
):
    """21/08/2026, demande Bourama : "ajoute activer et désactiver aux
    comportements". Désactiver n'efface rien -- voir
    activer_desactiver_comportement pour ce que ça change concrètement."""
    resultat = activer_desactiver_comportement(agent_id, utilisateur.id, comportement_id, payload.actif)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return _avec_libelle(resultat)


@router.post("/{comportement_id}/publier", status_code=201)
def publier_mon_comportement(agent_id: str, comportement_id: str, utilisateur=Depends(utilisateur_courant)):
    """21/08/2026, demande Bourama : "je veux un onglet public...
    quelqu'un peut l'uploader". Publie une COPIE figée dans le catalogue
    public -- voir publier_comportement_public, l'original ici n'est ni
    modifié ni lié après coup."""
    resultat = publier_comportement_public(agent_id, utilisateur.id, comportement_id)
    if not resultat:
        raise erreur_api(404, "COMPORTEMENT_INTROUVABLE")
    return resultat
