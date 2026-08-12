"""
Ré-embedding en masse de documents / prompts_chunks vers Gemini
(gemini-embedding-001, 768-dim), suite à la migration depuis
text-embedding-ada-002 (OpenRouter).

Ne re-télécharge ni ne re-parse rien : le texte déjà extrait (colonne
`contenu`) est réutilisé tel quel, seul le vecteur `embedding` est
recalculé et réécrit en place. Fonctionne pour tous les agents en une
seule passe (pas de filtre agent_id : on ré-embed tout le monde).

IMPORTANT : à lancer UNIQUEMENT après le changement de schéma
(ALTER TABLE ... vector(768)) sur documents et prompts_chunks, sinon
les UPDATE échoueront (dimension incompatible avec l'ancienne colonne
vector(1536)).

Usage :
    cd indexers
    python reembed_gemini.py            # dry-run : compte les lignes, teste 1 embedding, n'écrit rien
    python reembed_gemini.py --apply     # ré-embed et écrit réellement en base

Nécessite dans l'environnement (mêmes secrets que l'app / diagnostic.py) :
    SUPABASE_URL, SUPABASE_SECRET, GOOGLE_API_KEY
"""

import os
import sys
import time
import argparse

from supabase import create_client

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
from embeddings import vectoriser, DIMENSION_EMBEDDING  # noqa: E402

TABLES = ["documents", "prompts_chunks"]
TAILLE_PAGE = 200          # lignes lues par page (pagination Supabase)
DELAI_ENTRE_APPELS = 0.15  # secondes, pour rester tranquille sur le quota Gemini
MAX_TENTATIVES = 3


def get_secret(key):
    return os.environ.get(key)


def get_client():
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_SECRET")
    if not url or not key:
        print("❌ SUPABASE_URL ou SUPABASE_SECRET absent de l'environnement.")
        sys.exit(1)
    return create_client(url, key)


def vectoriser_avec_retry(texte):
    derniere_erreur = None
    for tentative in range(1, MAX_TENTATIVES + 1):
        try:
            return vectoriser(texte, task_type="RETRIEVAL_DOCUMENT")
        except Exception as e:
            derniere_erreur = e
            attente = 2 ** tentative  # backoff : 2s, 4s, 8s
            print(f"    ⚠️  Tentative {tentative}/{MAX_TENTATIVES} échouée ({e}), retry dans {attente}s...")
            time.sleep(attente)
    raise derniere_erreur


def compter_lignes(client, table):
    res = client.table(table).select("id", count="exact").limit(1).execute()
    return res.count or 0


def iterer_lignes(client, table):
    """Génère (id, contenu) page par page, sans tout charger en mémoire d'un coup."""
    debut = 0
    while True:
        fin = debut + TAILLE_PAGE - 1
        res = client.table(table).select("id, contenu").range(debut, fin).execute()
        lignes = res.data or []
        if not lignes:
            break
        for ligne in lignes:
            yield ligne
        debut += TAILLE_PAGE


def reembed_table(client, table, appliquer):
    total = compter_lignes(client, table)
    print(f"\n=== {table} : {total} ligne(s) au total ===")

    if total == 0:
        print("    Rien à faire.")
        return

    if not appliquer:
        # Dry-run : juste vérifier que ça fonctionne sur 1 ligne, sans écrire.
        premiere = next(iter(iterer_lignes(client, table)), None)
        if premiere is None:
            return
        try:
            vecteur = vectoriser_avec_retry(premiere["contenu"])
            print(f"    ✅ Test embedding OK sur la ligne id={premiere['id']} "
                  f"(dimension={len(vecteur)}, attendu={DIMENSION_EMBEDDING}).")
            print(f"    {total} ligne(s) seraient ré-embeddées avec --apply.")
        except Exception as e:
            print(f"    ❌ Échec du test d'embedding : {e}")
        return

    ok, erreurs = 0, 0
    for i, ligne in enumerate(iterer_lignes(client, table), start=1):
        contenu = ligne.get("contenu") or ""
        if not contenu.strip():
            print(f"    [{i}/{total}] id={ligne['id']} : contenu vide, ignoré.")
            continue
        try:
            vecteur = vectoriser_avec_retry(contenu)
            client.table(table).update({"embedding": vecteur}).eq("id", ligne["id"]).execute()
            ok += 1
            if i % 25 == 0 or i == total:
                print(f"    [{i}/{total}] {ok} ré-embeddées, {erreurs} erreur(s)...")
        except Exception as e:
            erreurs += 1
            print(f"    ❌ [{i}/{total}] id={ligne['id']} : {e}")
        time.sleep(DELAI_ENTRE_APPELS)

    print(f"    Terminé pour {table} : {ok} ré-embeddées, {erreurs} erreur(s) sur {total}.")


def main():
    parser = argparse.ArgumentParser(description="Ré-embedding vers gemini-embedding-001 (768-dim).")
    parser.add_argument("--apply", action="store_true",
                         help="Écrit réellement en base. Sans ce flag : dry-run (compte + teste, n'écrit rien).")
    args = parser.parse_args()

    print(f"Dimension cible : {DIMENSION_EMBEDDING} (depuis core/embeddings.py)")
    print("Mode : " + ("APPLICATION RÉELLE" if args.apply else "DRY-RUN (rien n'est écrit)"))

    client = get_client()
    for table in TABLES:
        reembed_table(client, table, appliquer=args.apply)

    if not args.apply:
        print("\nDry-run terminé. Relance avec --apply pour écrire réellement en base "
              "(après avoir migré le schéma vector(768)).")


if __name__ == "__main__":
    main()
