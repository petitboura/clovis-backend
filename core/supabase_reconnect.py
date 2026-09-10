"""
Corrige un bug de fond touchant les ~30 endroits du backend qui créent
chacun leur propre connexion vers Supabase (`create_client(...)`).

Chaque connexion est ouverte une seule fois au démarrage du process puis
réutilisée pour toutes les requêtes suivantes (comportement normal de la
librairie `supabase`/`postgrest`, HTTP/2). Le problème : quand Supabase
ferme cette connexion de son côté (ce qui est normal et arrive
régulièrement, sans rapport avec une panne chez eux), rien ne remplaçait
automatiquement la connexion morte -- la requête suivante plantait avec
`ConnectionTerminated` / `RemoteProtocolError`, ce qui pouvait ensuite
être avalé silencieusement plus haut dans la pile (ex: routage_outils.py
traitant l'échec comme "aucun outil").

Correctif choisi (option A, validé par Bourama le 10/09) : une réparation
ciblée au niveau du transport HTTP partagé par toutes ces connexions,
sans réorganiser les ~30 fichiers existants. On intercepte l'envoi de
requête HTTP (`httpx.Client.send`, utilisé par la librairie Supabase) :
si la connexion est cassée, on la referme et on en ouvre une nouvelle
automatiquement, puis on retente UNE fois avant de laisser tomber.

Ce module doit être importé une seule fois, le plus tôt possible au
démarrage du process (avant toute création de connexion Supabase), pour
que le monkeypatch soit en place avant le premier `create_client(...)`.
Il n'a besoin d'être importé qu'une fois -- les imports suivants ne
patchent rien de plus (protection `_DEJA_PATCHE`).
"""

import logging
import httpx

_DEJA_PATCHE = False

# Erreurs de transport qui signalent une connexion morte/coupée côté
# serveur (dont ConnectionTerminated en HTTP/2) -- jamais une erreur de
# logique métier ou de données, uniquement des soucis de bas niveau réseau.
_ERREURS_CONNEXION_MORTE = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
)


def activer_reconnexion_automatique():
    global _DEJA_PATCHE
    if _DEJA_PATCHE:
        return
    _DEJA_PATCHE = True

    send_original = httpx.Client.send

    def send_avec_reconnexion(self, request, *args, **kwargs):
        try:
            return send_original(self, request, *args, **kwargs)
        except _ERREURS_CONNEXION_MORTE as erreur:
            logging.warning(
                "Connexion Supabase coupée côté serveur (%s) -- fermeture "
                "et réouverture automatique de la connexion, nouvelle "
                "tentative en cours.",
                erreur.__class__.__name__,
            )
            try:
                self._transport.close()
            except Exception:
                pass
            # Une seule nouvelle tentative : si elle échoue aussi, l'erreur
            # remonte normalement (pas de boucle infinie de réessais).
            return send_original(self, request, *args, **kwargs)

    httpx.Client.send = send_avec_reconnexion
    logging.info(
        "Reconnexion automatique Supabase activée (retente une fois sur "
        "connexion coupée, avant de laisser tomber la requête)."
    )
