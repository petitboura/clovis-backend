-- Connexion IA <-> structure programme (13/08/2026, demande Bourama) :
-- déclare le nouvel outil consulter_programme comme disponible côté
-- plateforme, même mécanisme que consulter_comportement -- sans cette
-- ligne, l'outil existe dans le code (core/serveur_mcp_generation.py)
-- mais n'est jamais proposé au modèle (voir
-- core/mcp_tools.py::_tous_les_outils_generation_disponibles).
-- Appliquée directement en base le 13/08 (voir historique migrations),
-- ce fichier sert de trace/rejouabilité.

insert into registre_outils_plateforme (nom_outil, categorie, nom_serveur, disponible, updated_at)
values ('consulter_programme', 1, 'generation', true, now())
on conflict (nom_outil) do update set disponible = true, updated_at = now();
