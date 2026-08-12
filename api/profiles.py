"""
Portfolio public d'un créateur
et édition de son propre profil (table `profiles`).

Décision prise faute de réponse tranchée sur la génération de
`profiles.slug` (pas décidé encore) :
ces routes utilisent `user_id` directement comme clé d'URL, PAS un
slug. Même repli que celui déjà fait pour `GET /api/agents/{agent_id}`
(qui utilise `agents.id`, pas une colonne slug dédiée non plus). À
remplacer par `profiles.slug` quand sa génération sera décidée et
implémentée — traiter ça comme un changement d'API (l'URL change de
forme), pas une simple substitution de colonne dans la requête.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import utilisateur_courant, utilisateur_optionnel, supabase
from api.agents import supprimer_agent_completement
from api.journal import journaliser
from creation_agent import generer_id_depuis_nom
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfilPublic(BaseModel):
    user_id: str
    nom_affiche: str = ""
    bio: str = ""
    avatar_url: Optional[str] = None
    # Proactivité (25/07) : préférence PRIVÉE -- ne vaut la vraie valeur
    # que pour le propriétaire du profil (voir est_le_proprietaire dans
    # obtenir_profil_public), reste à False par défaut pour un visiteur
    # qui regarde le profil de quelqu'un d'autre (rien de personnel à
    # exposer publiquement ici).
    notifications_proactives_actives: bool = False
    # Choix d'accueil (31/07) : premier agent choisi par l'utilisateur
    # depuis les 5 boutons de la page d'accueil du frontend (voir
    # SectionsProduit-like côté frontend). Une fois choisi, `/` redirige
    # directement vers son chat au lieu de réafficher les 5 boutons.
    # Même convention "privée" que notifications_proactives_actives
    # ci-dessus -- ne vaut la vraie valeur que pour le propriétaire.
    premier_agent_id: Optional[str] = None
    # Ajouté le 2026-08-05 (Bourama : "n'importe qui peut plus être
    # créateur") : gate réelle côté backend (voir creer_agent), pas
    # juste un affichage -- exposé ici pour que le frontend sache s'il
    # doit montrer l'onglet "Mes IA" / le bouton "Créer une IA". Même
    # convention "privée" que les deux champs au-dessus : False par
    # défaut pour un visiteur qui regarde le profil de quelqu'un d'autre.
    est_createur: bool = False


class AgentDuCreateur(BaseModel):
    id: str
    nom: str
    icone_page: str = "🤖"
    image_vitrine_url: Optional[str] = None
    icone_url: Optional[str] = None
    description: str = ""
    # Ajouté le 2026-07-13 (Bourama : bouton on/off pour (dés)activer un
    # agent publiquement, directement à côté de sa carte dans "Mes
    # agents") : sans ce champ, le frontend ne pouvait pas savoir quel
    # état afficher pour le bouton. True par défaut, même convention que
    # partout ailleurs (`actif` absent/NULL = actif).
    actif: bool = True


class ProfilDetailPublic(ProfilPublic):
    agents: List[AgentDuCreateur] = Field(default_factory=list)
    # Ajouté le 2026-08-05 (BUG remonté par Bourama : l'onglet "Administrer"
    # de "Mon espace" n'apparaissait jamais, même pour un vrai administrateur).
    # Ce champ manquait ici alors que le frontend le lit sur CET endpoint
    # (`GET /api/profiles/{user_id}`, un seul appel pour tout "Mon espace") --
    # il n'existait que sur `MonStatutReponse` (`GET /api/profiles/moi/statut`),
    # jamais appelé par le frontend. Toujours vide pour un visiteur externe
    # (rempli uniquement pour le propriétaire, voir obtenir_profil_public),
    # même convention "privée" que notifications_proactives_actives/est_createur
    # ci-dessus.
    agents_administres: List[AgentDuCreateur] = Field(default_factory=list)


def _agents_administres_de(user_id: str) -> List[AgentDuCreateur]:
    """
    Lit `agents_administrateurs` puis les agents correspondants, pour
    déterminer les IA administrées par `user_id`. Factorisé ici (2026-08-05)
    car utilisé à la fois par `mon_statut` et `obtenir_profil_public` --
    logique identique, deux points d'entrée.
    """
    try:
        liens = (
            supabase.table("agents_administrateurs").select("agent_id").eq("user_id", user_id).execute()
        )
        ids_agents = [l["agent_id"] for l in (liens.data or [])]
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents_administrateurs {user_id}) : {e}")
        ids_agents = []

    if not ids_agents:
        return []

    try:
        agents_res = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, icone_url, description, actif")
            .in_("id", ids_agents)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents administrés {user_id}) : {e}")
        return []

    return [
        AgentDuCreateur(
            id=l["id"],
            nom=l["nom"],
            icone_page=(l.get("ui_config") or {}).get("icone_page", "🤖"),
            image_vitrine_url=l.get("image_vitrine_url"),
            icone_url=l.get("icone_url"),
            description=l.get("description") or "",
            actif=l.get("actif") if l.get("actif") is not None else True,
        )
        for l in (agents_res.data or [])
    ]


class MonStatutReponse(BaseModel):
    """
    Détermine ce que "Mon espace" doit afficher (2026-08-05, demande
    Bourama) : soit "Administrer" (si administrateur d'au moins une IA),
    soit "Mes IA" (si créateur), soit les deux si les deux statuts sont
    vrais en même temps (2026-08-05, précision de Bourama : pas de priorité
    exclusive entre les deux, les deux onglets doivent apparaître).

    Les deux statuts sont pour l'instant activés manuellement par
    Bourama en base (`profiles.est_createur`, table
    `agents_administrateurs`) : rien dans la plateforme ne permet
    encore à un utilisateur de devenir créateur ou de nommer un
    administrateur lui-même (voir migrations/2026_08_05_agents_administrateurs.sql).
    """

    est_createur: bool = False
    agents_administres: List[AgentDuCreateur] = Field(default_factory=list)


@router.get("/moi/statut", response_model=MonStatutReponse)
def mon_statut(utilisateur=Depends(utilisateur_courant)):
    try:
        profil = (
            supabase.table("profiles").select("est_createur").eq("user_id", utilisateur.id).maybe_single().execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture est_createur {utilisateur.id}) : {e}")
        profil = None
    est_createur = bool((profil.data or {}).get("est_createur")) if profil and profil.data else False

    agents_administres = _agents_administres_de(utilisateur.id)

    return MonStatutReponse(est_createur=est_createur, agents_administres=agents_administres)


@router.get("/{user_id}", response_model=ProfilDetailPublic)
def obtenir_profil_public(user_id: str, utilisateur=Depends(utilisateur_optionnel)):
    """
    Portfolio public d'un créateur, pour `/u/[slug]` (en pratique
    `/u/{user_id}` pour l'instant, voir docstring du module). Public,
    aucune auth requise. Inclut la liste de ses agents actifs (même
    convention "True par défaut" que `/api/feed`), pour le tableau des
    pages du frontend ("Liste des agents du créateur, bouton Follow").

    Bug corrigé le 2026-07-13 (Bourama : un agent désactivé disparaissait
    complètement, même pour son propriétaire) : ce même endpoint est
    réutilisé par le dashboard pour "Mes agents" (voir dashboard/page.tsx)
    — masquer les agents inactifs y était correct côté visiteur public,
    mais empêchait le créateur de retrouver son propre agent pour le
    réactiver. `utilisateur_optionnel` (jamais de 401 ici, contrairement
    à `utilisateur_courant`) permet de savoir si la personne qui regarde
    EST le propriétaire de ce profil ; si oui, elle voit tous ses agents,
    actifs ou non — sinon, comportement inchangé.

    404 si aucun profil n'existe pour ce `user_id` -- SAUF pour le
    propriétaire lui-même (voir BUG 2026-08-05 ci-dessous), qui reçoit un
    profil vide plutôt qu'une erreur. Pour un visiteur externe : un
    compte tout juste inscrit sans `PATCH /me` préalable n'a pas encore
    de portfolio public (voir docstring de `mettre_a_jour_mon_profil`,
    pas de trigger de création automatique décidé).

    L'échec de la lecture des agents n'annule pas la réponse (best-effort,
    même logique que l'indexation de texte libre à la création d'agent) :
    mieux vaut un profil sans liste d'agents qu'une 500 sur tout.

    BUG corrigé le 2026-08-05 (remonté par Bourama : la section "Mes IA"/
    "Administrer" de "Mon espace" plantait silencieusement). Cet endpoint
    est réutilisé par le dashboard pour lister ses propres agents (voir
    ci-dessus), mais créer un agent (`POST /api/agents`) n'a jamais
    exigé d'avoir un profil (`PATCH /api/profiles/me`) au préalable --
    un utilisateur (ou un compte tout juste rendu créateur/administrateur
    par Bourama via `est_createur`/`agents_administrateurs`, voir
    `GET /api/profiles/moi/statut`) peut donc très bien avoir des agents
    sans jamais avoir de ligne `profiles`. Le 404 ci-dessous, correct
    pour un VRAI visiteur externe (portfolio public inexistant), cassait
    silencieusement ce cas pour le propriétaire (le frontend avale
    l'erreur et affiche "Aucune IA", alors que les agents existent bel
    et bien). Convention reprise d'ailleurs dans le code (ex:
    `api/agents.py:creer_commentaire`, `nom_affiche=None` si profil
    absent) : absence de profil = valeurs vides, pas une erreur --
    appliquée ici uniquement pour le propriétaire (un visiteur externe
    voit toujours 404, comportement public inchangé).
    """
    try:
        profil = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, bio, avatar_url, notifications_proactives_actives, premier_agent_id, est_createur")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture profil {user_id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_CHARGER_CE_PROFIL_POUR")

    est_le_proprietaire = bool(utilisateur and utilisateur.id == user_id)

    if not profil or not profil.data:
        if not est_le_proprietaire:
            raise erreur_api(404, "PROFIL_INTROUVABLE")
        # Propriétaire sans profil : valeurs vides plutôt qu'une 404
        # (voir BUG 2026-08-05 dans la docstring ci-dessus).
        ligne_profil: dict = {"user_id": user_id}
    else:
        ligne_profil = profil.data

    try:
        requete_agents = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, icone_url, description, actif")
            .eq("owner_id", user_id)
        )
        if not est_le_proprietaire:
            requete_agents = requete_agents.or_("actif.is.null,actif.eq.true")
            requete_agents = requete_agents.or_("publiable.is.null,publiable.eq.true")
        agents_res = requete_agents.execute()
        lignes_agents = agents_res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents du créateur {user_id}) : {e}")
        lignes_agents = []

    agents = [
        AgentDuCreateur(
            id=ligne["id"],
            nom=ligne["nom"],
            icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
            image_vitrine_url=ligne.get("image_vitrine_url"),
            icone_url=ligne.get("icone_url"),
            description=ligne.get("description") or "",
            actif=ligne.get("actif") if ligne.get("actif") is not None else True,
        )
        for ligne in lignes_agents
    ]

    ligne = ligne_profil
    return ProfilDetailPublic(
        user_id=ligne["user_id"],
        nom_affiche=ligne.get("nom_affiche") or "",
        bio=ligne.get("bio") or "",
        avatar_url=ligne.get("avatar_url"),
        agents=agents,
        notifications_proactives_actives=(
            bool(ligne.get("notifications_proactives_actives")) if est_le_proprietaire else False
        ),
        premier_agent_id=(ligne.get("premier_agent_id") if est_le_proprietaire else None),
        est_createur=(bool(ligne.get("est_createur")) if est_le_proprietaire else False),
        agents_administres=(_agents_administres_de(user_id) if est_le_proprietaire else []),
    )


class MettreAJourProfilPayload(BaseModel):
    nom_affiche: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    # Proactivité (25/07) : l'utilisateur autorise SES agents (voir aussi
    # agents.proactivite_active, côté créateur) à le relancer en cas
    # d'inactivité. Double opt-in, voir core/proactivite.py.
    notifications_proactives_actives: Optional[bool] = None
    # Choix d'accueil (31/07) : voir docstring de ProfilPublic.
    # Transmettre explicitement "" (chaîne vide) permet de RÉINITIALISER
    # le choix (redevenir None) -- None seul veut dire "champ omis, ne
    # rien changer", donc il fallait un moyen distinct de forcer le vide.
    premier_agent_id: Optional[str] = None


@router.patch("/me", response_model=ProfilPublic)
def mettre_a_jour_mon_profil(
    payload: MettreAJourProfilPayload, request: Request, utilisateur=Depends(utilisateur_courant)
):
    """
    Crée ou met à jour le profil de l'utilisateur courant. Upsert, pas un
    simple update : rien ne garantit qu'une ligne `profiles` existe déjà
    pour ce `user_id` (pas de trigger de création automatique à
    l'inscription, et la génération de `profiles.slug` n'est toujours
    pas décidée). Le premier appel de ce endpoint sert
    donc aussi de création de profil.

    PATCH partiel : un champ omis (None) n'est pas modifié.

    Bug corrigé le 2026-07-12 (500 remonté par Bourama, capture d'écran
    /dashboard) : `profiles.slug` est NOT NULL + UNIQUE en base, mais
    rien ne le remplissait jamais ici — le tout premier upsert pour
    n'importe quel compte échouait systématiquement (violation de
    contrainte NOT NULL, avalée par le except générique et renvoyée comme
    500 opaque). Généré une seule fois, à la création de la ligne
    (jamais régénéré sur les PATCH suivants — une fois que
    `profiles.slug` remplacera `user_id` dans les URLs `/u/...`, un lien
    déjà partagé ne doit pas changer sous les pieds de la personne).

    Bug ter corrigé le 2026-07-12 (3e tentative, 500 toujours présent
    malgré les deux fixes précédents -- vrai message d'erreur Postgres
    obtenu via le DEBUG temporaire, capture d'écran de Bourama) : même
    symptôme que le "bug bis" (violation NOT NULL sur `slug`), donc la
    détection "profil déjà existant" sautait encore la génération du
    slug dans certains cas, malgré le fix précédent. Plutôt que de
    rajouter une 3e couche de rustine sur la même détection indirecte
    (vérité Python d'un objet de réponse Supabase), le test est refait
    entièrement différemment ici : on lit directement la valeur de
    `slug` en base pour ce `user_id`, et on ne saute la génération QUE si
    cette valeur existe et n'est pas vide. Tout le reste (aucune ligne,
    erreur de lecture, ligne existante mais slug vide) déclenche une
    génération -- c'est le comportement sûr par défaut : un slug généré
    à tort dans un cas limite est rattrapable, un NULL qui fait échouer
    l'upsert entier ne l'est pas.
    """
    ligne = {"user_id": utilisateur.id}
    if payload.nom_affiche is not None:
        ligne["nom_affiche"] = payload.nom_affiche.strip()
    if payload.bio is not None:
        ligne["bio"] = payload.bio.strip()
    if payload.avatar_url is not None:
        ligne["avatar_url"] = payload.avatar_url
    if payload.notifications_proactives_actives is not None:
        ligne["notifications_proactives_actives"] = payload.notifications_proactives_actives
    if payload.premier_agent_id is not None:
        ligne["premier_agent_id"] = payload.premier_agent_id.strip() or None

    try:
        deja_existant = (
            supabase.table("profiles")
            .select("slug")
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
        slug_existant = (deja_existant.data or {}).get("slug") if deja_existant else None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification slug existant {utilisateur.id}) : {e}")
        slug_existant = None

    if slug_existant:
        # Bug quater trouvé le 2026-07-12 (le debug v4 l'a prouvé dans les
        # faits, pas par déduction) : `upsert(ligne, on_conflict="user_id")`
        # tentait quand même une vraie INSERT (pas une UPDATE) même quand
        # `slug_existant` valait bien 'moi' -- donc même quand la ligne
        # existait déjà. Cause exacte non identifiée avec certitude (client
        # Supabase/PostgREST ne respectant pas `on_conflict` comme attendu
        # dans ce contexte), mais le contournement est imparable : on
        # abandonne `upsert()` pour ce cas, remplacé par un `update()`
        # explicite, ciblé par `.eq("user_id", ...)`. Un update() ne peut
        # pas se retrouver à tenter une insertion -- pas d'ambiguïté
        # possible sur le mécanisme.
        try:
            supabase.table("profiles").update(ligne).eq("user_id", utilisateur.id).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (update profil {utilisateur.id}) : {e}")
            raise erreur_api(500, "IMPOSSIBLE_DE_METTRE_A_JOUR_LE")
    else:
        base = generer_id_depuis_nom(payload.nom_affiche or "") or utilisateur.id[:8]
        slug = base
        try:
            collision = (
                supabase.table("profiles").select("user_id").eq("slug", slug).maybe_single().execute()
            )
            if collision and collision.data:
                slug = f"{base}-{utilisateur.id[:6]}"
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification unicité slug={slug}) : {e}")
            slug = f"{base}-{utilisateur.id[:6]}"
        ligne["slug"] = slug

        # Vraie INSERT, pas upsert : si une ligne existe malgré tout pour ce
        # user_id (cas limite : `slug_existant` faux-négatif), l'insert
        # échoue sur la contrainte PK -> on retente alors un update() avec
        # le slug fraîchement généré, plutôt que de laisser planter.
        try:
            supabase.table("profiles").insert(ligne).execute()
        except Exception as e_insert:
            logging.error(
                f"ERREUR SUPABASE (insert profil {utilisateur.id}), tentative update : {e_insert}"
            )
            try:
                supabase.table("profiles").update(ligne).eq("user_id", utilisateur.id).execute()
            except Exception as e_update:
                logging.error(f"ERREUR SUPABASE (update de repli profil {utilisateur.id}) : {e_update}")
                raise erreur_api(500, "IMPOSSIBLE_DE_METTRE_A_JOUR_LE")

    try:
        res = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, bio, avatar_url, notifications_proactives_actives, premier_agent_id, est_createur")
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (relecture profil {utilisateur.id}) : {e}")
        raise erreur_api(500, "PROFIL_RELECTURE_ECHEC")

    journaliser(
        action="profil.modifie",
        user_id=utilisateur.id,
        cible_type="profile",
        cible_id=utilisateur.id,
        details={
            "champs_modifies": [
                c for c in ("nom_affiche", "bio", "avatar_url", "premier_agent_id") if c in ligne
            ]
        },
        request=request,
    )

    resultat = res.data or ligne
    return ProfilPublic(
        user_id=resultat["user_id"],
        nom_affiche=resultat.get("nom_affiche") or "",
        bio=resultat.get("bio") or "",
        avatar_url=resultat.get("avatar_url"),
        notifications_proactives_actives=bool(resultat.get("notifications_proactives_actives")),
        premier_agent_id=resultat.get("premier_agent_id"),
        est_createur=bool(resultat.get("est_createur")),
    )


@router.delete("/me", status_code=204)
def supprimer_mon_compte(request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    "Supprimer mon compte" dans la zone de danger de Mon espace (demande
    Bourama, 2026-07-15). Purge dans l'ordre : chaque agent possédé (via
    api.agents.supprimer_agent_completement, même fonction que "Supprimer
    un agent" pour ne pas dupliquer cette logique), les publications
    (posts), les traces laissées sur le contenu des AUTRES (commentaires,
    notes, likes/commentaires de mises à jour, follows dans les deux
    sens), le profil, puis enfin le compte Supabase Auth lui-même via
    l'API admin (le client `supabase` de api.auth utilise déjà la service
    role key, seule capable d'appeler `auth.admin`).

    Ne touche PAS à `historique_conversations` (même choix que
    supprimer_agent_completement, journal permanent jamais purgé ailleurs
    dans le projet) : les échanges passés restent en base, seulement
    détachés de tout profil/agent visible.

    Chaque étape est best-effort (log et continue) sauf la suppression
    finale du compte Auth, seule à faire échouer la requête si elle
    plante -- mieux vaut un compte orphelin nettoyé à 95% qu'un compte qui
    ne se supprime jamais parce qu'une seule ligne annexe a fait échouer
    tout le reste.
    """
    user_id = utilisateur.id

    try:
        mes_agents = supabase.table("agents").select("id").eq("owner_id", user_id).execute()
        for ligne in mes_agents.data or []:
            try:
                supprimer_agent_completement(ligne["id"])
            except Exception as e:
                logging.error(f"ERREUR suppression agent {ligne['id']} (compte {user_id}) : {e}")
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste agents à purger, compte {user_id}) : {e}")

    for table, colonne in (
        ("posts", "user_id"),
        ("agent_comments", "user_id"),
        ("agent_ratings", "user_id"),
        ("agent_updates", "user_id"),
        ("agent_update_likes", "user_id"),
        ("agent_update_comments", "user_id"),
        ("notifications", "user_id"),
    ):
        try:
            supabase.table(table).delete().eq(colonne, user_id).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (purge table {table} pour compte {user_id}) : {e}")

    for table, colonne in (("follows", "follower_id"), ("follows", "creator_id")):
        try:
            supabase.table(table).delete().eq(colonne, user_id).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (purge follows.{colonne} pour compte {user_id}) : {e}")

    try:
        supabase.table("profiles").delete().eq("user_id", user_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression profil, compte {user_id}) : {e}")

    # Journalisé avant la suppression Auth elle-même (pas après) : une fois
    # le compte supprimé, journal_actions.user_id passe à NULL (ON DELETE
    # SET NULL) -- cible_id (non lié par contrainte) garde la trace de qui
    # a été supprimé, mais autant capturer l'e-mail pendant qu'il est
    # encore disponible sur `utilisateur`.
    journaliser(
        action="compte.supprime",
        user_id=user_id,
        cible_type="profile",
        cible_id=user_id,
        details={"email": getattr(utilisateur, "email", None)},
        request=request,
    )

    try:
        supabase.auth.admin.delete_user(user_id)
    except Exception as e:
        logging.error(f"ERREUR SUPABASE AUTH (suppression compte {user_id}) : {e}")
        raise erreur_api(500, "PROFIL_FERMETURE_PARTIELLE")
