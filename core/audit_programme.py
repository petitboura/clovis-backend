"""
Moteur des audits IA (26/08/2026, chantier "Audits" -- récap complet posé
par Bourama). Trois audits distincts, un par niveau de programme :
chapitre, matière, programme. Chaque audit fouille le contenu RÉEL
rattaché à son niveau et produit un texte qui décrit le périmètre réel de
ce niveau -- pas une comparaison à un cadre déclaré, un texte généré à
partir de ce qui existe vraiment (documents, exercices, examens).

Cascade à deux dimensions :
- Entre niveaux : le programme se base sur les textes déjà produits par
  ses matières, la matière sur les textes déjà produits par ses
  chapitres. Seul le chapitre lit le contenu brut (voir
  _contenu_brut_chapitre).
- À l'intérieur d'un niveau (chapitre) : si trop de documents pour un
  seul appel IA, découpage en lots résumés séparément (voir
  MAX_CARACTERES_LOT), puis fusion des résumés de lots.

Réaudit incrémental (boucle du lundi, voir api/main.py) : rien n'est
refait depuis zéro. Chaque lot de contenu brut est haché (sha256) ; un
lot dont le hash n'a pas changé depuis le dernier passage garde son
résumé tel quel, seule la fusion finale est refaite s'il y a eu au moins
un changement. Même logique en cascade : une matière ne retraite pas un
chapitre inchangé (hash_source comparé), un programme ne retraite pas une
matière inchangée.

Le texte de périmètre produit est transformé en skill et attaché au
chapitre/matière/programme concerné -- exactement comme un skill
s'attache déjà normalement (voir core/comportements_etudiants.py,
_generer_skill), rien à réinventer de ce côté. Le booléen
`depuis_audit` (voir migration 2026_08_26) distingue ce skill-là d'un
comportement écrit par l'étudiant/l'IA : lui seul est réécrit en place
chaque lundi.
"""

import hashlib
import logging

from groq import Groq

from core.comportements_etudiants import MODELE_SKILL, _generer_skill, get_secret, supabase

logging.basicConfig(level=logging.INFO)

AGENT_ID = "clovis"  # même constante que le frontend (SectionComportementsEmplacement.tsx)

# Taille max approximative (en caractères) de contenu brut par lot avant
# un appel LLM de résumé -- volontairement prudent (grande marge sous la
# fenêtre de contexte du modèle) puisqu'un chapitre peut accumuler
# beaucoup de documents/exercices au fil du temps.
MAX_CARACTERES_LOT = 12000


def _hash(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def _appeler_llm(prompt: str, max_tokens: int) -> str | None:
    """Même client/modèle "costaud" que la génération de skill
    (MODELE_SKILL) -- un texte de périmètre lu par l'étudiant et injecté
    comme skill mérite la même qualité qu'un skill normal, pas de raison
    de descendre à MODELE_PETIT ici."""
    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=30.0)
        completion = client.chat.completions.create(
            model=MODELE_SKILL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=max_tokens,
            timeout=30.0,
        )
        return (completion.choices[0].message.content or "").strip() or None
    except Exception as e:
        logging.error(f"ERREUR LLM audit : {e}")
        return None


# ---------------------------------------------------------------------------
# Niveau chapitre -- seul niveau qui lit le contenu brut
# ---------------------------------------------------------------------------


def _contenu_brut_chapitre(chapitre_id: str) -> list[str]:
    """Un item texte par élément de contenu rattaché à ce chapitre --
    chacun sera haché individuellement pour le réaudit incrémental par
    lot. Ordre stable (id croissant) pour que le regroupement en lots
    soit déterministe d'un passage à l'autre."""
    items: list[str] = []

    docs = (
        supabase.table("documents_programme")
        .select("id, titre, url_ou_contenu")
        .eq("chapitre_id", chapitre_id)
        .order("id")
        .execute()
        .data
        or []
    )
    for d in docs:
        items.append(f"[document #{d['id']}] {d['titre']} :: {(d.get('url_ou_contenu') or '')[:2000]}")

    exercices = (
        supabase.table("exercices_programme")
        .select("id, enonce")
        .eq("chapitre_id", chapitre_id)
        .order("id")
        .execute()
        .data
        or []
    )
    for e in exercices:
        items.append(f"[exercice #{e['id']}] {(e.get('enonce') or '')[:2000]}")

    liens_examens = (
        supabase.table("examen_chapitres").select("examen_id").eq("chapitre_id", chapitre_id).execute().data or []
    )
    examen_ids = [l["examen_id"] for l in liens_examens]
    if examen_ids:
        examens = (
            supabase.table("examens_programme").select("id, titre, type").in_("id", examen_ids).order("id").execute().data
            or []
        )
        for ex in examens:
            items.append(f"[examen #{ex['id']}] {ex['titre']} ({ex['type']})")

    emplacements = (
        supabase.table("bibliotheque_emplacements_programme")
        .select("fichier_id")
        .eq("type_cible", "chapitre")
        .eq("cible_id", chapitre_id)
        .order("fichier_id")
        .execute()
        .data
        or []
    )
    fichier_ids = [e["fichier_id"] for e in emplacements]
    if fichier_ids:
        fichiers = (
            supabase.table("fichiers_uploades")
            .select("id, nom_fichier, description")
            .in_("id", fichier_ids)
            .order("id")
            .execute()
            .data
            or []
        )
        for f in fichiers:
            items.append(f"[bibliothèque #{f['id']}] {f['nom_fichier']} :: {(f.get('description') or '')[:2000]}")

    return items


def _regrouper_en_lots(items: list[str]) -> list[str]:
    """Regroupe les items de contenu brut en lots de MAX_CARACTERES_LOT
    caractères max, dans l'ordre -- un item plus gros que la limite forme
    son propre lot plutôt que d'être tronqué silencieusement."""
    lots: list[str] = []
    lot_courant: list[str] = []
    taille_courante = 0
    for item in items:
        if taille_courante + len(item) > MAX_CARACTERES_LOT and lot_courant:
            lots.append("\n\n".join(lot_courant))
            lot_courant = []
            taille_courante = 0
        lot_courant.append(item)
        taille_courante += len(item)
    if lot_courant:
        lots.append("\n\n".join(lot_courant))
    return lots


def _resumer_lot(chapitre_nom: str, lot_texte: str) -> str:
    prompt = (
        "Tu es en train d'auditer un chapitre de programme scolaire. Voici un "
        f"lot de contenu réel rattaché au chapitre \"{chapitre_nom}\" (documents, "
        "exercices, examens). Résume factuellement ce que ce lot contient "
        "réellement -- notions couvertes, niveau de difficulté, type de "
        "contenu -- en 3 à 6 phrases, en français, sans jugement de valeur ni "
        "comparaison à un programme officiel.\n\n"
        f"{lot_texte}"
    )
    resume = _appeler_llm(prompt, max_tokens=400)
    return resume or "(résumé indisponible -- erreur lors de la génération)"


def _fusionner_resumes_chapitre(chapitre_nom: str, resumes: list[str]) -> str:
    if len(resumes) == 1:
        corps = resumes[0]
    else:
        corps = "\n\n".join(f"- {r}" for r in resumes)
    prompt = (
        f"Voici les résumés du contenu réel rattaché au chapitre \"{chapitre_nom}\" "
        "d'un programme scolaire (chacun couvre une partie du contenu). "
        "Fusionne-les en UN texte cohérent qui décrit le périmètre réel de ce "
        "chapitre tel qu'il existe aujourd'hui -- notions couvertes, niveau, "
        "types de contenu disponibles. 4 à 8 phrases, en français, sans "
        "doublons ni transitions du type \"le premier résumé dit...\".\n\n"
        f"{corps}"
    )
    texte = _appeler_llm(prompt, max_tokens=500)
    return texte or corps


def auditer_chapitre(chapitre_id: str, proprietaire_id: str, forcer: bool = False) -> tuple[str, bool]:
    """Retourne (texte, a_change). a_change=False signifie que rien n'a
    bougé depuis le dernier passage -- la matière parente peut donc
    sauter ce chapitre dans son propre réaudit (cascade incrémentale)."""
    chapitre_res = supabase.table("chapitres").select("nom").eq("id", chapitre_id).maybe_single().execute()
    chapitre_nom = (chapitre_res.data or {}).get("nom", "Chapitre") if chapitre_res else "Chapitre"

    items = _contenu_brut_chapitre(chapitre_id)
    lots_texte = _regrouper_en_lots(items)
    hash_lots = [_hash(t) for t in lots_texte]
    hash_contenu_global = _hash("|".join(hash_lots))

    existant = supabase.table("audits_chapitre").select("*").eq("chapitre_id", chapitre_id).maybe_single().execute()
    existant_data = existant.data if existant else None

    if not forcer and existant_data and existant_data.get("hash_contenu") == hash_contenu_global:
        # Rien n'a changé du tout pour ce chapitre depuis le dernier passage.
        return existant_data.get("texte") or "", False

    anciens_lots = {l["hash"]: l["resume"] for l in (existant_data.get("lots") if existant_data else []) or []}

    if not items:
        texte = f"Aucun contenu rattaché au chapitre \"{chapitre_nom}\" pour le moment."
        nouveaux_lots: list[dict] = []
    else:
        nouveaux_lots = []
        resumes: list[str] = []
        for h, t in zip(hash_lots, lots_texte):
            resume = anciens_lots.get(h) if not forcer else None
            if resume is None:
                resume = _resumer_lot(chapitre_nom, t)
            nouveaux_lots.append({"hash": h, "resume": resume})
            resumes.append(resume)
        texte = _fusionner_resumes_chapitre(chapitre_nom, resumes)

    supabase.table("audits_chapitre").upsert(
        {
            "chapitre_id": chapitre_id,
            "proprietaire_id": proprietaire_id,
            "texte": texte,
            "lots": nouveaux_lots,
            "hash_contenu": hash_contenu_global,
            "derniere_execution": "now()",
            "updated_at": "now()",
        },
        on_conflict="chapitre_id",
    ).execute()

    _synchroniser_skill_audit(proprietaire_id, "chapitre", chapitre_id, texte, f"Périmètre réel -- {chapitre_nom}")
    return texte, True


# ---------------------------------------------------------------------------
# Niveau matière -- se base sur les textes déjà produits par ses chapitres
# ---------------------------------------------------------------------------


def auditer_matiere(matiere_id: str, proprietaire_id: str, forcer: bool = False) -> tuple[str, bool]:
    matiere_res = supabase.table("matieres").select("nom").eq("id", matiere_id).maybe_single().execute()
    matiere_nom = (matiere_res.data or {}).get("nom", "Matière") if matiere_res else "Matière"

    chapitres = (
        supabase.table("chapitres").select("id, nom").eq("matiere_id", matiere_id).order("ordre").execute().data or []
    )
    chapitre_ids = [c["id"] for c in chapitres]
    audits_chapitres = (
        supabase.table("audits_chapitre").select("chapitre_id, texte").in_("chapitre_id", chapitre_ids).execute().data
        if chapitre_ids
        else []
    ) or []
    textes_par_chapitre = {a["chapitre_id"]: a["texte"] for a in audits_chapitres}

    hash_source = _hash("|".join(f"{c['id']}:{textes_par_chapitre.get(c['id'], '')}" for c in chapitres))

    existant = supabase.table("audits_matiere").select("*").eq("matiere_id", matiere_id).maybe_single().execute()
    existant_data = existant.data if existant else None

    if not forcer and existant_data and existant_data.get("hash_source") == hash_source:
        return existant_data.get("texte") or "", False

    if not chapitres:
        texte = f"Aucun chapitre dans la matière \"{matiere_nom}\" pour le moment."
    else:
        corps = "\n\n".join(f"- {c['nom']} : {textes_par_chapitre.get(c['id']) or '(pas encore audité)'}" for c in chapitres)
        prompt = (
            f"Voici les périmètres réels déjà établis pour chaque chapitre de la "
            f"matière \"{matiere_nom}\" d'un programme scolaire. Fusionne-les en UN "
            "texte cohérent qui décrit le périmètre réel de la matière entière -- "
            "grands axes couverts, niveau global, ce qui est solide ou encore "
            "clairsemé. 5 à 10 phrases, en français.\n\n"
            f"{corps}"
        )
        texte = _appeler_llm(prompt, max_tokens=600) or corps

    supabase.table("audits_matiere").upsert(
        {
            "matiere_id": matiere_id,
            "proprietaire_id": proprietaire_id,
            "texte": texte,
            "hash_source": hash_source,
            "derniere_execution": "now()",
            "updated_at": "now()",
        },
        on_conflict="matiere_id",
    ).execute()

    _synchroniser_skill_audit(proprietaire_id, "matiere", matiere_id, texte, f"Périmètre réel -- {matiere_nom}")
    return texte, True


# ---------------------------------------------------------------------------
# Niveau programme -- se base sur les textes déjà produits par ses matières
# ---------------------------------------------------------------------------


def auditer_programme(programme_id: str, proprietaire_id: str, forcer: bool = False) -> tuple[str, bool]:
    programme_res = supabase.table("programmes").select("nom, niveau").eq("id", programme_id).maybe_single().execute()
    programme_data = programme_res.data or {} if programme_res else {}
    programme_nom = programme_data.get("nom") or programme_data.get("niveau") or "Programme"

    matieres = (
        supabase.table("matieres").select("id, nom").eq("programme_id", programme_id).order("created_at").execute().data
        or []
    )
    matiere_ids = [m["id"] for m in matieres]
    audits_matieres = (
        supabase.table("audits_matiere").select("matiere_id, texte").in_("matiere_id", matiere_ids).execute().data
        if matiere_ids
        else []
    ) or []
    textes_par_matiere = {a["matiere_id"]: a["texte"] for a in audits_matieres}

    hash_source = _hash("|".join(f"{m['id']}:{textes_par_matiere.get(m['id'], '')}" for m in matieres))

    existant = supabase.table("audits_programme").select("*").eq("programme_id", programme_id).maybe_single().execute()
    existant_data = existant.data if existant else None

    if not forcer and existant_data and existant_data.get("hash_source") == hash_source:
        return existant_data.get("texte") or "", False

    if not matieres:
        texte = f"Aucune matière dans le programme \"{programme_nom}\" pour le moment."
    else:
        corps = "\n\n".join(f"- {m['nom']} : {textes_par_matiere.get(m['id']) or '(pas encore auditée)'}" for m in matieres)
        prompt = (
            f"Voici les périmètres réels déjà établis pour chaque matière du "
            f"programme \"{programme_nom}\". Fusionne-les en UN texte cohérent qui "
            "décrit le périmètre réel du programme entier -- vue d'ensemble, "
            "équilibre entre matières, ce qui est solide ou encore clairsemé. 6 à "
            "12 phrases, en français.\n\n"
            f"{corps}"
        )
        texte = _appeler_llm(prompt, max_tokens=700) or corps

    supabase.table("audits_programme").upsert(
        {
            "programme_id": programme_id,
            "proprietaire_id": proprietaire_id,
            "texte": texte,
            "hash_source": hash_source,
            "derniere_execution": "now()",
            "updated_at": "now()",
        },
        on_conflict="programme_id",
    ).execute()

    _synchroniser_skill_audit(proprietaire_id, "programme", programme_id, texte, f"Périmètre réel -- {programme_nom}")
    return texte, True


# ---------------------------------------------------------------------------
# Synchronisation skill (réutilise _generer_skill de core/comportements_etudiants.py)
# ---------------------------------------------------------------------------


def _synchroniser_skill_audit(proprietaire_id: str, lien_type: str, lien_id: str, texte: str, nom_repli: str) -> None:
    """Upsert du skill d'audit pour cet emplacement -- un seul par
    (agent_id, etudiant_id, lien_type, lien_id, depuis_audit=true), voir
    l'index unique posé par la migration. Si le texte de périmètre n'a
    pas changé depuis le dernier skill généré, on ne refait pas l'appel
    LLM de _generer_skill (économie, comme pour les lots)."""
    if not texte.strip():
        return
    existant = (
        supabase.table("comportements_etudiants")
        .select("id, texte")
        .eq("agent_id", AGENT_ID)
        .eq("etudiant_id", proprietaire_id)
        .eq("lien_type", lien_type)
        .eq("lien_id", lien_id)
        .eq("depuis_audit", True)
        .maybe_single()
        .execute()
    )
    existant_data = existant.data if existant else None

    if existant_data and existant_data.get("texte") == texte:
        return  # texte de périmètre identique -- rien à régénérer

    skill = _generer_skill(texte)
    ligne = {
        "agent_id": AGENT_ID,
        "etudiant_id": proprietaire_id,
        "texte": texte,
        "description": skill["description"],
        "skill_md": skill["skill_md"],
        "nom": skill["nom"] or nom_repli,
        "lien_type": lien_type,
        "lien_id": lien_id,
        "depuis_audit": True,
    }
    if existant_data:
        supabase.table("comportements_etudiants").update(ligne).eq("id", existant_data["id"]).execute()
    else:
        supabase.table("comportements_etudiants").insert(ligne).execute()


# ---------------------------------------------------------------------------
# Orchestrateur -- appelé chaque lundi (voir api/main.py) et par le
# déclenchement manuel (voir api/audits_programme.py)
# ---------------------------------------------------------------------------


def auditer_programme_complet(programme_id: str, proprietaire_id: str, forcer: bool = False) -> None:
    """Cascade complète pour UN programme : tous ses chapitres, puis
    toutes ses matières, puis le programme lui-même -- dans cet ordre,
    seul ordre qui respecte la cascade décrite en tête de fichier."""
    matieres = supabase.table("matieres").select("id").eq("programme_id", programme_id).execute().data or []
    for matiere in matieres:
        chapitres = supabase.table("chapitres").select("id").eq("matiere_id", matiere["id"]).execute().data or []
        for chapitre in chapitres:
            try:
                auditer_chapitre(chapitre["id"], proprietaire_id, forcer=forcer)
            except Exception as e:
                logging.error(f"ERREUR audit chapitre {chapitre['id']} : {e}")
        try:
            auditer_matiere(matiere["id"], proprietaire_id, forcer=forcer)
        except Exception as e:
            logging.error(f"ERREUR audit matière {matiere['id']} : {e}")
    try:
        auditer_programme(programme_id, proprietaire_id, forcer=forcer)
    except Exception as e:
        logging.error(f"ERREUR audit programme {programme_id} : {e}")


def executer_audits_hebdomadaires() -> int:
    """Boucle du lundi (voir _boucle_planificateur_audits dans
    api/main.py) : cascade complète pour TOUS les programmes de TOUS les
    utilisateurs. forcer=False partout -- l'incrémental fait le tri."""
    programmes = supabase.table("programmes").select("id, proprietaire_id").execute().data or []
    for programme in programmes:
        auditer_programme_complet(programme["id"], programme["proprietaire_id"], forcer=False)
    return len(programmes)
