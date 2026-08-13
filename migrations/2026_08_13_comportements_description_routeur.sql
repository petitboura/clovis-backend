-- Nouveau mécanisme de comportements (13/08/2026, demande Bourama) : chaque
-- comportement a désormais une description courte, générée automatiquement,
-- utilisée par un petit routeur pour décider quels candidats présenter au
-- grand modèle -- qui décide lui-même s'il veut lire le texte complet (voir
-- core/comportements_etudiants.py et l'outil consulter_comportement dans
-- core/serveur_mcp_generation.py). Les comportements existants n'ont pas de
-- description : on repart de zéro plutôt que d'en générer après coup.
delete from comportements_etudiants;
alter table comportements_etudiants add column if not exists description text;
