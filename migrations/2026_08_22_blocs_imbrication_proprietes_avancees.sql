-- Refonte "vraiment comme Notion", Partie 2/2, 2026-08-22, demande
-- explicite de Bourama ("tout"). Contrairement à la note du 20/08 dans
-- 2026_08_20_bases_donnees_revision_taches.sql ("pas de relations entre
-- bases, pas de formules calculées, pas de filtres/tris avancés") --
-- décision explicitement revenue dessus par lui-même aujourd'hui, pas
-- une supposition de ma part.

-- Imbrication bloc-dans-bloc (ex : un bloc "bascule"/toggle qui contient
-- d'autres blocs). Auto-référence nullable -- un bloc sans parent_bloc_id
-- reste un bloc de premier niveau, comme avant cette migration.
alter table blocs add column if not exists parent_bloc_id uuid references blocs(id) on delete cascade;
create index if not exists idx_blocs_parent on blocs(parent_bloc_id);

-- Configuration des propriétés avancées de base de données :
--   - relation : {"base_cible_id": "..."}
--   - rollup   : {"relation_propriete_id": "...", "propriete_cible_id": "...", "fonction": "nombre"|"somme"|"texte"}
--   - formule  : {"propriete_a_id": "...", "operation": "concatener"|"addition"|"soustraction"|"multiplication", "propriete_b_id": "..."}
-- Vide ({}) pour les types de propriété existants (texte/nombre/date/
-- statut/case_a_cocher), qui n'en ont pas besoin.
alter table bases_donnees_proprietes add column if not exists config jsonb not null default '{}'::jsonb;
