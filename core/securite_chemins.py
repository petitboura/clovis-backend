"""
Sanitisation des chemins relatifs fournis par le modèle pour les fichiers
générés (code, archives, données) -- voir generation_code.py,
generation_archives.py, generation_donnees.py.

CORRECTIF 2026-07-30 (audit de sécurité) : ces chemins ("chemin",
"chemin_fichier", "nom"...) sont choisis par le modèle -- potentiellement
influencé par un contenu externe qu'il a lu (page web, dépôt GitHub) --
et étaient utilisés tels quels dans archive.writestr(...) et dans les
clés de stockage Supabase, sans aucune vérification. Un chemin du type
"../../../etc/cron.d/x" n'était jamais rejeté : zip-slip classique une
fois l'archive extraite chez quelqu'un, ou pollution de l'arborescence
du bucket Supabase.
"""


def chemin_relatif_sur(chemin, repli: str = "fichier") -> str:
    """
    Normalise un chemin relatif pour qu'il reste TOUJOURS à l'intérieur
    du dossier prévu, sans jamais pouvoir "remonter" en dehors :
      - convertit les séparateurs Windows ("\\") en "/"
      - retire toute racine absolue (un chemin commençant par "/" perd
        ce "/")
      - retire tout segment "." ou ".." (silencieusement -- ce ne sont
        jamais des noms de fichier légitimes dans ce contexte)
      - préserve les sous-dossiers légitimes (ex. "src/index.js" reste
        "src/index.js") : seule la traversée est bloquée, pas la
        structure.
      - si le résultat est vide (chemin uniquement fait de "../"/"."/
        "/", ou absent), retombe sur `repli`.
    """
    if not chemin:
        return repli
    chemin = str(chemin).replace("\\", "/")
    segments = [s for s in chemin.split("/") if s not in ("", ".", "..")]
    if not segments:
        return repli
    return "/".join(segments)
