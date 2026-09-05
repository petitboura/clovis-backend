"""
Outils MCP de la bibliothèque personnelle (fichiers, dossiers, recherche
RAG interne et publique, catalogue public).

Extrait de core/serveur_mcp_generation.py le 05/09/2026 (découpage d'un
fichier de 2524 lignes) -- aucun changement de comportement, uniquement un
déplacement de code.
"""

import os
import logging
import tempfile
import base64
import requests

from core.bibliotheque_fichiers import (
    chercher_fichiers as _chercher_fichiers,
    enregistrer_fichier as _enregistrer_fichier,
    enregistrer_lien as _enregistrer_lien,
    lister_fichiers as _lister_fichiers,
    supprimer_fichier as _supprimer_fichier,
)
from core.bibliotheque_rag import (
    chercher_bibliotheque as _chercher_bibliotheque,
    chercher_bibliotheque_combinee as _chercher_bibliotheque_combinee,
    chercher_bibliotheque_publique as _chercher_bibliotheque_publique,
    lire_document_bibliotheque_en_entier as _lire_document_bibliotheque_en_entier,
    indexer_pdf_bibliotheque as _indexer_pdf_bibliotheque,
    indexer_texte_bibliotheque as _indexer_texte_bibliotheque,
    indexer_transcription_bibliotheque as _indexer_transcription_bibliotheque,
    formater_source_bibliotheque as _formater_source_bibliotheque,
)
from core.catalogue_public_rag import (
    chercher_catalogue_public as _chercher_catalogue_public,
    lire_document_catalogue_public as _lire_document_catalogue_public,
    lister_catalogue_public as _lister_catalogue_public,
)
from core.dossiers_bibliotheque import (
    _proprietaire_dossier,
    creer_dossier as _creer_dossier,
    lister_dossiers as _lister_dossiers,
    lister_fichiers_ids_dossier as _lister_fichiers_ids_dossier,
    ranger_fichier as _ranger_fichier,
    renommer_dossier as _renommer_dossier,
    retirer_fichier as _retirer_fichier,
    supprimer_dossier as _supprimer_dossier,
)
from core.description_multimedia import (
    decrire_image_bibliotheque as _decrire_image_bibliotheque,
    transcrire_audio_bibliotheque as _transcrire_audio_bibliotheque,
)

from core.outils_generation_commun import (
    mcp_generation,
    Context,
    TYPES_EMPLACEMENT_BIBLIOTHEQUE,
    _libelle_emplacement,
    _lister_emplacements_document,
    _TAILLE_MAX_OCTETS_BIBLIOTHEQUE,
    _supabase_memoire,
)



@mcp_generation.tool()
def chercher_fichier(recherche: str, agent_id: str = None, user_id: str = None) -> str:
    """
    Cherche un fichier déjà uploadé (image, PDF, audio, vidéo, autre)
    dans la bibliothèque -- uploadé soit par la plateforme (accessible à
    tous les agents), soit par le créateur de CET agent, soit par CET
    utilisateur lui-même dans une conversation passée. `recherche` est un
    mot-clé (nom de fichier ou sujet). `agent_id` et `user_id` doivent
    être exactement ceux donnés dans tes instructions système, pas
    inventés. Renvoie la liste des fichiers trouvés (nom, url, niveau)
    ou un message si rien n'est trouvé -- à toi ensuite d'inclure le
    lien dans ta réponse (![...](url) pour une image, [...](url) sinon).
    """
    try:
        resultats = _chercher_fichiers(recherche, agent_id=agent_id, user_id=user_id)
    except Exception:
        return "Erreur : la recherche de fichier a échoué, réessaie."

    if not resultats:
        return "Aucun fichier trouvé pour cette recherche."

    return "\n".join(
        f"- {f['nom_fichier']} ({f['niveau']}) : {f['url_publique']}"
        + (f" -- {f['description']}" if f.get("description") else "")
        for f in resultats
    )


@mcp_generation.tool()
def gerer_document_bibliotheque(
    action: str,
    ctx: Context,
    question: str = "",
    fichier_id: str = "",
    url: str = "",
    titre: str = "",
    contenu: str = "",
    nom_fichier: str = "",
    type_mime: str = "",
    description: str = "",
    contenu_base64: str = "",
    url_fichier: str = "",
    type_emplacement: str = "",
    emplacement_id: str = "",
    dossier_id: str = "",
    type_fichier: str = "",
    nom_dossier: str = "",
    decalage: int = 0,
) -> str:
    """
    Gère la bibliothèque personnelle de CET utilisateur, et permet aussi de
    chercher/localiser des documents dans le catalogue public. Section
    "Bibliothèque" de "Mon
    espace" -- un seul outil, plusieurs actions, au lieu de 12 outils
    séparés (consolidé le 26/08, demande Bourama : "un outil puis
    paramètres c'est pas mieux que plusieurs outils si on peut regrouper",
    pattern "action + paramètres" -- réduit le catalogue envoyé au
    routeur/modèle et limite les confusions de noms d'outils proches).

    Quand tu affiches le lien d'un document (n'importe quelle action
    ci-dessous), le texte entre crochets DOIT être le vrai nom du
    fichier, jamais l'URL elle-même.

    04/09/2026 (demande Bourama : plus besoin d'une action séparée pour
    "donner" un fichier -- "chercher"/"lister"/"trouver_catalogue_public"/
    "lister_catalogue_public" ci-dessous renvoient déjà le lien de chaque
    document trouvé). Dès que tu as ce lien (par une de ces actions, ou
    parce qu'il est déjà visible plus tôt dans cette même conversation),
    tu peux directement écrire ce lien dans ta réponse, en markdown, avec
    le vrai nom du fichier comme texte affiché -- il apparaît alors
    automatiquement en pièce jointe pour l'étudiant, sans appel d'outil
    supplémentaire. Fais-le à la demande explicite de l'étudiant
    ("donne-moi ce fichier", "envoie-moi le PDF"), ou quand le contexte
    indique clairement qu'il veut le fichier lui-même plutôt qu'un
    résumé -- pas systématiquement à chaque fois qu'un document apparaît
    dans une recherche. Même chose pour redonner un fichier que
    l'utilisateur a joint DANS cette conversation : son lien réel est
    déjà visible entre crochets "[Lien réel du fichier : ...]" juste
    après son upload, réécris-le directement, pas besoin d'appeler cet
    outil pour ça.

    `action` doit être l'une de :
    - "chercher" : RECHERCHE COMBINÉE dans la bibliothèque PERSONNELLE
      (contenu vectorisé + nom de fichier + type + dossier) -- TOUJOURS
      la première action à essayer dès qu'il y a un sujet, un nom, un
      type ou un dossier précis à chercher, avant même de songer à
      "lister" (qui est exhaustif/coûteux -- une recherche ciblée est
      largement moins volumineuse). Paramètres : `question` (sujet ou
      mot-clé -- peut rester vide si tu ne filtres que par
      `type_fichier`/`nom_dossier`), `type_fichier` (optionnel, ex.
      "pdf"/"image"/"audio"/"vidéo" -- à ne remplir QUE si l'étudiant
      mentionne clairement un type), `nom_dossier` (optionnel -- à ne
      remplir QUE si l'étudiant mentionne clairement un dossier).
    - "trouver_catalogue_public" : LOCALISE un document dans le
      CATALOGUE PUBLIC (section "Bibliothèque publique", ouvert à tout
      le monde). Renvoie
      uniquement le nom, la description et le lien de chaque document
      trouvé, JAMAIS son contenu : ne sert qu'à dire à l'utilisateur où
      trouver un document, jamais à citer ou paraphraser ce document
      dans ta réponse. Paramètre : `question`.
    - "lire_catalogue_public" : renvoie le texte intégral d'un document
      du catalogue public, identifié par le `fichier_id` obtenu via
      "trouver_catalogue_public". N'appelle cette action QUE si
      l'utilisateur demande explicitement à voir/lire ce document en
      entier -- jamais automatiquement après un "trouver_catalogue_
      public". Paramètre : `fichier_id`.
    - "lister_catalogue_public" : liste les documents les plus RÉCENTS
      du catalogue public, SANS recherche par contenu -- à utiliser pour
      une demande vague ("qu'est-ce qu'il y a dans la bibliothèque
      publique ?", "montre-moi le catalogue public") où
      "trouver_catalogue_public" ne renverrait rien faute de sujet
      précis à chercher. N'IMPORTE QUI peut ajouter un document au
      catalogue public : cette action n'est JAMAIS exhaustive, toujours
      plafonnée à 15 entrées PAR APPEL, les plus récentes -- si
      l'utilisateur cherche quelque chose de précis, utilise
      "trouver_catalogue_public" à la place. Le TOUT PREMIER appel de
      cette action dans une conversation doit être précédé d'une
      confirmation en langage naturel à l'étudiant (ex. "veux-tu que je
      te montre les documents récents du catalogue public ?"), sauf s'il
      a déjà demandé explicitement de tout voir/lister -- les appels
      SUIVANTS (pagination via `decalage`, voir "lister" ci-dessous pour
      la logique, identique ici) n'ont pas besoin d'une nouvelle
      confirmation, ils font partie de la même demande déjà approuvée.
      Paramètre optionnel : `decalage`.
    - "lister" : liste les documents/liens/notes de la bibliothèque
      personnelle (avec le lien de chacun), sans recherche par contenu.
      Couvre TOUS les types, y compris image/audio/vidéo, contrairement
      à "chercher" (qui rate un fichier non-texte jamais vectorisé et
      non trouvable par nom/type/dossier) -- utilise "lister" en
      priorité pour "redonne-moi l'image/document que tu as générée"
      (tout ce que tu génères est automatiquement enregistré ici avec
      `description` = son nom). DANS TOUS LES AUTRES CAS : n'appelle
      "lister" (ici ou "lister_catalogue_public") QUE si "chercher" (ou
      "trouver_catalogue_public") n'a RIEN trouvé -- jamais comme
      substitut ou raccourci de recherche ; "lister" est plafonné à 15
      résultats PAR APPEL (comme "lister_catalogue_public") et coûte
      plus cher en volume qu'une recherche ciblée.
      Pagination : si les 15 premiers résultats (ou ceux de la page
      précédente) ne contiennent rien de pertinent ET que la réponse
      indique qu'il en reste d'autres, tu peux rappeler "lister"
      toi-même avec `decalage` augmenté de 15 (ex. decalage=15 puis
      30...) SANS redemander à l'étudiant -- c'est la suite de la même
      recherche. Arrête-toi dès que la réponse ne signale plus qu'il en
      reste ("c'est fini" veut vraiment dire fini : ne rappelle plus
      "lister" au-delà, dis simplement à l'étudiant que rien de
      pertinent n'a été trouvé). Paramètre optionnel : `decalage`
      (défaut 0).
    - "ajouter_lien" : ajoute un lien. Paramètres : `url`, `titre`.
    - "ajouter_texte" : ajoute une note de texte libre. Paramètres :
      `contenu`, `titre`.
    - "ajouter_fichier" : ajoute un fichier (PDF, image, audio ou vidéo).
      Paramètres : `nom_fichier`, `type_mime` (obligatoires, à déduire
      TOI-MÊME du contexte -- ne jamais les demander à l'utilisateur, ce
      sont des détails techniques qu'il n'a pas à connaître : reprends le
      nom donné entre crochets juste après un upload, ex. "[Document
      joint : cours_svt.pdf]", ou l'extension visible à la fin de
      `url_fichier` ; `type_mime` se déduit de cette même extension,
      mapping standard extension -> type MIME). `titre`/`description`
      optionnels. Fournir SOIT `url_fichier` (lien réel d'un fichier déjà
      joint dans CETTE conversation, entre crochets "[Lien réel du
      fichier : ...]" -- à privilégier systématiquement) SOIT
      `contenu_base64` (jamais les deux vides). SI AUCUN FICHIER N'A ÉTÉ
      JOINT dans cette conversation : n'appelle PAS cette action, dis
      simplement à l'utilisateur d'uploader/joindre le fichier (bouton
      trombone). Limite : 50 Mo.
    - "supprimer" : supprime DÉFINITIVEMENT un document/lien/note.
      Paramètre : `fichier_id`. SENSIBLE : demande toujours confirmation
      à l'utilisateur avant d'être exécuté, quelle que soit la
      formulation de sa demande.
    - "ranger_dossier" : range un fichier dans un dossier. Paramètres :
      `fichier_id`, `dossier_id`. Un fichier peut être rangé dans
      plusieurs dossiers à la fois.
    - "retirer_dossier" : retire un fichier d'un dossier précis (le
      fichier reste dans la bibliothèque et ses autres dossiers
      éventuels). Paramètres : `fichier_id`, `dossier_id`.
    - "lire_entier" : renvoie le texte intégral d'un document PDF/texte
      déjà indexé (obtiens `fichier_id` via "chercher" ou chercher_fichier).
      À utiliser quand les extraits de "chercher" ne suffisent pas. Ne
      fonctionne que pour les documents PDF/texte (images/audio/vidéo non
      vectorisés aujourd'hui -- utilise leur lien pour les afficher).
      Paramètre : `fichier_id`.

    Pour les dossiers eux-mêmes (créer/lister/renommer/supprimer un
    dossier), voir les outils dédiés (section "Dossiers de la
    bibliothèque personnelle" plus bas) -- non consolidés ici.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "chercher":
        # BUG corrigé le 14/08 (constaté en prod : "Rien de pertinent
        # trouvé" alors que la bibliothèque contenait bien des documents
        # indexés) -- user_id vient de ctx (authentifié), jamais d'un
        # paramètre que le modèle pourrait halluciner/inventer.
        try:
            resultats = _chercher_bibliotheque_combinee(
                question, user_id=user_id, type_fichier=type_fichier, nom_dossier=nom_dossier
            )
        except Exception:
            return "Erreur : la recherche dans la bibliothèque a échoué, réessaie."
        if not resultats:
            return "Rien de pertinent trouvé dans la bibliothèque pour cette question."
        blocs = []
        for r in resultats:
            bloc = r["contenu"]
            source = _formater_source_bibliotheque(r)
            if source:
                bloc += f"\n{source}"
            blocs.append(bloc)
        return "\n\n---\n\n".join(blocs)

    if action == "chercher_publique":
        # Fonctionnalité "Programme" (et ses plugins publics par niveau)
        # désactivée le 29/08/2026 (demande Bourama) -- voir
        # _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md.
        return "Erreur : la recherche dans les plugins publics n'est plus disponible."

    if action == "trouver_catalogue_public":
        try:
            resultats = _chercher_catalogue_public(question)
        except Exception:
            return "Erreur : la recherche dans le catalogue public a échoué, réessaie."
        if not resultats:
            return "Rien de pertinent trouvé dans le catalogue public pour cette question."
        lignes = []
        for r in resultats:
            ligne = f"- {r['nom']}"
            if r.get("description"):
                ligne += f" — {r['description']}"
            if r.get("url_publique"):
                ligne += f" ({r['url_publique']})"
            lignes.append(ligne)
        return "\n".join(lignes)

    if action == "lire_catalogue_public":
        texte = _lire_document_catalogue_public(fichier_id)
        if texte is None:
            return "Rien à lire pour ce document : soit il n'existe pas, soit son contenu n'a pas pu être vectorisé (vidéo, ou lien externe)."
        return texte

    if action == "lister_catalogue_public":
        decalage_val = max(0, decalage or 0)
        try:
            resultat = _lister_catalogue_public(decalage=decalage_val)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (lister_catalogue_public) : {e}")
            return "Erreur : impossible de lister le catalogue public, réessaie."
        documents = resultat["documents"]
        total = resultat["total"]
        if not documents:
            if decalage_val > 0:
                return "C'est fini : plus aucun document au-delà de ceux déjà vus dans le catalogue public."
            return "Le catalogue public est vide pour l'instant."
        lignes = []
        for r in documents:
            ligne = f"- {r['nom']}"
            if r.get("description"):
                ligne += f" — {r['description']}"
            if r.get("url_publique"):
                ligne += f" ({r['url_publique']})"
            lignes.append(ligne)
        entete = f"{len(documents)} document(s) (à partir du rang {decalage_val + 1}) du catalogue public"
        reste = total is not None and total > decalage_val + len(documents)
        if total is not None:
            entete += f" (sur {total} au total)"
        resultat_txt = entete + " :\n" + "\n".join(lignes)
        if reste:
            resultat_txt += (
                f"\n\n(Il en reste d'autres -- tu peux rappeler \"lister_catalogue_public\" avec "
                f"decalage={decalage_val + len(documents)} si rien de pertinent ici, sans redemander "
                f"confirmation à l'étudiant.)"
            )
        else:
            resultat_txt += "\n\n(C'est fini : plus rien au-delà de cette liste.)"
        return resultat_txt

    if action == "lister":
        decalage_val = max(0, decalage or 0)
        try:
            # CORRECTIF 02/09/2026 : origine="bibliotheque" -> exclut_origine=
            # "chat", pour que les nouvelles origines "publique"/"code_partage"/
            # "ia_generee" restent visibles ici (voir bibliotheque_fichiers.py).
            resultat = _lister_fichiers(
                "utilisateur", user_id=user_id, exclut_origine="chat", limite=15, decalage=decalage_val
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (lister) : {e}")
            return "Erreur : impossible de lister la bibliothèque, réessaie."
        fichiers = resultat["fichiers"]
        total = resultat["total"]
        if not fichiers:
            if decalage_val > 0:
                return "C'est fini : plus aucun fichier au-delà de ceux déjà vus dans la bibliothèque."
            return "Bibliothèque vide pour l'instant."
        lignes = []
        for f in fichiers:
            ligne = (
                f"- {f.get('description') or f.get('nom_fichier')} "
                f"({f.get('type_mime', 'inconnu')}, ajouté le {f.get('created_at', '?')})"
            )
            emplacements = _lister_emplacements_document(f["id"])
            if emplacements:
                ligne += " | classé dans : " + ", ".join(e["libelle"] for e in emplacements)
            ligne += f" [id: {f['id']}]"
            # 04/09/2026, demande Bourama : lien inclus directement ici
            # (déjà connu, remonté par le select("*") de _lister_fichiers)
            # pour que l'IA puisse le redonner sans appel d'outil séparé.
            if f.get("url_publique"):
                ligne += f" -- {f['url_publique']}"
            lignes.append(ligne)
        reste = total is not None and total > decalage_val + len(fichiers)
        resultat_txt = "\n".join(lignes)
        if reste:
            resultat_txt += (
                f"\n\n(Il en reste d'autres -- tu peux rappeler \"lister\" avec "
                f"decalage={decalage_val + len(fichiers)} si rien de pertinent ici, sans redemander "
                f"à l'étudiant.)"
            )
        else:
            resultat_txt += "\n\n(C'est fini : plus rien au-delà de cette liste.)"
        return resultat_txt

    if action == "ajouter_lien":
        url_val = (url or "").strip()
        if not url_val:
            return "Erreur : url manquante."
        titre_final = (titre or url_val).strip()
        try:
            ligne = _enregistrer_lien(
                url=url_val,
                nom_fichier=titre_final,
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=titre_final,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_lien) : {e}")
            return "Erreur : impossible d'enregistrer ce lien, réessaie."
        return f"Lien ajouté (id {ligne['id']})."

    if action == "ajouter_texte":
        contenu_val = (contenu or "").strip()
        if not contenu_val:
            return "Erreur : contenu vide."
        titre_val = (titre or "").strip()
        nom_fichier_note = f"{titre_val or 'Note'}.txt"
        description_note = titre_val or (contenu_val[:80] + ("…" if len(contenu_val) > 80 else ""))
        try:
            ligne = _enregistrer_fichier(
                contenu=contenu_val.encode("utf-8"),
                nom_fichier=nom_fichier_note,
                type_mime="text/plain",
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=description_note,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_texte) : {e}")
            return "Erreur : impossible d'enregistrer cette note, réessaie."
        try:
            _indexer_texte_bibliotheque(contenu_val, fichier_id=ligne["id"], user_id=user_id)
        except Exception as e:
            logging.error(f"ERREUR vectorisation gerer_document_bibliotheque (ajouter_texte) : {e}")
        return f"Note ajoutée (id {ligne['id']})."

    if action == "ajouter_fichier":
        type_mime_val = (type_mime or "").strip().lower()
        if not type_mime_val:
            return "Erreur : type de fichier manquant."
        url_fichier_val = (url_fichier or "").strip()
        contenu_base64_val = (contenu_base64 or "").strip()
        if not url_fichier_val and not contenu_base64_val:
            return "Erreur : fournis url_fichier (lien réel d'un fichier déjà joint dans la conversation) ou contenu_base64."
        if url_fichier_val:
            try:
                reponse = requests.get(url_fichier_val, timeout=30)
                reponse.raise_for_status()
                contenu_fichier = reponse.content
            except Exception as e:
                logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_fichier, url_fichier={url_fichier_val}) : {e}")
                return "Erreur : impossible de récupérer le fichier à cette URL."
        else:
            try:
                contenu_fichier = base64.b64decode(contenu_base64_val, validate=True)
            except Exception:
                return "Erreur : contenu_base64 invalide (doit être du base64 valide)."
        if len(contenu_fichier) == 0:
            return "Erreur : fichier vide."
        if len(contenu_fichier) > _TAILLE_MAX_OCTETS_BIBLIOTHEQUE:
            return "Erreur : fichier trop lourd (50 Mo max)."
        nom_original = (nom_fichier or "fichier").strip()
        titre_val = (titre or "").strip()
        description_val = (description or "").strip()
        description_finale = (
            f"{titre_val} — {description_val}" if titre_val and description_val
            else (description_val or titre_val or nom_original)
        )
        try:
            ligne = _enregistrer_fichier(
                contenu=contenu_fichier,
                nom_fichier=nom_original,
                type_mime=type_mime_val,
                niveau="utilisateur",
                uploade_par=user_id,
                user_id=user_id,
                description=description_finale,
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ajouter_fichier) : {e}")
            return "Erreur : impossible d'enregistrer ce fichier, réessaie."
        if type_mime_val == "application/pdf":
            chemin_temp = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(contenu_fichier)
                    chemin_temp = tmp.name
                _indexer_pdf_bibliotheque(chemin_temp, fichier_id=ligne["id"], user_id=user_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation gerer_document_bibliotheque (ajouter_fichier, fichier_id={ligne['id']}) : {e}")
            finally:
                if chemin_temp:
                    try:
                        os.remove(chemin_temp)
                    except OSError:
                        pass
        elif type_mime_val.startswith("image/"):
            try:
                description_image = _decrire_image_bibliotheque(contenu_fichier, type_mime_val)
                if description_image:
                    _indexer_texte_bibliotheque(description_image, fichier_id=ligne["id"], user_id=user_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation image gerer_document_bibliotheque (ajouter_fichier, fichier_id={ligne['id']}) : {e}")
        elif type_mime_val.startswith("audio/"):
            try:
                segments_audio = _transcrire_audio_bibliotheque(contenu_fichier, nom_original)
                if segments_audio:
                    _indexer_transcription_bibliotheque(segments_audio, fichier_id=ligne["id"], user_id=user_id)
            except Exception as e:
                logging.error(f"ERREUR vectorisation audio gerer_document_bibliotheque (ajouter_fichier, fichier_id={ligne['id']}) : {e}")
        message = f"Fichier ajouté (id {ligne['id']})."
        # Classement dans le programme désactivé le 29/08/2026 (demande
        # Bourama, voir _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md).
        if type_emplacement and emplacement_id:
            message += " Attention : le classement dans le programme n'est plus disponible."
        return message

    if action == "supprimer":
        try:
            res = (
                _supabase_memoire.table("fichiers_uploades")
                .select("user_id")
                .eq("id", fichier_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (supprimer, lecture) : {e}")
            return "Erreur : impossible de supprimer ce document, réessaie."
        if not res or not res.data:
            return "Ce document est introuvable."
        if res.data["user_id"] != user_id:
            return "Ce document ne t'appartient pas."
        try:
            _supprimer_fichier(fichier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (supprimer, suppression) : {e}")
            return "Erreur : impossible de supprimer ce document, réessaie."
        return "Document supprimé."

    if action == "classer":
        # Désactivé le 29/08/2026 (demande Bourama, fonctionnalité "Programme"
        # isolée) -- voir _desactive_programme/LISEZ_MOI_NE_JAMAIS_REUTILISER.md.
        return "Erreur : le classement dans le programme n'est plus disponible."

    if action == "declasser":
        return "Erreur : le classement dans le programme n'est plus disponible."

    if action == "ranger_dossier":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            res = _supabase_memoire.table("fichiers_uploades").select("user_id").eq("id", fichier_id).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ranger_dossier, lecture fichier) : {e}")
            return "Erreur : impossible de ranger ce fichier, réessaie."
        if not res or not res.data:
            return "Ce fichier est introuvable."
        if res.data["user_id"] != user_id:
            return "Ce fichier ne t'appartient pas."
        try:
            _ranger_fichier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (ranger_dossier) : {e}")
            return "Erreur : impossible de ranger ce fichier, réessaie."
        return "Fichier rangé dans le dossier."

    if action == "retirer_dossier":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            _retirer_fichier(fichier_id, dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_document_bibliotheque (retirer_dossier) : {e}")
            return "Erreur : impossible de retirer ce fichier, réessaie."
        return "Fichier retiré du dossier."

    if action == "lire_entier":
        texte = _lire_document_bibliotheque_en_entier(fichier_id, user_id=user_id)
        if texte is None:
            return "Rien à lire pour ce fichier : soit il n'existe pas ou ne t'appartient pas, soit ce n'est pas un PDF/texte indexé."
        return texte

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : chercher, "
        "trouver_catalogue_public, lire_catalogue_public, lister_catalogue_public, lister, "
        "ajouter_lien, ajouter_texte, ajouter_fichier, supprimer, "
        "ranger_dossier, retirer_dossier, lire_entier."
    )


# --- Dossiers de la bibliothèque personnelle (22/08, demande Bourama,
# parité avec serveur_mcp_espace.py) : voir core/dossiers_bibliotheque.py
# pour la logique complète. Séparé du classement dans le programme
# ci-dessus. Un fichier peut être dans plusieurs dossiers à la fois.

@mcp_generation.tool()
def gerer_dossier_bibliotheque(
    action: str,
    ctx: Context,
    dossier_id: str = "",
    nom: str = "",
    dossier_parent_id: str = "",
    nouveau_nom: str = "",
) -> str:
    """
    Gère les dossiers de la bibliothèque personnelle de CET utilisateur
    (organisation des documents/liens/notes, distincte du classement
    dans le programme -- voir gerer_document_bibliotheque pour ça) --
    consolidé le 26/08, un seul outil, plusieurs actions (pattern
    "action + paramètres", même logique que gerer_document_bibliotheque).

    `action` doit être l'une de :
    - "lister" : liste tous les dossiers avec leur arborescence (dossier
      parent) et le nombre de fichiers directement rangés dans chacun.
      Aucun paramètre.
    - "consulter" : liste le contenu direct d'un dossier précis (ses
      sous-dossiers et ses fichiers). Ne descend pas récursivement dans
      les sous-dossiers, rappelle avec l'id d'un sous-dossier pour y
      entrer. Paramètre : `dossier_id`.
    - "creer" : crée un dossier. Paramètres : `nom` ; `dossier_parent_id`
      optionnel (id d'un dossier existant pour créer un SOUS-dossier
      dedans, laisse vide pour un dossier à la racine).
    - "renommer" : renomme un dossier existant. Paramètres : `dossier_id`,
      `nouveau_nom`.
    - "supprimer" : supprime DÉFINITIVEMENT un dossier (et ses
      sous-dossiers). Un fichier encore rattaché à au moins un autre
      dossier est conservé (juste détaché de celui-ci) ; un fichier qui
      n'était rattaché à AUCUN autre dossier est supprimé en même temps
      que le dossier. Paramètre : `dossier_id`. SENSIBLE : demande
      toujours confirmation à l'utilisateur avant d'être exécuté, quelle
      que soit la formulation de sa demande.

    Pour ranger/retirer un fichier DANS un dossier, ou pour les documents
    eux-mêmes (chercher/lister/ajouter/supprimer/classer), voir l'outil
    dédié gerer_document_bibliotheque.
    """
    user_id = ctx.request_context.request.query_params.get("user_id")
    if not user_id:
        return "Erreur : utilisateur non authentifié."

    if action == "lister":
        try:
            dossiers = _lister_dossiers(user_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (lister) : {e}")
            return "Erreur : impossible de lister les dossiers, réessaie."
        if not dossiers:
            return "Aucun dossier pour l'instant."
        par_id = {d["id"]: d for d in dossiers}
        lignes = []
        for d in dossiers:
            parent = par_id.get(d["dossier_parent_id"])
            chemin = f"{parent['nom']} > {d['nom']}" if parent else d["nom"]
            nb_fichiers = len(_lister_fichiers_ids_dossier(d["id"]))
            lignes.append(f"- {chemin} [id: {d['id']}] ({nb_fichiers} fichier(s) direct(s))")
        return "\n".join(lignes)

    if action == "consulter":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            dossiers = _lister_dossiers(user_id)
            sous_dossiers = [d for d in dossiers if d["dossier_parent_id"] == dossier_id]
            fichier_ids = _lister_fichiers_ids_dossier(dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (consulter) : {e}")
            return "Erreur : impossible de consulter ce dossier, réessaie."

        lignes = []
        for sd in sous_dossiers:
            lignes.append(f"- [dossier] {sd['nom']} [id: {sd['id']}]")
        for f_id in fichier_ids:
            try:
                res = _supabase_memoire.table("fichiers_uploades").select("nom_fichier, description, type_mime").eq("id", f_id).maybe_single().execute()
            except Exception as e:
                logging.error(f"ERREUR gerer_dossier_bibliotheque (consulter, lecture fichier {f_id}) : {e}")
                continue
            if not res or not res.data:
                continue
            f = res.data
            lignes.append(f"- [fichier] {f.get('description') or f.get('nom_fichier')} ({f.get('type_mime', 'inconnu')}) [id: {f_id}]")
        if not lignes:
            return "Ce dossier est vide."
        return "\n".join(lignes)

    if action == "creer":
        nom_val = (nom or "").strip()
        if not nom_val:
            return "Erreur : nom de dossier manquant."
        parent_id = (dossier_parent_id or "").strip() or None
        if parent_id:
            proprietaire = _proprietaire_dossier(parent_id)
            if proprietaire is None:
                return "Erreur : le dossier parent indiqué est introuvable."
            if proprietaire != user_id:
                return "Erreur : ce dossier parent ne t'appartient pas."
        try:
            dossier = _creer_dossier(user_id, nom_val, parent_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (creer) : {e}")
            return "Erreur : impossible de créer ce dossier, réessaie."
        return f"Dossier « {nom_val} » créé [id: {dossier['id']}]."

    if action == "renommer":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        nouveau_nom_val = (nouveau_nom or "").strip()
        if not nouveau_nom_val:
            return "Erreur : nouveau nom manquant."
        try:
            _renommer_dossier(dossier_id, nouveau_nom_val)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (renommer) : {e}")
            return "Erreur : impossible de renommer ce dossier, réessaie."
        return f"Dossier renommé en « {nouveau_nom_val} »."

    if action == "supprimer":
        proprietaire = _proprietaire_dossier(dossier_id)
        if proprietaire is None:
            return "Ce dossier est introuvable."
        if proprietaire != user_id:
            return "Ce dossier ne t'appartient pas."
        try:
            _supprimer_dossier(dossier_id)
        except Exception as e:
            logging.error(f"ERREUR gerer_dossier_bibliotheque (supprimer) : {e}")
            return "Erreur : impossible de supprimer ce dossier, réessaie."
        return "Dossier supprimé."

    return (
        f"Erreur : action '{action}' inconnue. Actions valides : lister, "
        "consulter, creer, renommer, supprimer."
    )
