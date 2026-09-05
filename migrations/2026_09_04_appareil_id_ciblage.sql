-- 04/09/2026, Bourama : correction de deux bugs remontes sur les
-- dossiers designes de l'app mobile.
--
-- Bug 1 ("android" colle au nom du dossier) : traite cote code
-- uniquement (formatage de gerer_dossier_telephone dans
-- core/serveur_mcp_generation.py), rien a changer ici.
--
-- Bug 2 (deux telephones Android du meme compte se melangent) :
-- jusqu'ici, dossiers_designes_mobile et
-- appareils_mobiles_push_tokens ne distinguaient que la PLATEFORME
-- (android/ios), jamais l'appareil precis -- deux telephones Android
-- du meme etudiant partageaient donc le meme "seau", avec ecrasement
-- mutuel a chaque synchronisation. Introduit ici un identifiant
-- d'appareil (`appareil_id`, UUID genere une fois par l'app et
-- persiste localement, voir IdentifiantAppareil.kt/.swift cote
-- clovis-frontend) et un libelle optionnel (`appareil_nom`, choisi
-- par l'etudiant ou genere par defaut a partir du modele du
-- telephone) pour que l'agent puisse cibler un appareil precis quand
-- une collision de noms existe entre deux appareils.

-- dossiers_designes_mobile : table purement miroir/cache (aucune
-- donnee qui n'existe pas deja, en mieux, sur le telephone lui-meme),
-- videe ici plutot que migree -- chaque appareil la reconstruit
-- integralement a sa prochaine synchronisation (ouverture de l'app ou
-- prochain ajout/retrait, voir DossiersPlugin.kt::synchroniserAvecBackend).
truncate table dossiers_designes_mobile;

alter table dossiers_designes_mobile
    drop constraint if exists dossiers_designes_mobile_user_id_plateforme_nom_key;

alter table dossiers_designes_mobile
    add column if not exists appareil_id text,
    add column if not exists appareil_nom text;

update dossiers_designes_mobile set appareil_id = '' where appareil_id is null;

alter table dossiers_designes_mobile
    alter column appareil_id set not null,
    alter column appareil_id set default '';

alter table dossiers_designes_mobile
    add constraint dossiers_designes_mobile_user_id_appareil_id_nom_key
    unique (user_id, appareil_id, nom);

-- appareils_mobiles_push_tokens : meme identifiant, pour pouvoir
-- livrer une action a UN SEUL appareil precis plutot qu'a tous les
-- tokens de l'utilisateur (voir core/notifications_push.py,
-- envoyer_action_appareil). Nullable : un token existant avant cette
-- migration reste utilisable (diffusion large, comportement actuel),
-- seuls les nouveaux enregistrements porteront un appareil_id.
alter table appareils_mobiles_push_tokens
    add column if not exists appareil_id text;

create index if not exists idx_appareils_mobiles_push_tokens_appareil
    on appareils_mobiles_push_tokens (user_id, appareil_id);

-- actions_appareil_mobile : cible optionnelle d'une action
-- fire-and-forget. NULL = comportement actuel (n'importe quel
-- appareil du compte peut l'executer, ex: types d'action futurs sans
-- notion de dossier). Rempli des que gerer_dossier_telephone a pu
-- resoudre sans ambiguite l'appareil proprietaire du dossier vise.
alter table actions_appareil_mobile
    add column if not exists appareil_id_cible text;
