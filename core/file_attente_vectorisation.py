"""
File d'attente de vectorisation en arrière-plan (29/08/2026, demande
Bourama : l'ajout d'un fichier à la bibliothèque privée ou publique
attendait la vectorisation complète -- extraction + embeddings Gemini
chunk par chunk -- avant de répondre, ce qui bloquait longtemps sur un
gros fichier ou un upload en masse plusieurs fichiers).

Nouveau flux : le fichier est stocké et renvoyé immédiatement, avec
statut_vectorisation="en_attente" s'il doit être vectorisé (voir
necessite_vectorisation_fichier_privee/publique et
necessite_vectorisation_note ci-dessous), "pret" sinon (lien, vidéo,
type non vectorisé -- rien à attendre). Ce module tourne en arrière-plan
(boucle asyncio démarrée dans api/main.py, voir _boucle_vectorisation) et
traite les fichiers "en_attente" un par un, pour chacune des deux
bibliothèques : privée (fichiers_uploades + documents_bibliotheque) et
publique (bibliotheque_publique + documents_catalogue_public).
Volontairement dans le MÊME module malgré la duplication déjà présente
entre bibliotheque_rag.py et catalogue_public_rag.py, pour ne pas
complexifier davantage ces deux fichiers déjà volumineux -- seule la
DISPATCH par type de fichier vit ici, la logique d'extraction/découpage/
embedding elle-même reste dans ces deux modules, inchangée.

Scope délibérément limité à niveau="utilisateur" (bibliothèque perso) et
à bibliotheque_publique (catalogue public) -- PAS la bibliothèque niveau
"agent" (route Diffuser un document, api/roles.py) qui utilise un
troisième système de RAG totalement différent (table `documents`,
indexers/index_documents.py), resté inchangé (confirmé avec Bourama le
29/08 : chantier séparé, pas traité ici).

Robustesse au redémarrage (Railway redéploie à chaque push) :
- remettre_en_attente_bloques(), appelée une fois au démarrage du
  process (voir _lifespan dans api/main.py), repasse tout fichier resté
  "en_cours" (process coupé en plein traitement) à "en_attente" -- repart
  tout seul, jamais besoin d'intervention manuelle.
- avant de (re)vectoriser, les chunks déjà présents pour ce fichier sont
  supprimés d'abord (voir _nettoyer_chunks_existants) -- un traitement
  interrompu à moitié ne laisse jamais de morceaux dupliqués ou
  incomplets qui fausseraient la recherche.
- un compteur tentatives_vectorisation (max MAX_TENTATIVES) évite qu'un
  fichier cassé (PDF corrompu, etc.) ne reparte indéfiniment à chaque
  redémarrage -- passe en statut "echec" au-delà, erreur_vectorisation
  garde le dernier message pour diagnostic.

Réessai après "echec" (03/09/2026, demande Bourama : un fichier en échec
restait affiché avec un point rouge indéfiniment, seule "solution"
proposée à l'utilisateur = supprimer + réajouter) -- deux mécanismes :
- AUTOMATIQUE, à froid : relancer_echecs_a_froid() (appelée en boucle
  espacée depuis api/main.py:_boucle_reessai_echecs) repasse un fichier
  "echec" à "en_attente" après un délai (COOLDOWN_AUTO_REESSAI) écoulé
  depuis sa dernière tentative (derniere_tentative_vectorisation_a) --
  utile pour une panne passagère (API de vectorisation en rate limit,
  coupure réseau ponctuelle...). Plafonné à MAX_TENTATIVES_AUTO tentatives
  au total pour ne jamais retenter indéfiniment un fichier réellement
  cassé.
- MANUEL : reinitialiser_pour_reessai() (appelée par les routes API
  POST .../reessayer-vectorisation) remet tentatives_vectorisation à 0 et
  le fichier à "en_attente" immédiatement, sans attendre le cooldown --
  toujours disponible tant que le fichier est en échec, même après avoir
  épuisé le plafond automatique.

MIS A JOUR le 06/09/2026 (demande Bourama, même chantier que
core/vectorisation_dossiers_designes.py) : la bibliothèque PUBLIQUE
(bibliotheque_publique) uniquement -- la privée (fichiers_uploades) est
explicitement HORS SCOPE ici, inchangée -- perd la vraie vectorisation
automatique à l'ajout pour tout sauf l'image :
- Image : inchangé, vraie vectorisation automatique.
- PDF / Word / Excel / texte : EXTRACTION GRATUITE du texte à l'ajout
  (necessite_extraction_texte_publique, colonne texte_brut, statut
  dédié statut_extraction_texte), cherchable par mot-clé immédiatement
  (recherche_catalogue_public_mots_cles cherche déjà dans texte_brut).
  La vraie vectorisation reste à la demande (vectoriser_maintenant_
  publique), déclenchée depuis core/outils_bibliotheque.py quand
  l'action "trouver_catalogue_public" trouve une vraie correspondance.
- Audio / Vidéo : plus rien d'automatique, tout à la demande (y compris
  la vidéo désormais, réutilise les briques ffmpeg/Whisper/Gemini de
  core/description_multimedia.py::transcrire_et_decrire_video_bibliotheque).
"""

import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from supabase import create_client

sys.path.append(os.path.dirname(__file__))
from bibliotheque_rag import (  # noqa: E402
    indexer_pdf_bibliotheque,
    indexer_texte_bibliotheque,
    indexer_transcription_bibliotheque,
)
from catalogue_public_rag import (  # noqa: E402
    indexer_pdf_catalogue_public,
    indexer_texte_catalogue_public,
    indexer_transcription_catalogue_public,
    extraire_pages_pdf as extraire_pages_pdf_catalogue_public,
)
from description_multimedia import decrire_image_bibliotheque, transcrire_audio_bibliotheque, transcrire_et_decrire_video_bibliotheque  # noqa: E402
from embeddings import activer_pause_quota_gemini, est_en_pause_quota_gemini, est_erreur_quota_gemini  # noqa: E402

BUCKET_BIBLIOTHEQUE = "bibliotheque"
MAX_TENTATIVES = 3
TAILLE_LOT = 5  # fichiers traités par passage, par bibliothèque -- garde-fou pour qu'un passage ne tourne jamais indéfiniment avant de rendre la main à la boucle appelante.

# Réessai automatique à froid après "echec" (voir docstring du module) --
# distinct de MAX_TENTATIVES (retries rapprochés, quasi immédiats, dans le
# même passage de file d'attente) : ici on laisse d'abord passer un délai,
# pour laisser une chance à une panne passagère de se résorber, avant de
# retenter. MAX_TENTATIVES_AUTO est un plafond TOTAL (compte les tentatives
# rapprochées initiales) -- au-delà, plus aucun réessai automatique, seul
# le bouton "Réessayer" manuel (reinitialiser_pour_reessai) reste possible.
COOLDOWN_AUTO_REESSAI = timedelta(minutes=15)
MAX_TENTATIVES_AUTO = MAX_TENTATIVES + 3

TYPES_MIME_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TYPES_MIME_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def necessite_vectorisation_fichier_privee(type_mime: str | None) -> bool:
    """
    Types vectorisés à l'ajout d'un FICHIER dans la bibliothèque PRIVÉE
    (route POST /api/bibliotheque) -- EXACTEMENT comme l'ancien
    _indexer_et_propager : pdf/image/audio uniquement. Un texte/plain
    envoyé comme fichier via cette route n'a jamais été vectorisé ici
    (seule la note tapée directement, route /texte, l'est -- voir
    necessite_vectorisation_note) : comportement inchangé.

    HORS SCOPE du chantier du 06/09 (extraction gratuite + vectorisation
    à la demande) -- Bourama a confirmé que ce chantier ne concerne QUE
    les dossiers désignés (téléphone) et la bibliothèque publique,
    jamais cette bibliothèque privée manuelle. Comportement inchangé.
    """
    if not type_mime:
        return False
    return type_mime == "application/pdf" or type_mime.startswith("image/") or type_mime.startswith("audio/")


def necessite_vectorisation_fichier_publique(type_mime: str | None) -> bool:
    """
    06/09/2026 : seule l'IMAGE déclenche encore une vraie vectorisation
    automatique à l'ajout dans la bibliothèque PUBLIQUE (voir docstring
    du module). pdf/word/excel/texte passent par
    necessite_extraction_texte_publique (gratuit) ; audio/vidéo restent
    entièrement à la demande.
    """
    return bool(type_mime) and type_mime.startswith("image/")


def necessite_extraction_texte_publique(type_mime: str | None) -> bool:
    """
    06/09/2026 : pdf/word/excel/texte reçoivent une extraction de texte
    GRATUITE (aucun appel Gemini/Groq) dès l'ajout dans la bibliothèque
    PUBLIQUE, pour être cherchables par mot-clé immédiatement (voir
    recherche_catalogue_public_mots_cles, colonne texte_brut). La vraie
    vectorisation (sens) de ce texte reste à la demande.
    """
    return type_mime in ("application/pdf", TYPES_MIME_WORD, TYPES_MIME_EXCEL, "text/plain")


def necessite_vectorisation_note() -> bool:
    """Une note de texte tapée directement (routes /texte, privée ET publique) est toujours vectorisée -- comportement inchangé."""
    return True


def _extraire_texte_docx_bytes(contenu: bytes) -> str:
    """Même logique que core/vectorisation_dossiers_designes.py::_extraire_texte_docx_bytes, dupliquée volontairement (pas de dépendance croisée entre circuits)."""
    import io
    import docx

    document = docx.Document(io.BytesIO(contenu))
    morceaux = [p.text for p in document.paragraphs]
    for table in document.tables:
        for ligne in table.rows:
            morceaux.append("\t".join(cellule.text for cellule in ligne.cells))
    return "\n".join(morceaux)


def _extraire_texte_xlsx_bytes(contenu: bytes) -> str:
    """Même logique que core/vectorisation_dossiers_designes.py::_extraire_texte_xlsx_bytes, dupliquée volontairement."""
    import io
    import openpyxl

    classeur = openpyxl.load_workbook(io.BytesIO(contenu), data_only=True)
    morceaux = []
    for feuille in classeur.worksheets:
        morceaux.append(f"--- Feuille : {feuille.title} ---")
        for ligne in feuille.iter_rows(values_only=True):
            morceaux.append("\t".join("" if v is None else str(v) for v in ligne))
    return "\n".join(morceaux)


def _telecharger(chemin_stockage: str) -> bytes:
    return supabase.storage.from_(BUCKET_BIBLIOTHEQUE).download(chemin_stockage)


def _nettoyer_chunks_existants(table_chunks: str, colonne_scope: str | None, valeur_scope, fichier_id: str) -> None:
    """
    Supprime les chunks déjà indexés pour ce fichier avant de le
    (re)traiter -- un traitement interrompu à moitié (redémarrage,
    crash) ne doit jamais laisser de morceaux dupliqués ou incomplets en
    base (voir docstring du module).
    """
    requete = supabase.table(table_chunks).delete().eq("fichier_id", fichier_id)
    if colonne_scope:
        requete = requete.eq(colonne_scope, valeur_scope)
    requete.execute()


def _vectoriser_privee(ligne: dict) -> None:
    fichier_id = ligne["id"]
    user_id = ligne["user_id"]
    type_mime = ligne["type_mime"] or ""
    contenu = _telecharger(ligne["chemin_stockage"])

    _nettoyer_chunks_existants("documents_bibliotheque", "user_id", user_id, fichier_id)

    if type_mime == "application/pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenu)
            chemin_temp = tmp.name
        try:
            indexer_pdf_bibliotheque(chemin_temp, fichier_id=fichier_id, user_id=user_id)
        finally:
            try:
                os.remove(chemin_temp)
            except OSError:
                pass
    elif type_mime.startswith("image/"):
        description_image = decrire_image_bibliotheque(contenu, type_mime)
        if description_image:
            indexer_texte_bibliotheque(description_image, fichier_id=fichier_id, user_id=user_id)
    elif type_mime.startswith("audio/"):
        segments_audio = transcrire_audio_bibliotheque(contenu, ligne["nom_fichier"])
        if segments_audio:
            indexer_transcription_bibliotheque(segments_audio, fichier_id=fichier_id, user_id=user_id)
    elif type_mime == "text/plain":
        # Note tapée directement (route /texte) -- déjà du texte, pas
        # besoin d'extraction.
        indexer_texte_bibliotheque(contenu.decode("utf-8", errors="ignore"), fichier_id=fichier_id, user_id=user_id)


def _vectoriser_publique(ligne: dict) -> None:
    fichier_id = ligne["id"]
    type_mime = ligne["type_mime"] or ""
    contenu = _telecharger(ligne["chemin_stockage"])

    _nettoyer_chunks_existants("documents_catalogue_public", None, None, fichier_id)

    if type_mime == "application/pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenu)
            chemin_temp = tmp.name
        try:
            indexer_pdf_catalogue_public(chemin_temp, fichier_id=fichier_id)
        finally:
            try:
                os.remove(chemin_temp)
            except OSError:
                pass
    elif type_mime.startswith("image/"):
        description_image = decrire_image_bibliotheque(contenu, type_mime)
        if description_image:
            indexer_texte_catalogue_public(description_image, fichier_id=fichier_id)
    elif type_mime.startswith("audio/"):
        segments_audio = transcrire_audio_bibliotheque(contenu, ligne["nom_fichier"])
        if segments_audio:
            indexer_transcription_catalogue_public(segments_audio, fichier_id=fichier_id)
    elif type_mime.startswith("video/"):
        # 06/09/2026 : vidéo A LA DEMANDE uniquement (voir docstring du
        # module) -- réutilise les mêmes briques que la vidéo de chat.
        extension = (ligne.get("nom_fichier") or "").rsplit(".", 1)[-1].lower() or "mp4"
        resultat = transcrire_et_decrire_video_bibliotheque(contenu, ligne["nom_fichier"], extension)
        for segment in (resultat or {}).get("segments_audio") or []:
            texte = (segment.get("text") or "").strip()
            if texte:
                indexer_texte_catalogue_public(texte, fichier_id=fichier_id, timestamp_debut=segment.get("start"), timestamp_fin=segment.get("end"))
        for description in (resultat or {}).get("descriptions_frames") or []:
            if description.strip():
                indexer_texte_catalogue_public(description, fichier_id=fichier_id)
    elif type_mime == TYPES_MIME_WORD:
        texte = _extraire_texte_docx_bytes(contenu)
        if texte.strip():
            indexer_texte_catalogue_public(texte, fichier_id=fichier_id)
    elif type_mime == TYPES_MIME_EXCEL:
        texte = _extraire_texte_xlsx_bytes(contenu)
        if texte.strip():
            indexer_texte_catalogue_public(texte, fichier_id=fichier_id)
    elif type_mime == "text/plain":
        indexer_texte_catalogue_public(contenu.decode("utf-8", errors="ignore"), fichier_id=fichier_id)


def _extraire_texte_pour_extraction_publique(type_mime: str | None, contenu: bytes) -> str:
    """
    Extraction GRATUITE (aucun appel Gemini/Groq) pour pdf/word/excel/
    texte du catalogue public -- voir necessite_extraction_texte_publique.
    Assemble le texte en un seul bloc (pas de découpage par page ici,
    juste pour la recherche par mot-clé).
    """
    if type_mime == "application/pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contenu)
            chemin_temp = tmp.name
        try:
            return "\n\n".join(extraire_pages_pdf_catalogue_public(chemin_temp))
        finally:
            try:
                os.remove(chemin_temp)
            except OSError:
                pass
    if type_mime == TYPES_MIME_WORD:
        return _extraire_texte_docx_bytes(contenu)
    if type_mime == TYPES_MIME_EXCEL:
        return _extraire_texte_xlsx_bytes(contenu)
    return contenu.decode("utf-8", errors="ignore")


def remettre_en_attente_bloques() -> None:
    """Appelée une fois au démarrage du process (voir api/main.py:_lifespan) -- voir docstring du module."""
    for table in ("fichiers_uploades", "bibliotheque_publique"):
        try:
            supabase.table(table).update({"statut_vectorisation": "en_attente"}).eq(
                "statut_vectorisation", "en_cours"
            ).execute()
        except Exception as e:
            logging.error(f"ERREUR remise en attente au démarrage ({table}) : {e}")
    # statut_extraction_texte n'existe que sur bibliotheque_publique
    # (colonne absente de fichiers_uploades, hors scope -- voir docstring
    # du module).
    try:
        supabase.table("bibliotheque_publique").update({"statut_extraction_texte": "en_attente"}).eq(
            "statut_extraction_texte", "en_cours"
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR remise en attente au démarrage (extraction texte, bibliotheque_publique) : {e}")


COLONNES_EXTRACTION_PUBLIQUE = "id, chemin_stockage, nom_fichier, type_mime"


def traiter_extractions_texte_publique_une_fois() -> int:
    """
    06/09/2026 : pendante GRATUITE distincte de traiter_file_attente_
    une_fois -- extrait le texte brut de pdf/word/excel/texte du
    catalogue public dès l'ajout (statut_extraction_texte="en_attente"),
    AUCUN appel Gemini/Groq donc AUCUN coupe-circuit quota ici.
    """
    try:
        lignes = (
            supabase.table("bibliotheque_publique")
            .select(COLONNES_EXTRACTION_PUBLIQUE)
            .eq("statut_extraction_texte", "en_attente")
            .order("created_at")
            .limit(TAILLE_LOT)
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR lecture file d'attente extraction (bibliotheque_publique) : {e}")
        return 0

    for ligne in lignes:
        fichier_id = ligne["id"]
        try:
            supabase.table("bibliotheque_publique").update({"statut_extraction_texte": "en_cours"}).eq("id", fichier_id).execute()
            contenu = _telecharger(ligne["chemin_stockage"])
            texte = _extraire_texte_pour_extraction_publique(ligne["type_mime"], contenu)
            supabase.table("bibliotheque_publique").update({
                "texte_brut": texte or None,
                "statut_extraction_texte": "fait",
            }).eq("id", fichier_id).execute()
        except Exception as e:
            logging.error(f"ERREUR extraction texte (bibliotheque_publique, fichier_id={fichier_id}) : {e}")
            try:
                supabase.table("bibliotheque_publique").update({"statut_extraction_texte": "echec"}).eq("id", fichier_id).execute()
            except Exception as e2:
                logging.error(f"ERREUR mise à jour statut échec extraction (bibliotheque_publique, fichier_id={fichier_id}) : {e2}")

    return len(lignes)


def relancer_echecs_extraction_a_froid_publique() -> int:
    """Même politique que relancer_echecs_a_froid ci-dessous, pour l'extraction de texte publique -- pas de coupe-circuit quota ici (aucun appel externe)."""
    seuil = (datetime.now(timezone.utc) - COOLDOWN_AUTO_REESSAI).isoformat()
    try:
        resultat = (
            supabase.table("bibliotheque_publique")
            .update({"statut_extraction_texte": "en_attente"})
            .eq("statut_extraction_texte", "echec")
            .lt("created_at", seuil)
            .execute()
        )
        return len(resultat.data or [])
    except Exception as e:
        logging.error(f"ERREUR réessai automatique à froid extraction (bibliotheque_publique) : {e}")
        return 0


def vectoriser_maintenant_publique(fichier_id: str) -> bool:
    """
    06/09/2026, demande Bourama : VRAIE vectorisation A LA DEMANDE d'UN
    SEUL document du catalogue public, appelée depuis
    core/outils_bibliotheque.py quand l'action "trouver_catalogue_public"
    trouve une vraie correspondance sur un document pas encore
    vectorisé -- jamais tout le catalogue d'un coup. Idempotent : si
    déjà "pret", ne refait rien et renvoie True directement.
    """
    try:
        ligne = (
            supabase.table("bibliotheque_publique")
            .select("id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation, statut_vectorisation")
            .eq("id", fichier_id)
            .single()
            .execute()
        ).data
    except Exception as e:
        logging.error(f"ERREUR lecture fichier pour vectorisation à la demande (bibliotheque_publique, fichier_id={fichier_id}) : {e}")
        return False

    if not ligne:
        return False
    if ligne.get("statut_vectorisation") == "pret":
        return True
    if est_en_pause_quota_gemini():
        return False

    maintenant_iso = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("bibliotheque_publique").update({"statut_vectorisation": "en_cours"}).eq("id", fichier_id).execute()
        _vectoriser_publique(ligne)
        supabase.table("bibliotheque_publique").update({
            "statut_vectorisation": "pret",
            "erreur_vectorisation": None,
            "derniere_tentative_vectorisation_a": maintenant_iso,
        }).eq("id", fichier_id).execute()
        return True
    except Exception as e:
        tentatives = (ligne.get("tentatives_vectorisation") or 0) + 1
        if est_erreur_quota_gemini(str(e)):
            activer_pause_quota_gemini()
            logging.error(f"QUOTA GEMINI épuisé (vectorisation à la demande, bibliotheque_publique, fichier_id={fichier_id}) : pause de 24h.")
        else:
            logging.error(f"ERREUR vectorisation à la demande (bibliotheque_publique, fichier_id={fichier_id}, tentative {tentatives}) : {e}")
        try:
            supabase.table("bibliotheque_publique").update({
                "statut_vectorisation": "echec",
                "tentatives_vectorisation": tentatives,
                "erreur_vectorisation": str(e)[:500],
                "derniere_tentative_vectorisation_a": maintenant_iso,
            }).eq("id", fichier_id).execute()
        except Exception as e2:
            logging.error(f"ERREUR mise à jour statut échec (vectorisation à la demande, bibliotheque_publique, fichier_id={fichier_id}) : {e2}")
        return False


COLONNES_PRIVEE = "id, user_id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation"
COLONNES_PUBLIQUE = "id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation"


def _traiter_lot(table: str, colonnes: str, fonction_vectorisation, filtre_niveau: bool) -> int:
    """
    Traite jusqu'à TAILLE_LOT fichiers "en_attente" de `table`, du plus
    ancien au plus récent (pour qu'une longue file ne fasse jamais
    indéfiniment attendre les premiers fichiers ajoutés). `filtre_niveau`
    (True pour fichiers_uploades) restreint à niveau="utilisateur" --
    par sécurité supplémentaire, même si aucun autre niveau n'est censé
    passer à "en_attente" (voir docstring du module : agent/plateforme
    non concernés par ce chantier). Renvoie le nombre de fichiers traités
    (succès + échecs confondus). Ne fait RIEN (renvoie 0 sans même
    interroger Supabase) si la porte est fermée (pause quota Gemini en
    cours, voir embeddings.py) -- inutile d'aller chercher des fichiers
    pour n'en traiter aucun juste après.
    """
    if est_en_pause_quota_gemini():
        return 0
    try:
        requete = supabase.table(table).select(colonnes).eq("statut_vectorisation", "en_attente")
        if filtre_niveau:
            requete = requete.eq("niveau", "utilisateur")
        lignes = requete.order("created_at").limit(TAILLE_LOT).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR lecture file d'attente ({table}) : {e}")
        return 0

    for ligne in lignes:
        # Ajouté le 05/09/2026, demande Bourama : coupe-circuit GLOBAL --
        # si un fichier (ici ou dans une AUTRE file, dossiers designes
        # compris -- même quota Google partagé) a déjà tapé le quota
        # Gemini il y a moins de 24h, "la porte est fermée" : on
        # n'essaie même plus les fichiers suivants de CE lot, on les
        # laisse "en_attente" tels quels.
        if est_en_pause_quota_gemini():
            break
        fichier_id = ligne["id"]
        maintenant_iso = datetime.now(timezone.utc).isoformat()
        try:
            supabase.table(table).update({"statut_vectorisation": "en_cours"}).eq("id", fichier_id).execute()
            fonction_vectorisation(ligne)
            supabase.table(table).update({
                "statut_vectorisation": "pret",
                "erreur_vectorisation": None,
                "derniere_tentative_vectorisation_a": maintenant_iso,
            }).eq("id", fichier_id).execute()
        except Exception as e:
            tentatives = (ligne.get("tentatives_vectorisation") or 0) + 1
            if est_erreur_quota_gemini(str(e)):
                # Quota Gemini épuisé (quotidien) : ferme la porte pour
                # TOUTES les files pendant 24h (voir embeddings.py) et
                # laisse ce fichier "echec" normal, comme n'importe quel
                # autre échec -- pas d'exclusion permanente : passé les
                # 24h, il repart tout seul via relancer_echecs_a_froid,
                # comme prévu.
                activer_pause_quota_gemini()
                nouveau_statut = "echec"
                logging.error(f"QUOTA GEMINI épuisé ({table}, fichier_id={fichier_id}) : pause de 24h, plus aucun fichier tenté d'ici là.")
            else:
                # MAX_TENTATIVES (retries rapprochés dans ce même passage)
                # -- au-delà, "echec" ; relancer_echecs_a_froid ci-dessous
                # se charge des réessais automatiques suivants, plus
                # espacés.
                nouveau_statut = "echec" if tentatives >= MAX_TENTATIVES else "en_attente"
                logging.error(f"ERREUR vectorisation ({table}, fichier_id={fichier_id}, tentative {tentatives}) : {e}")
            try:
                supabase.table(table).update({
                    "statut_vectorisation": nouveau_statut,
                    "tentatives_vectorisation": tentatives,
                    "erreur_vectorisation": str(e)[:500],
                    "derniere_tentative_vectorisation_a": maintenant_iso,
                }).eq("id", fichier_id).execute()
            except Exception as e2:
                logging.error(f"ERREUR mise à jour statut échec ({table}, fichier_id={fichier_id}) : {e2}")
            if est_erreur_quota_gemini(str(e)):
                # Ne pas tenter les fichiers suivants de CE lot non plus
                # (voir commentaire en tête de boucle) -- "personne
                # d'autre n'essaie" dès le premier échec quota.
                break

    return len(lignes)


def traiter_file_attente_une_fois() -> int:
    """Un seul passage sur les deux bibliothèques -- voir api/main.py:_boucle_vectorisation pour la boucle continue."""
    total = 0
    total += _traiter_lot("fichiers_uploades", COLONNES_PRIVEE, _vectoriser_privee, filtre_niveau=True)
    total += _traiter_lot("bibliotheque_publique", COLONNES_PUBLIQUE, _vectoriser_publique, filtre_niveau=False)
    return total


def _relancer_echecs_a_froid_table(table: str) -> int:
    """
    Repasse à "en_attente" les fichiers "echec" de `table` dont le
    cooldown est écoulé et qui n'ont pas dépassé MAX_TENTATIVES_AUTO --
    voir docstring du module. Renvoie le nombre de fichiers relancés. Ne
    fait RIEN tant que la porte est fermée (pause quota Gemini, voir
    embeddings.py) -- pas de sens à relancer des fichiers pour qu'ils
    retapent immédiatement le même quota épuisé.
    """
    if est_en_pause_quota_gemini():
        return 0
    seuil = (datetime.now(timezone.utc) - COOLDOWN_AUTO_REESSAI).isoformat()
    try:
        resultat = (
            supabase.table(table)
            .update({"statut_vectorisation": "en_attente"})
            .eq("statut_vectorisation", "echec")
            .lt("tentatives_vectorisation", MAX_TENTATIVES_AUTO)
            .lt("derniere_tentative_vectorisation_a", seuil)
            .execute()
        )
        return len(resultat.data or [])
    except Exception as e:
        logging.error(f"ERREUR réessai automatique à froid ({table}) : {e}")
        return 0


def relancer_echecs_a_froid() -> int:
    """Appelée en boucle espacée depuis api/main.py:_boucle_reessai_echecs -- voir docstring du module."""
    total = _relancer_echecs_a_froid_table("fichiers_uploades")
    total += _relancer_echecs_a_froid_table("bibliotheque_publique")
    return total


def reinitialiser_pour_reessai(table: str, fichier_id: str) -> bool:
    """
    Réessai MANUEL (bouton "Réessayer" -- voir docstring du module) :
    remet un fichier "echec" à "en_attente" immédiatement, avec un
    compteur de tentatives repartant de zéro (nouvelle pleine série de
    MAX_TENTATIVES essais rapprochés, plus à nouveau éligible au réessai
    automatique à froid ensuite si ça échoue encore). Ne fait rien si le
    fichier n'est pas en échec (pas de sens à "réessayer" un fichier déjà
    prêt ou en cours de traitement) -- renvoie False dans ce cas, True si
    la remise en attente a bien eu lieu.
    """
    resultat = (
        supabase.table(table)
        .update({
            "statut_vectorisation": "en_attente",
            "tentatives_vectorisation": 0,
            "erreur_vectorisation": None,
            "derniere_tentative_vectorisation_a": None,
        })
        .eq("id", fichier_id)
        .eq("statut_vectorisation", "echec")
        .execute()
    )
    return len(resultat.data or []) > 0
