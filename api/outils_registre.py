"""
Registre d'affichage des outils, exposé au frontend (2026-08-15, demande
Bourama : "qu'à un nouvel outil, on ne touche pas au frontend").

Source unique de vérité : core/registre_outils.py:REGISTRE_AFFICHAGE_OUTILS.
Cette route se contente de le sérialiser -- ajouter un outil ne nécessite
donc de toucher QUE registre_outils.py, jamais cette route ni le frontend.

Pas d'authentification requise (même logique que api/feedback.py:
lister_categories_negatif) : cette liste n'est pas une donnée utilisateur,
juste de la config d'affichage publique, nécessaire dès l'ouverture du
chat, avant même une éventuelle connexion.
"""

from fastapi import APIRouter

from registre_outils import REGISTRE_AFFICHAGE_OUTILS

router = APIRouter(prefix="/api/outils", tags=["outils"])


@router.get("/registre")
def lister_registre_affichage_outils():
    """
    Renvoie chaque outil sous la forme {nom, label, icone, onglet, appli}
    -- `appli` absent si non pertinent. Le frontend (classgpt-frontend/
    lib/outils.ts) convertit `icone` (nom lucide-react en chaîne) en
    composant React lui-même ; cette route ne connaît rien du rendu.

    `onglet` peut valoir None (17/08, demande Bourama) pour un outil qui
    doit garder son icône/label ici -- utile pour la bulle "résultat
    d'outil" côté frontend -- sans jamais apparaître comme bouton
    cliquable dans aucun menu (ex : les outils d'édition de programme,
    que le modèle appelle en autonomie). `.get("onglet")` plutôt qu'un
    accès direct pour ne pas planter sur ces entrées-là.
    """
    return {
        "outils": [
            {
                "nom": nom,
                "label": entree["label"],
                "icone": entree["icone"],
                "onglet": entree.get("onglet"),
                **({"appli": entree["appli"]} if "appli" in entree else {}),
            }
            for nom, entree in REGISTRE_AFFICHAGE_OUTILS.items()
        ]
    }
