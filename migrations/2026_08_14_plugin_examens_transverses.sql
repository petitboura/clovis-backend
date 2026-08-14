-- Clonage complet des plugins (14/08/2026, demande Bourama) : le
-- clonage d'un programme (voir api/plugins_programme.py::_cloner_programme)
-- ne copiait jusqu'ici que matières/chapitres/documents/exercices, jamais
-- les examens ni les classements transversaux. Cette migration ajoute le
-- support nécessaire pour les examens.
--
-- Un examen peut couvrir des chapitres de PLUSIEURS programmes différents
-- (pas de contrainte "même programme" -- voir
-- api/contenu_programme.py::_verifier_chapitres_pour_examen). Quand un
-- seul programme est publié comme plugin, l'auteur choisit explicitement,
-- pour chaque examen qui touche AUSSI un autre programme, s'il doit être
-- inclus dans la copie -- dans ce cas, seuls les chapitres appartenant à
-- CE programme sont conservés côté copie (décision Bourama 14/08).
alter table plugins_programme
  add column if not exists examens_transverses_inclus uuid[] not null default '{}';
