"""
Script à exécuter UNE SEULE FOIS (en local, ou une fois via Railway
shell) pour générer les clés VAPID nécessaires aux notifications push.
Ce n'est PAS un fichier appelé par l'application elle-même -- juste un
générateur de clés à usage ponctuel.

Usage :
    pip install py-vapid --break-system-packages
    python scripts/generer_cles_vapid.py

Copie ensuite les deux valeurs affichées dans les variables
d'environnement Railway : VAPID_PRIVATE_KEY_PEM_B64 et VAPID_PUBLIC_KEY.
"""

import base64

from py_vapid import Vapid01

v = Vapid01()
v.generate_keys()

pem_prive = v.private_pem()  # bytes, format PEM multi-lignes
pem_b64 = base64.b64encode(pem_prive).decode("ascii")

# Clé publique brute (point non compressé, 65 octets) encodée en
# base64url SANS padding -- c'est le format exact attendu par
# `pushManager.subscribe({applicationServerKey: ...})` côté navigateur.
cle_publique_brute = v.public_key.public_bytes(
    encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
    format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint,
)
cle_publique_b64url = base64.urlsafe_b64encode(cle_publique_brute).decode("ascii").rstrip("=")

print("=" * 70)
print("VAPID_PRIVATE_KEY_PEM_B64=")
print(pem_b64)
print()
print("VAPID_PUBLIC_KEY=")
print(cle_publique_b64url)
print("=" * 70)
print()
print("Copie ces deux valeurs dans les variables d'environnement Railway.")
print("VAPID_PUBLIC_KEY doit aussi être transmise au frontend (utilisée")
print("comme applicationServerKey lors de l'abonnement aux notifications).")
