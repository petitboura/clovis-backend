"""
Outil MCP pour les dossiers du catalogue public (bibliothèque publique) --
09/09/2026, demande Bourama : "il faut [que le LLM puisse] ouvrir y
naviguer, y agir dans les libres au nom de l'user et pouvoir voir le
statut d'un dossier". Avant cet ajout, le LLM n'avait AUCUN moyen de
savoir qu'un dossier public existait (voir core/catalogue_public_rag.py,
qui dit lui-même "pas de dossiers ici").

Nouvel outil séparé (demande explicite de Bourama), pas des actions
ajoutées à gerer_document_bibliotheque -- même pattern "action +
paramètres" que gerer_dossier_bibliotheque (dossiers PERSONNELS), mais
adapté aux dossiers PUBLICS : visibles par tout le monde, statut
contribution_libre/privee (voir core/dossiers_catalogue_public.py pour
le détail des droits), jamais de suppression des documents eux-mêmes
au retrait d'un dossier (contrairement au perso).
"""

import logging

from core.outils_generation_commun import mcp_generation, Context
from core.dossiers_catalogue_public import (
    supabase as _supabase,
    _dossier as _obtenir_dossier,
    creer_dossier as _creer_dossier,
    lister_dossiers as _lister_dossiers,
    lister_fichiers_ids_dossier as _lister_fichiers_ids_dossier,
    ranger_fichier as _ranger_fichier,
    retirer_fichier as _retirer_fichier,
    renommer_dossier as _renommer_dossier,
    supprimer_dossier as _supprimer_dossier,
    peut_ajouter_contenu as _peut_ajouter_contenu,
    peut_retirer_contenu as _peut_retirer_contenu,
)
from core.dossiers_publics_attaches import propager_fichier_public_range_dossier as _propager_fichier_public_range_dossier
from core.listes_bibliotheque_publique import lister_valeurs as _lister_valeurs

STATUTS_VALIDES = ("contribution_libre", "privee")


@mcp_generation.tool()
def gerer_dossier_catalogue_public(
    action: str,
    ctx: Context,
    dossier_id: str = "",
    nom: str = "",
    dossier_parent_id: str = "",
    nouveau_nom: str = "",
    statut: str = "",
    description: str = "",
    pays: str = "",
    niveau: str = "",
    categorie: str = "",
    classe: str = "",
    specialite: str = "",
    fichier_id: str = "",
) -> str:
    """
    Gère les dossiers du CATALOGUE PUBLIC (section "Bibliothèque
    publique", ouvert à tout le monde) -- distinct de
    gerer_dossier_bibliotheque (dossiers PERSONNELS de cet utilisateur
    uniquement). Pour les filtres de RECHERCHE dans le catalogue public
    lui-même, voir gerer_document_bibliotheque (actions
    trouver_catalogue_public/lister_catalogue_public).

    `action` doit être l'une de :
    - "lister_valeurs_filtres" : renvoie, pour chacun des 5 filtres
      (pays/niveau/catégorie/classe/spécialité), la liste des valeurs
      RÉELLEMENT déjà utilisées dans le catalogue public aujourd'hui
      (10/09/2026, correctif suite bug remonté par Bourama : le LLM
      inventait des valeurs de filtre au hasard -- ex. confondait
      "niveau" et "classe" -- faute de savoir lesquelles existent
      vraiment). RÈGLE ABSOLUE : appelle TOUJOURS cette action avant
      d'utiliser un filtre pays/niveau/catégorie/classe/spécialité dans
      trouver_catalogue_public/lister_catalogue_public (voir
      gerer_document_bibliotheque) si l'étudiant n'a pas donné la
      valeur exacte lui-même -- ne devine JAMAIS un nom de filtre.
      Aucun paramètre.
    - "lister" : liste TOUS les dossiers publics à plat, avec pour
      chacun son chemin (dossier parent > dossier), son STATUT
      ("contribution_libre" = tout le monde peut y ajouter un document,
      "privee" = seul son créateur le peut), les filtres renseignés
      (pays/niveau/catégorie/classe/spécialité) et son nombre de
      fichiers directs. Aucun paramètre.
    - "consulter" : ouvre un dossier précis, liste ses sous-dossiers et
      ses fichiers directs, et rappelle son statut. Ne descend pas
      récursivement, rappelle avec l'id d'un sous-dossier pour y entrer.
      Paramètre : `dossier_id`.
    - "creer" : crée un dossier public. Paramètres : `nom` ; `statut`
      OBLIGATOIRE, valeur EXACTE "contribution_libre" ou "privee" --
      RÈGLE ABSOLUE (demande explicite de Bourama) : tu ne dois JAMAIS
      choisir ce statut toi-même ni appliquer une valeur par défaut,
      TOUJOURS demander explicitement à l'étudiant lequel il veut avant
      d'appeler cette action (explique-lui la différence si besoin :
      "contribution_libre" = n'importe qui peut y ranger un document
      ensuite, "privee" = lui seul pourra). `dossier_parent_id`
      optionnel (sous-dossier). `description`, `pays`, `niveau`,
      `categorie`, `classe`, `specialite` tous optionnels -- à ne
      remplir QUE si l'étudiant les mentionne clairement, jamais
      devinés.
    - "renommer" : renomme un dossier public existant. Réservé au
      créateur du dossier, même si "contribution_libre". Paramètres :
      `dossier_id`, `nouveau_nom`.
    - "supprimer" : supprime DÉFINITIVEMENT un dossier public (et ses
      sous-dossiers). Ne supprime JAMAIS les documents qu'il contenait
      (ressources partagées par la communauté, pas la propriété du
      dossier) -- ils redeviennent seulement non classés. Réservé au
      créateur du dossier, même si "contribution_libre". Paramètre :
      `dossier_id`. SENSIBLE : demande toujours confirmation à
      l'utilisateur avant d'être exécuté, quelle que soit la
      formulation de sa demande.
    - "ranger_fichier" : range un document déjà publié dans le
      catalogue public (obtenu via gerer_document_bibliotheque) dans un
      dossier public. Autorisé si l'étudiant est le créateur du dossier
      OU si le dossier est "contribution_libre" (dans ce cas, agis bien
      au nom de l'étudiant -- n'importe quel étudiant peut contribuer à
      un dossier libre). Paramètres : `fichier_id`, `dossier_id`.
    - "retirer_fichier" : retire un document d'un dossier public
      (le document lui-même n'est jamais supprimé, juste détaché de ce
      dossier). Réservé au créateur du dossier, même si
      "contribution_libre". Paramètres : `fichier_id`, `dossier_id`.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "lister_valeurs_filtres":
        lignes = []
        for champ, libelle in (
            ("pays", "pays"), ("niveau", "niveau"), ("categorie", "catégorie"),
            ("classe", "classe"), ("specialite", "spécialité"),
        ):
            try:
                valeurs = _lister_valeurs(champ)
            except Exception as e:
                logging.error(f"ERREUR gerer_dossier_catalogue_public (lister_valeurs_filtres, champ {champ}) : {e}")
                valeurs = []
            lignes.append(f"- {libelle} : " + (", ".join(valeurs) if valeurs else "aucune valeur enregistrée pour l'instant"))
        return "Valeurs déjà utilisées dans le catalogue public :\n" + "\n".join(lignes)

    if action == "lister":
        try:
            dossiers = _lister_dossiers()
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (lister) : {e}")
            return "Erreur : impossible de lister les dossiers du catalogue public, réessaie."
        if not dossiers:
            return "Aucun dossier public pour l'instant."
        par_id = {d["id"]: d for d in dossiers}
        lignes = []
        for d in dossiers:
            parent = par_id.get(d["dossier_parent_id"])
            chemin = f"{parent['nom']} > {d['nom']}" if parent else d["nom"]
            try:
                nb_fichiers = len(_lister_fichiers_ids_dossier(d["id"]))
            except Exception:
                nb_fichiers = 0
            # 13/09/2026 : chaque filtre est désormais une liste (un
            # dossier peut avoir plusieurs valeurs) -- affichage joint
            # par "/" plutôt que la représentation Python brute d'une liste.
            filtres = ", ".join(
                f"{cle}={'/'.join(d[cle])}" for cle in ("pays", "niveau", "categorie", "classe", "specialite") if d.get(cle)
            )
            ligne = f"- {chemin} [id: {d['id']}] (statut: {d['statut']}, {nb_fichiers} fichier(s) direct(s))"
            if filtres:
                ligne += f" [{filtres}]"
            lignes.append(ligne)
        return "\n".join(lignes)

    if action == "consulter":
        dossier = _obtenir_dossier(dossier_id)
        if dossier is None:
            return "Ce dossier public est introuvable."
        try:
            dossiers = _lister_dossiers()
            sous_dossiers = [d for d in dossiers if d["dossier_parent_id"] == dossier_id]
            fichier_ids = _lister_fichiers_ids_dossier(dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (consulter) : {e}")
            return "Erreur : impossible de consulter ce dossier, réessaie."

        lignes = [f"Statut de ce dossier : {dossier['statut']}."]
        for sd in sous_dossiers:
            lignes.append(f"- [dossier] {sd['nom']} [id: {sd['id']}] (statut: {sd['statut']})")
        for f_id in fichier_ids:
            try:
                res = _supabase.table("bibliotheque_publique").select("nom, description, type_mime").eq("id", f_id).maybe_single().execute()
            except Exception as e:
                logging.error(f"ERREUR gerer_dossier_catalogue_public (consulter, lecture fichier {f_id}) : {e}")
                continue
            if not res or not res.data:
                continue
            f = res.data
            lignes.append(f"- [fichier] {f.get('nom')} ({f.get('type_mime', 'inconnu')}) [id: {f_id}]" + (f" -- {f['description']}" if f.get("description") else ""))
        if len(lignes) == 1:
            lignes.append("Ce dossier est vide.")
        return "\n".join(lignes)

    if action == "creer":
        nom_val = (nom or "").strip()
        if not nom_val:
            return "Erreur : nom de dossier manquant."
        statut_val = (statut or "").strip()
        if statut_val not in STATUTS_VALIDES:
            return (
                "Erreur : avant de créer ce dossier, demande explicitement à l'étudiant s'il doit être "
                "\"contribution_libre\" (n'importe qui peut y ranger un document) ou \"privee\" (lui seul) -- "
                "ne choisis jamais ce statut toi-même. Rappelle cette action avec `statut` valant exactement "
                "l'un des deux une fois qu'il a répondu."
            )
        parent_id = (dossier_parent_id or "").strip() or None
        if parent_id and _obtenir_dossier(parent_id) is None:
            return "Erreur : le dossier parent indiqué est introuvable."
        try:
            dossier = _creer_dossier(
                user_id, nom_val, statut=statut_val, dossier_parent_id=parent_id,
                # 13/09/2026 : creer_dossier attend désormais une liste par
                # filtre (un dossier peut avoir plusieurs valeurs) -- cet
                # outil ne propose qu'une seule valeur à la fois, on
                # l'enveloppe simplement dans une liste d'un élément.
                pays=[(pays or "").strip()] if (pays or "").strip() else [],
                niveau=[(niveau or "").strip()] if (niveau or "").strip() else [],
                categorie=[(categorie or "").strip()] if (categorie or "").strip() else [],
                classe=[(classe or "").strip()] if (classe or "").strip() else [],
                specialite=[(specialite or "").strip()] if (specialite or "").strip() else [],
                description=(description or "").strip(),
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (creer) : {e}")
            return "Erreur : impossible de créer ce dossier, réessaie."
        return f"Dossier public « {nom_val} » créé (statut: {statut_val}) [id: {dossier['id']}]."

    if action == "renommer":
        dossier = _obtenir_dossier(dossier_id)
        if dossier is None:
            return "Ce dossier public est introuvable."
        if not _peut_retirer_contenu(dossier_id, user_id):
            return "Erreur : seul le créateur de ce dossier peut le renommer."
        nouveau_nom_val = (nouveau_nom or "").strip()
        if not nouveau_nom_val:
            return "Erreur : nouveau nom manquant."
        try:
            _renommer_dossier(dossier_id, nouveau_nom_val)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (renommer) : {e}")
            return "Erreur : impossible de renommer ce dossier, réessaie."
        return f"Dossier public renommé en « {nouveau_nom_val} »."

    if action == "supprimer":
        dossier = _obtenir_dossier(dossier_id)
        if dossier is None:
            return "Ce dossier public est introuvable."
        if not _peut_retirer_contenu(dossier_id, user_id):
            return "Erreur : seul le créateur de ce dossier peut le supprimer."
        try:
            _supprimer_dossier(dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (supprimer) : {e}")
            return "Erreur : impossible de supprimer ce dossier, réessaie."
        return "Dossier public supprimé (les documents qu'il contenait restent dans le catalogue public, juste non classés)."

    if action == "ranger_fichier":
        if not (fichier_id or "").strip() or not (dossier_id or "").strip():
            return "Erreur : fichier_id et dossier_id sont obligatoires."
        if _obtenir_dossier(dossier_id) is None:
            return "Ce dossier public est introuvable."
        if not _peut_ajouter_contenu(dossier_id, user_id):
            return "Erreur : ce dossier est privé, seul son créateur peut y ranger un document."
        try:
            res = _supabase.table("bibliotheque_publique").select("id, statut").eq("id", fichier_id).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (ranger_fichier, vérif fichier {fichier_id}) : {e}")
            return "Erreur : impossible de vérifier ce document, réessaie."
        if not res or not res.data or res.data.get("statut") != "publie":
            return "Erreur : ce document n'existe pas dans le catalogue public (ou n'y est plus)."
        try:
            _ranger_fichier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (ranger_fichier) : {e}")
            return "Erreur : impossible de ranger ce document, réessaie."
        try:
            _propager_fichier_public_range_dossier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR propagation dossier public attaché (gerer_dossier_catalogue_public, fichier {fichier_id}, dossier {dossier_id}) : {e}")
        return "Document rangé dans ce dossier public."

    if action == "retirer_fichier":
        if not (fichier_id or "").strip() or not (dossier_id or "").strip():
            return "Erreur : fichier_id et dossier_id sont obligatoires."
        if _obtenir_dossier(dossier_id) is None:
            return "Ce dossier public est introuvable."
        if not _peut_retirer_contenu(dossier_id, user_id):
            return "Erreur : seul le créateur de ce dossier peut en retirer un document."
        try:
            _retirer_fichier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_catalogue_public (retirer_fichier) : {e}")
            return "Erreur : impossible de retirer ce document, réessaie."
        return "Document retiré de ce dossier public (il reste dans le catalogue public)."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister_valeurs_filtres, lister, "
        "consulter, creer, renommer, supprimer, ranger_fichier, retirer_fichier."
    )
