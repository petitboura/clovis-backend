-- Migration : outils retenus par le grand modele d'un tour a l'autre
-- (demande Bourama, 04/09). Voir core/main.py (_lire_outils_retenus /
-- _ecrire_outils_retenus) et le nouvel outil interne garder_outils
-- (_outil_garder_outils). Objectif : le grand modele peut decider de
-- garder un outil disponible pour le tour SUIVANT sans repasser par le
-- petit routeur automatique (_router_outils) ni par un clic de
-- confirmation cote frontend (evenement "outils_suggeres").
--
-- Une ligne par conversation, ECRASEE (jamais cumulee) a chaque tour :
-- si le modele ne rappelle pas garder_outils pendant un tour, la ligne
-- est remise a liste vide a la fin de ce tour -- comportement voulu
-- "reste garde tant qu'il le redemande, disparait sinon".

create table if not exists outils_retenus_conversation (
    conversation_id text primary key,
    outils jsonb not null default '[]'::jsonb,
    mis_a_jour_a timestamptz not null default now()
);

comment on table outils_retenus_conversation is
    'Outils que le grand modele a explicitement decide de garder disponibles pour le tour suivant (appel interne garder_outils), independamment du routeur automatique. Une ligne par conversation_id, ecrasee (jamais cumulee) a chaque tour.';
