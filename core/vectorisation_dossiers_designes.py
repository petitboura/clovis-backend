"""
Cree le 04/09/2026, Bourama : vectorisation automatique en arriere-plan
de tout le contenu (hormis video) d'un dossier designe sur le telephone
(dossiers_designes_mobile.py), transfere via api/dossiers_designes.py.

MIS A JOUR le 06/09/2026 (demande Bourama, meme chantier applique aussi
a la bibliotheque publique -- voir core/file_attente_vectorisation.py) :
la VRAIE vectorisation automatique a la designation est retiree pour
tout sauf l'image. A la place :
- Image : inchange, vraie vectorisation automatique des la designation.
- PDF / Word / Excel / texte brut : EXTRACTION GRATUITE du texte brut
  des la designation (necessite_extraction_texte ci-dessous, statut
  dedie statut_extraction_texte), cherchable par mot-cle immediatement
  (voir chercher_fichiers_dossier_designe_par_metadonnees). La VRAIE
  vectorisation (sens) n'a lieu qu'A LA DEMANDE, quand l'exploration en
  direct (core/outils_mobile.py::explorer_dossier) trouve une vraie
  correspondance sur ce fichier precis -- voir vectoriser_maintenant.
- Audio / Video : plus rien d'automatique du tout (ni extraction ni
  vectorisation). Tout a la demande (vectoriser_maintenant), y compris
  la video desormais ACCEPTEE a la designation (l'interdiction du
  04/09 etait uniquement due au cout de la vectorisation automatique,
  qui disparait ici -- voir api/dossiers_designes.py).

Module DEDIE, distinct de core/file_attente_vectorisation.py (bibliotheque
perso/publique) -- meme inspiration (file d'attente, statuts, robustesse
au redemarrage) mais PAS le meme module, pour ne rien risquer sur le
systeme existant deja en production, et parce que la politique de reessai
est volontairement differente ici (voir plus bas).

Reutilise les fonctions d'extraction/description DEJA existantes
ailleurs dans le projet (aucune logique dupliquee pour rien) :
- PDF : meme logique que core/bibliotheque_rag.py::extraire_pages_pdf,
  dupliquee ici sur des bytes (voir extraire_pages_pdf_bytes plus bas --
  meme convention de duplication volontaire qu'ailleurs dans le projet
  pour ne pas creer de dependance croisee entre circuits)
- Image : core/description_multimedia.py::decrire_image_bibliotheque
- Audio : core/description_multimedia.py::transcrire_audio_bibliotheque
- Video : core/description_multimedia.py::transcrire_et_decrire_video_bibliotheque
  (transcription + description de frames -- 06/09, briques reutilisees
  de la video de chat, jamais branchees avant a une recherche future)
- Word (.docx) / Excel (.xlsx) : memes fonctions que api/uploads.py
  (_extraire_texte_docx/_extraire_texte_xlsx), dupliquees ici sur des
  bytes -- meme convention de duplication volontaire deja assumee entre
  api/uploads.py et core/lecture_fichier_mobile.py pour ce meme type de
  contenu (voir leurs docstrings respectifs).
- Texte brut (txt/md/csv/json/code...) : lu tel quel, meme liste
  d'extensions que core/lecture_fichier_mobile.py::EXTENSIONS_TEXTE_BRUT.
Le decoupage + embedding lui-meme passe par indexer_texte_bibliotheque-
like, mais ecrit dans documents_dossier_designe (table dediee) --
volontairement pas indexer_texte_bibliotheque (qui ecrit dans
documents_bibliotheque, scope different).

Politique de reessai (04/09, precisee par Bourama) : PAS de reessai
rapproche -- un echec passe DIRECTEMENT en statut "echec" des la
premiere tentative ratee (pas de boucle de retentatives immediates dans
le meme passage, contrairement a file_attente_vectorisation.py). Le seul
reessai est AUTOMATIQUE A FROID (apres COOLDOWN_REESSAI), et SANS
PLAFOND -- contrairement a MAX_TENTATIVES_AUTO de la bibliotheque perso,
un fichier repart indefiniment tant qu'il echoue, jusqu'a reussir. Pas de
bouton "reessayer" manuel pour l'instant (pas demande ici, a ajouter si
besoin). Meme politique reprise pour la nouvelle file d'extraction de
texte (statut_extraction_texte).
"""

import io
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from supabase import create_client, ClientOptions
from client_http_supabase import nouveau_client_http_supabase

sys.path.append(os.path.dirname(__file__))
from embeddings import activer_pause_quota_gemini, decouper_texte, est_en_pause_quota_gemini, est_erreur_quota_gemini, vectoriser  # noqa: E402
from description_multimedia import decrire_image_bibliotheque, transcrire_audio_bibliotheque, transcrire_et_decrire_video_bibliotheque  # noqa: E402

BUCKET_DOSSIERS_DESIGNES = "bibliotheque"  # meme bucket que la bibliotheque perso, sous-dossier "dossiers_designes/" (voir api/dossiers_designes.py)
TAILLE_LOT = 5  # meme garde-fou que file_attente_vectorisation.py -- un passage ne tourne jamais indefiniment
TAILLE_MAX_CHUNKS_PAR_DOCUMENT = 400  # meme plafond que bibliotheque_rag.py

# Reessai automatique a froid, SANS plafond de tentatives (voir docstring
# du module) -- seul un delai separe deux tentatives successives.
COOLDOWN_REESSAI = timedelta(minutes=15)

EXTENSIONS_TEXTE_BRUT = {
    "txt", "md", "csv", "json", "py", "js", "ts", "tsx", "jsx", "html", "css",
    "xml", "yaml", "yml", "java", "c", "cpp", "kt", "sh", "log",
}

TYPES_MIME_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TYPES_MIME_EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _get_secret(cle):
    return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"), options=ClientOptions(httpx_client=nouveau_client_http_supabase()))


def _extension(nom_fichier: str) -> str:
    return nom_fichier.rsplit(".", 1)[-1].lower() if "." in (nom_fichier or "") else ""


def necessite_vectorisation(type_mime: str | None) -> bool:
    """
    06/09/2026 : seule l'IMAGE declenche encore une vraie vectorisation
    automatique a la designation (cout Gemini vision par image jugee
    acceptable par Bourama, contrairement au reste). Tout le reste passe
    par necessite_extraction_texte (gratuit, pdf/word/excel/texte) ou
    reste totalement en attente d'une demande explicite (audio/video).
    """
    return bool(type_mime) and type_mime.startswith("image/")


def necessite_extraction_texte(type_mime: str | None, nom_fichier: str) -> bool:
    """
    06/09/2026 : pdf/word/excel/texte brut recoivent une extraction de
    texte GRATUITE (aucun appel Gemini/Groq) des la designation, pour
    etre cherchables par mot-cle immediatement -- voir
    chercher_fichiers_dossier_designe_par_metadonnees. La vraie
    vectorisation (sens) de ce texte reste a la demande (voir
    vectoriser_maintenant).
    """
    if type_mime == "application/pdf":
        return True
    if type_mime in (TYPES_MIME_WORD, TYPES_MIME_EXCEL):
        return True
    if type_mime == "text/plain" or (not type_mime and _extension(nom_fichier) in EXTENSIONS_TEXTE_BRUT):
        return True
    if type_mime is None:
        return _extension(nom_fichier) in EXTENSIONS_TEXTE_BRUT
    return False


def _extraire_texte_docx_bytes(contenu: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(contenu))
    morceaux = [p.text for p in document.paragraphs]
    for table in document.tables:
        for ligne in table.rows:
            morceaux.append("\t".join(cellule.text for cellule in ligne.cells))
    return "\n".join(morceaux)


def _extraire_texte_xlsx_bytes(contenu: bytes) -> str:
    import openpyxl

    classeur = openpyxl.load_workbook(io.BytesIO(contenu), data_only=True)
    morceaux = []
    for feuille in classeur.worksheets:
        morceaux.append(f"--- Feuille : {feuille.title} ---")
        for ligne in feuille.iter_rows(values_only=True):
            morceaux.append("\t".join("" if v is None else str(v) for v in ligne))
    return "\n".join(morceaux)


def _telecharger(chemin_stockage: str) -> bytes:
    return supabase.storage.from_(BUCKET_DOSSIERS_DESIGNES).download(chemin_stockage)


def _nettoyer_chunks_existants(fichier_id: str) -> None:
    """Meme principe que file_attente_vectorisation.py -- un traitement
    interrompu a moitie ne doit jamais laisser de chunks dupliques/incomplets."""
    supabase.table("documents_dossier_designe").delete().eq("fichier_id", fichier_id).execute()


def _indexer_texte(
    texte: str, fichier_id: str, user_id: str,
    page_debut=None, page_fin=None, timestamp_debut=None, timestamp_fin=None,
) -> int:
    morceaux = decouper_texte(texte)[:TAILLE_MAX_CHUNKS_PAR_DOCUMENT]
    lignes = []
    for morceau in morceaux:
        if not morceau.strip():
            continue
        embedding = vectoriser(morceau)
        lignes.append({
            "fichier_id": fichier_id,
            "user_id": user_id,
            "contenu": morceau,
            "embedding": embedding,
            "page_debut": page_debut,
            "page_fin": page_fin,
            "timestamp_debut": timestamp_debut,
            "timestamp_fin": timestamp_fin,
        })
    if lignes:
        supabase.table("documents_dossier_designe").insert(lignes).execute()
    return len(lignes)


def _vectoriser_fichier(ligne: dict) -> None:
    fichier_id = ligne["id"]
    user_id = ligne["user_id"]
    type_mime = ligne["type_mime"] or ""
    nom_fichier = ligne["nom_fichier"]
    extension = _extension(nom_fichier)
    contenu = _telecharger(ligne["chemin_stockage"])

    _nettoyer_chunks_existants(fichier_id)

    if type_mime == "application/pdf":
        for numero, texte_page in enumerate(extraire_pages_pdf_bytes(contenu), start=1):
            if texte_page.strip():
                _indexer_texte(texte_page, fichier_id, user_id, page_debut=numero, page_fin=numero)
    elif type_mime.startswith("image/"):
        description = decrire_image_bibliotheque(contenu, type_mime)
        if description:
            _indexer_texte(description, fichier_id, user_id)
    elif type_mime.startswith("audio/"):
        segments = transcrire_audio_bibliotheque(contenu, nom_fichier)
        for segment in segments or []:
            texte = (segment.get("text") or "").strip()
            if texte:
                _indexer_texte(texte, fichier_id, user_id, timestamp_debut=segment.get("start"), timestamp_fin=segment.get("end"))
    elif type_mime.startswith("video/"):
        resultat = transcrire_et_decrire_video_bibliotheque(contenu, nom_fichier, extension or "mp4")
        for segment in (resultat or {}).get("segments_audio") or []:
            texte = (segment.get("text") or "").strip()
            if texte:
                _indexer_texte(texte, fichier_id, user_id, timestamp_debut=segment.get("start"), timestamp_fin=segment.get("end"))
        for description in (resultat or {}).get("descriptions_frames") or []:
            if description.strip():
                _indexer_texte(description, fichier_id, user_id)
    elif type_mime == TYPES_MIME_WORD:
        texte = _extraire_texte_docx_bytes(contenu)
        if texte.strip():
            _indexer_texte(texte, fichier_id, user_id)
    elif type_mime == TYPES_MIME_EXCEL:
        texte = _extraire_texte_xlsx_bytes(contenu)
        if texte.strip():
            _indexer_texte(texte, fichier_id, user_id)
    elif type_mime == "text/plain" or extension in EXTENSIONS_TEXTE_BRUT:
        texte = contenu.decode("utf-8", errors="ignore")
        if texte.strip():
            _indexer_texte(texte, fichier_id, user_id)


def extraire_pages_pdf_bytes(contenu: bytes) -> list[str]:
    """Meme logique que bibliotheque_rag.py::extraire_pages_pdf, mais sur des bytes (pas un chemin de fichier -- le contenu vient de Supabase Storage, jamais ecrit sur disque)."""
    import PyPDF2

    pages = []
    reader = PyPDF2.PdfReader(io.BytesIO(contenu))
    for page in reader.pages:
        pages.append((page.extract_text() or "").replace("\x00", ""))
    return pages


def _extraire_texte_pour_extraction(type_mime: str | None, extension: str, contenu: bytes) -> str:
    """
    Extraction GRATUITE (aucun appel Gemini/Groq) pour pdf/word/excel/texte
    brut -- voir necessite_extraction_texte. Reutilise les memes fonctions
    que la vraie vectorisation (extraire_pages_pdf_bytes,
    _extraire_texte_docx_bytes, _extraire_texte_xlsx_bytes) mais assemble
    le texte en UN SEUL bloc (pas de decoupage par page ici, juste pour la
    recherche par mot-cle -- le decoupage/embedding reel n'a lieu qu'a la
    demande, voir vectoriser_maintenant).
    """
    if type_mime == "application/pdf":
        return "\n\n".join(extraire_pages_pdf_bytes(contenu))
    if type_mime == TYPES_MIME_WORD:
        return _extraire_texte_docx_bytes(contenu)
    if type_mime == TYPES_MIME_EXCEL:
        return _extraire_texte_xlsx_bytes(contenu)
    return contenu.decode("utf-8", errors="ignore")


def remettre_en_attente_bloques() -> None:
    """Appelee une fois au demarrage du process -- voir docstring de file_attente_vectorisation.py::remettre_en_attente_bloques (meme raison : Railway redeploie a chaque push)."""
    try:
        supabase.table("fichiers_dossier_designe").update({"statut_vectorisation": "en_attente"}).eq(
            "statut_vectorisation", "en_cours"
        ).execute()
        supabase.table("fichiers_dossier_designe").update({"statut_extraction_texte": "en_attente"}).eq(
            "statut_extraction_texte", "en_cours"
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR remise en attente au demarrage (dossiers designes) : {e}")


COLONNES = "id, user_id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation, statut_vectorisation"
COLONNES_EXTRACTION = "id, chemin_stockage, nom_fichier, type_mime, tentatives_vectorisation"


def traiter_extractions_texte_une_fois() -> int:
    """
    06/09/2026 : pendant GRATUITE distincte de traiter_file_attente_une_fois
    -- extrait le texte brut de pdf/word/excel/texte des la designation
    (statut_extraction_texte = "en_attente"), AUCUN appel Gemini/Groq donc
    AUCUN coupe-circuit quota ici. Meme garde-fou TAILLE_LOT qu'ailleurs.
    """
    try:
        lignes = (
            supabase.table("fichiers_dossier_designe")
            .select(COLONNES_EXTRACTION)
            .eq("statut_extraction_texte", "en_attente")
            .order("created_at")
            .limit(TAILLE_LOT)
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR lecture file d'attente extraction (dossiers designes) : {e}")
        return 0

    for ligne in lignes:
        fichier_id = ligne["id"]
        try:
            supabase.table("fichiers_dossier_designe").update({"statut_extraction_texte": "en_cours"}).eq("id", fichier_id).execute()
            contenu = _telecharger(ligne["chemin_stockage"])
            texte = _extraire_texte_pour_extraction(ligne["type_mime"], _extension(ligne["nom_fichier"]), contenu)
            supabase.table("fichiers_dossier_designe").update({
                "texte_brut": texte or None,
                "statut_extraction_texte": "fait",
            }).eq("id", fichier_id).execute()
        except Exception as e:
            logging.error(f"ERREUR extraction texte (dossiers designes, fichier_id={fichier_id}) : {e}")
            try:
                supabase.table("fichiers_dossier_designe").update({"statut_extraction_texte": "echec"}).eq("id", fichier_id).execute()
            except Exception as e2:
                logging.error(f"ERREUR mise a jour statut echec extraction (dossiers designes, fichier_id={fichier_id}) : {e2}")

    return len(lignes)


def relancer_echecs_extraction_a_froid() -> int:
    """Meme politique que relancer_echecs_a_froid ci-dessous, pour l'extraction de texte -- pas de coupe-circuit quota ici (aucun appel externe)."""
    seuil = (datetime.now(timezone.utc) - COOLDOWN_REESSAI).isoformat()
    try:
        resultat = (
            supabase.table("fichiers_dossier_designe")
            .update({"statut_extraction_texte": "en_attente"})
            .eq("statut_extraction_texte", "echec")
            .lt("created_at", seuil)
            .execute()
        )
        return len(resultat.data or [])
    except Exception as e:
        logging.error(f"ERREUR reessai automatique a froid extraction (dossiers designes) : {e}")
        return 0


def vectoriser_maintenant(fichier_id: str) -> bool:
    """
    06/09/2026, demande Bourama : VRAIE vectorisation A LA DEMANDE d'UN
    SEUL fichier precis, appelee depuis l'exploration en direct
    (core/outils_mobile.py::explorer_dossier) quand ce fichier correspond
    vraiment a la recherche -- jamais tout un dossier d'un coup. Idempotent
    : si deja "pret", ne refait rien et renvoie True directement.

    Renvoie True si le fichier est desormais vectorise (ou l'etait deja),
    False en cas d'echec (statut passe a "echec", repris ensuite par
    relancer_echecs_a_froid comme n'importe quel autre echec).
    """
    try:
        ligne = (
            supabase.table("fichiers_dossier_designe")
            .select(COLONNES)
            .eq("id", fichier_id)
            .single()
            .execute()
        ).data
    except Exception as e:
        logging.error(f"ERREUR lecture fichier pour vectorisation a la demande (dossiers designes, fichier_id={fichier_id}) : {e}")
        return False

    if not ligne:
        return False
    if ligne.get("statut_vectorisation") == "pret":
        return True

    if est_en_pause_quota_gemini():
        return False

    maintenant_iso = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("fichiers_dossier_designe").update({"statut_vectorisation": "en_cours"}).eq("id", fichier_id).execute()
        _vectoriser_fichier(ligne)
        supabase.table("fichiers_dossier_designe").update({
            "statut_vectorisation": "pret",
            "erreur_vectorisation": None,
            "derniere_tentative_vectorisation_a": maintenant_iso,
        }).eq("id", fichier_id).execute()
        return True
    except Exception as e:
        tentatives = (ligne.get("tentatives_vectorisation") or 0) + 1
        if est_erreur_quota_gemini(str(e)):
            activer_pause_quota_gemini()
            logging.error(f"QUOTA GEMINI épuisé (vectorisation à la demande, dossiers designes, fichier_id={fichier_id}) : pause de 24h.")
        else:
            logging.error(f"ERREUR vectorisation à la demande (dossiers designes, fichier_id={fichier_id}, tentative {tentatives}) : {e}")
        try:
            supabase.table("fichiers_dossier_designe").update({
                "statut_vectorisation": "echec",
                "tentatives_vectorisation": tentatives,
                "erreur_vectorisation": str(e)[:500],
                "derniere_tentative_vectorisation_a": maintenant_iso,
            }).eq("id", fichier_id).execute()
        except Exception as e2:
            logging.error(f"ERREUR mise a jour statut echec (vectorisation à la demande, dossiers designes, fichier_id={fichier_id}) : {e2}")
        return False


def traiter_file_attente_une_fois() -> int:
    """
    Traite jusqu'a TAILLE_LOT fichiers "en_attente", du plus ancien au
    plus recent. PAS de reessai rapproche (voir docstring du module) :
    un echec passe direct en "echec", relancer_echecs_a_froid ci-dessous
    s'en charge ensuite. Renvoie le nombre de fichiers traites. Ne fait
    RIEN (renvoie 0 sans meme interroger Supabase) si la porte est
    fermee (pause quota Gemini en cours, voir embeddings.py -- coupe-
    circuit GLOBAL partage avec file_attente_vectorisation.py, meme
    quota Google).
    """
    if est_en_pause_quota_gemini():
        return 0
    try:
        lignes = (
            supabase.table("fichiers_dossier_designe")
            .select(COLONNES)
            .eq("statut_vectorisation", "en_attente")
            .order("created_at")
            .limit(TAILLE_LOT)
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR lecture file d'attente (dossiers designes) : {e}")
        return 0

    for ligne in lignes:
        # Coupe-circuit GLOBAL (ajoute le 05/09/2026, demande Bourama) :
        # si un fichier -- ici ou dans une AUTRE file, bibliotheque
        # perso/publique comprise, meme quota Google partage -- a deja
        # tape le quota Gemini il y a moins de 24h, on n'essaie meme
        # plus les fichiers suivants de CE lot ; ils restent
        # "en_attente" tels quels.
        if est_en_pause_quota_gemini():
            break
        fichier_id = ligne["id"]
        maintenant_iso = datetime.now(timezone.utc).isoformat()
        try:
            supabase.table("fichiers_dossier_designe").update({"statut_vectorisation": "en_cours"}).eq("id", fichier_id).execute()
            _vectoriser_fichier(ligne)
            supabase.table("fichiers_dossier_designe").update({
                "statut_vectorisation": "pret",
                "erreur_vectorisation": None,
                "derniere_tentative_vectorisation_a": maintenant_iso,
            }).eq("id", fichier_id).execute()
        except Exception as e:
            tentatives = (ligne.get("tentatives_vectorisation") or 0) + 1
            if est_erreur_quota_gemini(str(e)):
                # Quota Gemini epuise (quotidien) : ferme la porte pour
                # TOUTES les files pendant 24h (voir embeddings.py). Ce
                # fichier reste "echec" normal, sans exclusion
                # permanente -- passe les 24h, il repart tout seul via
                # relancer_echecs_a_froid, comme prevu par la politique
                # "sans plafond" du module.
                activer_pause_quota_gemini()
                logging.error(f"QUOTA GEMINI épuisé (dossiers designes, fichier_id={fichier_id}) : pause de 24h, plus aucun fichier tenté d'ici là.")
            else:
                logging.error(f"ERREUR vectorisation (dossiers designes, fichier_id={fichier_id}, tentative {tentatives}) : {e}")
            try:
                supabase.table("fichiers_dossier_designe").update({
                    "statut_vectorisation": "echec",
                    "tentatives_vectorisation": tentatives,
                    "erreur_vectorisation": str(e)[:500],
                    "derniere_tentative_vectorisation_a": maintenant_iso,
                }).eq("id", fichier_id).execute()
            except Exception as e2:
                logging.error(f"ERREUR mise a jour statut echec (dossiers designes, fichier_id={fichier_id}) : {e2}")
            if est_erreur_quota_gemini(str(e)):
                # Ne pas tenter les fichiers suivants de CE lot non plus
                # (voir commentaire en tête de boucle).
                break

    return len(lignes)


def chercher_dossiers_designes(question: str, user_id: str, match_count: int = 5) -> list:
    """
    Recherche semantique dans TOUT le contenu deja vectorise des dossiers
    designes de `user_id` (tous dossiers confondus -- pas de filtre par
    dossier_nom ici, voir recherche_dossiers_designes en SQL). Meme
    principe que chercher_bibliotheque (core/bibliotheque_rag.py), sur la
    table dediee documents_dossier_designe.

    Renvoie une liste de {contenu, similarite, fichier_id, nom_fichier,
    dossier_nom, chemin, url_publique, type_mime, page_debut, page_fin,
    timestamp_debut, timestamp_fin} triee par pertinence. `chemin` est la
    liste ordonnee des sous-dossiers depuis la racine designee (jamais le
    nom du fichier), voir migrations/2026_09_04_dossiers_designes_
    vectorisation.sql.
    """
    if not user_id:
        logging.error("chercher_dossiers_designes appele sans user_id : renvoie vide.")
        return []

    if est_en_pause_quota_gemini():
        # Porte fermee (pause quota Gemini en cours, voir embeddings.py) --
        # inutile de tenter un appel qu'on sait deja voue a l'echec.
        return []

    try:
        vecteur = vectoriser(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        if est_erreur_quota_gemini(str(e)):
            activer_pause_quota_gemini()
        logging.error(f"ERREUR VECTORISATION dossiers designes (Gemini) : {e}")
        return []

    try:
        return supabase.rpc(
            "recherche_dossiers_designes",
            {"query_embedding": vecteur, "match_count": match_count, "p_user_id": user_id},
        ).execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE RPC recherche_dossiers_designes (user_id={user_id}) : {e}")
        return []


def formater_source_dossier_designe(r: dict) -> str | None:
    """
    Meme role que formater_source_bibliotheque (core/bibliotheque_rag.py),
    adapte aux dossiers designes : ajoute le chemin (dossier_nom + sous-
    dossiers) pour que l'IA sache d'ou vient chaque extrait, en plus de la
    page/du timestamp quand ce chunk en a un.
    """
    if not (r.get("nom_fichier") and r.get("dossier_nom")):
        return None
    reperage = ""
    if r.get("page_debut") is not None:
        if r.get("page_fin") and r["page_fin"] != r["page_debut"]:
            reperage = f", page {r['page_debut']}-{r['page_fin']}"
        else:
            reperage = f", page {r['page_debut']}"
    elif r.get("timestamp_debut") is not None:
        debut = int(r["timestamp_debut"])
        reperage = f", à {debut // 60:02d}:{debut % 60:02d}"
    chemin = r.get("chemin") or []
    emplacement = " / ".join([r["dossier_nom"], *chemin]) if chemin else r["dossier_nom"]
    lien = f", {r['url_publique']}" if r.get("url_publique") else ""
    type_mime = r.get("type_mime") or ""
    return f"(Source : {r['nom_fichier']}{reperage}, dossier {emplacement}{lien}, {type_mime})"


CATEGORIES_TYPE_FICHIER = {
    "pdf": "application/pdf",
    "image": "image/",
    "audio": "audio/",
    "video": "video/",
    "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "texte": "text/plain",
}
# Categories dont la valeur ci-dessus est un PREFIXE (type_mime commencant
# par .../) plutot qu'une valeur EXACTE -- pdf/word/excel/texte ont un seul
# type_mime possible, image/audio/video en ont plusieurs (image/png,
# image/jpeg, etc.), meme decoupage que necessite_vectorisation ci-dessus.
_CATEGORIES_TYPE_FICHIER_PREFIXE = {"image", "audio", "video"}


def chercher_fichiers_dossier_designe_par_metadonnees(
    user_id: str,
    dossier_nom: str,
    mot_cle: str | None = None,
    type_fichier: str | None = None,
    chemin: list[str] | None = None,
) -> list:
    """
    Recherche PAR METADONNEES (nom de fichier, type, emplacement) dans les
    fichiers deja transferes d'un dossier designe -- ajoutee le 06/09/2026,
    demande Bourama, INDEPENDANTE de chercher_dossiers_designes ci-dessus :
    ne regarde jamais le contenu, uniquement les colonnes de
    fichiers_dossier_designe. Les deux recherches tournent EN PARALLELE
    (voir core/outils_mobile.py::explorer_dossier, action
    "chercher_par_contenu"), jamais l'une apres l'autre. Aucun appel Gemini
    ici : instantanee, gratuite, et fonctionne aussi sur des fichiers PAS
    ENCORE vectorises (n'importe quel statut_vectorisation).

    - mot_cle : sous-chaine insensible a la casse cherchee dans
      nom_fichier OU texte_brut (06/09 : texte deja extrait gratuitement
      pour pdf/word/excel/texte, voir necessite_extraction_texte).
      None/vide = pas de filtre.
    - type_fichier : categorie EXACTE parmi CATEGORIES_TYPE_FICHIER
      ci-dessus. None/vide = pas de filtre. Une valeur hors de cette liste
      est ignoree ici (la validation stricte, avec message d'erreur, est
      faite en amont dans core/outils_mobile.py).
    - chemin : sous-dossier EXACT depuis la racine du dossier designe
      (liste ORDONNEE de noms, meme convention que la colonne `chemin` de
      fichiers_dossier_designe -- jamais juste le dernier niveau). None =
      pas de filtre sur l'emplacement (cherche a n'importe quelle
      profondeur). Liste vide = uniquement les fichiers a la racine.

    Renvoie une liste de {id, nom_fichier, type_mime, chemin, url_publique,
    statut_vectorisation, dossier_nom} -- pas de `contenu` ici (aucun
    contenu n'est lu par cette fonction).
    """
    if not user_id or not dossier_nom:
        logging.error("chercher_fichiers_dossier_designe_par_metadonnees appele sans user_id/dossier_nom : renvoie vide.")
        return []

    requete = (
        supabase.table("fichiers_dossier_designe")
        .select("id, nom_fichier, type_mime, chemin, url_publique, statut_vectorisation, dossier_nom")
        .eq("user_id", user_id)
        .eq("dossier_nom", dossier_nom)
    )
    if mot_cle:
        requete = requete.or_(f"nom_fichier.ilike.%{mot_cle}%,texte_brut.ilike.%{mot_cle}%")
    if type_fichier and type_fichier in CATEGORIES_TYPE_FICHIER:
        valeur = CATEGORIES_TYPE_FICHIER[type_fichier]
        if type_fichier in _CATEGORIES_TYPE_FICHIER_PREFIXE:
            requete = requete.like("type_mime", f"{valeur}%")
        else:
            requete = requete.eq("type_mime", valeur)

    try:
        lignes = requete.execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE recherche metadonnees dossier designe (user_id={user_id}, dossier_nom={dossier_nom}) : {e}")
        return []

    if chemin is not None:
        # Comparaison EXACTE cote Python (pas de filtre jsonb cote SQL ici,
        # pour eviter tout risque de mismatch de serialisation jsonb via le
        # client Supabase) -- meme approche pragmatique que le filtrage par
        # dossier_nom deja fait cote Python sur les resultats de
        # chercher_dossiers_designes, voir core/outils_mobile.py.
        lignes = [ligne for ligne in lignes if (ligne.get("chemin") or []) == chemin]

    return lignes


def formater_resultat_metadonnees_dossier_designe(r: dict) -> str:
    """
    Meme role que formater_source_dossier_designe ci-dessus, mais pour un
    resultat de chercher_fichiers_dossier_designe_par_metadonnees (fichier
    trouve par nom/type/emplacement, pas par son contenu) : precise le
    statut de vectorisation quand il n'est pas encore "pret", pour que
    l'IA sache qu'elle ne peut pas (encore) lire ce fichier directement.
    """
    chemin = r.get("chemin") or []
    emplacement = " / ".join([r["dossier_nom"], *chemin]) if chemin else r["dossier_nom"]
    lien = f", {r['url_publique']}" if r.get("url_publique") else ""
    type_mime = r.get("type_mime") or ""
    statut = r.get("statut_vectorisation")
    suffixe_statut = "" if statut == "pret" else f", pas encore indexé ({statut})"
    return f"{r['nom_fichier']} (dossier {emplacement}, {type_mime}{lien}{suffixe_statut})"


def relancer_echecs_a_froid() -> int:
    """
    Reessai automatique a froid, SANS plafond pour les echecs (voir
    docstring du module) -- seul le cooldown separe deux tentatives. Ne
    fait RIEN tant que la porte est fermee (pause quota Gemini en cours,
    voir embeddings.py) -- pas de sens a relancer des fichiers pour
    qu'ils retapent immediatement le meme quota epuise ; passe la pause
    (24h), reprise 100% normale, aucune exclusion permanente par
    fichier.
    """
    if est_en_pause_quota_gemini():
        return 0
    seuil = (datetime.now(timezone.utc) - COOLDOWN_REESSAI).isoformat()
    try:
        resultat = (
            supabase.table("fichiers_dossier_designe")
            .update({"statut_vectorisation": "en_attente"})
            .eq("statut_vectorisation", "echec")
            .lt("derniere_tentative_vectorisation_a", seuil)
            .execute()
        )
        return len(resultat.data or [])
    except Exception as e:
        logging.error(f"ERREUR reessai automatique a froid (dossiers designes) : {e}")
        return 0
