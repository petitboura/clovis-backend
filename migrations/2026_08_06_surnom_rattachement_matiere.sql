-- Surnom optionnel sur un rattachement (06/08/2026, demande Bourama) :
-- la liste "mes matières débloquées" affichée côté étudiant montre déjà
-- automatiquement le nom de l'enseignant (via contenus_par_matiere ->
-- enseignant_id -> profiles.nom_affiche, voir _nom_enseignant côté
-- api/contenu_dynamique_matiere.py) -- ce champ ajoute EN PLUS un label
-- perso optionnel que l'étudiant peut taper lui-même pour se repérer
-- (ex: "Maths - avec M. Ali"), utile surtout si plusieurs rattachements
-- couvrent la même matière.
alter table rattachements_par_matiere add column if not exists surnom text;
