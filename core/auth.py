"""
Authentification utilisateur (email/mot de passe + Google), via Supabase Auth.

STATUT (25/07/2026) : ce fichier n'est actuellement importe nulle part
dans le depot (ni api/, ni core/, ni connexions/). La connexion
email/mot de passe reelle se fait aujourd'hui cote frontend Next.js,
directement contre Supabase (voir djiguigne-frontend/lib/authFallback.ts),
sans passer par ce module. Garde intentionnellement : "Se connecter
avec Google" doit etre remis en place plus tard (Bourama). Pour
l'activer, il faudra des identifiants OAuth Google (Client ID + Secret,
Google Cloud Console) a renseigner dans Supabase -> Authentication ->
Providers -> Google -- rien a faire cote Railway pour ca.

A noter avant de reutiliser ce module tel quel : le flux PKCE fait a la
main ici (voir NOTE TECHNIQUE plus bas) contournait une limite propre a
Streamlit (un seul process partage par plusieurs utilisateurs a la fois).
Cote Next.js, chaque utilisateur a deja sa propre session navigateur,
donc l'appel client standard supabase.auth.signInWithOAuth({provider:
"google"}) depuis le frontend suffirait probablement, sans ce detour
Python -- a evaluer le moment venu plutot que de reactiver ce fichier
par defaut.

Le compte reste OPTIONNEL : le chat fonctionne sans connexion. On ne pousse
l'utilisateur a se connecter que quand une fonctionnalite en a vraiment besoin
(ex: connecter son Notion plus tard).

POURQUOI UNE TABLE oauth_temp POUR GOOGLE :
Quand l'utilisateur clique "Se connecter avec Google", il quitte entierement
l'app pour aller sur Google, puis revient. Ce depart/retour redemarre la
session Streamlit a zero (nouvelle session_state vide). Le "code_verifier"
PKCE genere avant le depart doit donc etre range quelque part qui survit a
ce redemarrage : la table oauth_temp (une ligne par tentative en cours,
supprimee juste apres usage).

NOTE TECHNIQUE : on construit nous-memes l'URL d'autorisation Google avec
les fonctions PKCE publiques de supabase-auth (generate_pkce_verifier,
generate_pkce_challenge) plutot que d'utiliser sign_in_with_oauth() de la
librairie. Raison : cette derniere stocke le code_verifier dans un espace
memoire interne au client, pense pour UN SEUL utilisateur a la fois (ex:
une appli mobile) - pas adapte a un backend qui sert plusieurs utilisateurs
en meme temps sur la meme instance.
"""

import os
import secrets
import logging
from urllib.parse import urlencode

from supabase import create_client
from supabase_auth.helpers import generate_pkce_verifier, generate_pkce_challenge


def get_secret(key):
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
URL_RETOUR = get_secret("URL_RETOUR_APP")  # ex: https://telecom-ia.streamlit.app

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error("SUPABASE_URL ou SUPABASE_SECRET manquant : l'authentification ne fonctionnera pas.")

if not URL_RETOUR:
    logging.warning(
        "URL_RETOUR_APP manquant dans les secrets : la connexion Google ne saura pas ou revenir. "
        "Ajoute par exemple URL_RETOUR_APP = \"https://tonapp.streamlit.app\""
    )

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def inscription(email, mot_de_passe, redirection=None):
    """
    Cree un compte utilisateur par email/mot de passe.
    Retourne (succes: bool, resultat).

    `redirection` (optionnel) : URL vers laquelle Supabase renverra la
    personne APRES qu'elle ait clique sur le lien de confirmation recu par
    email (paypal-like "email_redirect_to"). Sans ce parametre, Supabase
    utilise la "Site URL" par defaut configuree dans le projet (Authentication
    > URL Configuration), qui est UNIQUE pour tout le projet -> impossible de
    distinguer un createur (doit revenir sur la plateforme) d'un utilisateur
    utilisant un agent precis (doit revenir sur CET agent, ?agent=xxx).
    D'ou l'interet de le preciser explicitement a chaque appel :
    - depuis l'ancien formulaire Streamlit / mes_agents.py (createur) :
      l'URL de base de la plateforme.
    - depuis l'ancienne interface Streamlit (utilisateur) : l'URL de base + "?agent=<id de
      cet agent>", pour qu'il retombe directement sur le bon chat.

    `resultat` est soit :
    - une session Supabase valide (objet avec .user, .access_token...) si
      la confirmation par email est desactivee sur ce projet -> sign_up()
      connecte alors directement le compte, pas besoin de repasser par
      connexion() juste apres (evite d'obliger l'utilisateur a se
      reconnecter une deuxieme fois immediatement apres son inscription).
    - une chaine de message (str) si une confirmation par email est
      requise -> aucune session n'existe encore, l'appelant doit afficher
      le message tel quel.
    """
    try:
        payload = {"email": email, "password": mot_de_passe}
        if redirection:
            payload["options"] = {"email_redirect_to": redirection}
        resultat = supabase.auth.sign_up(payload)
        if resultat.session:
            return True, resultat.session
        return True, "Compte cree. Verifie ta boite mail si une confirmation est demandee."
    except Exception as e:
        logging.error(f"ERREUR INSCRIPTION ({email}): {e}")
        return False, "Impossible de creer ce compte (email deja utilise ou mot de passe trop court)."


def connexion(email, mot_de_passe):
    """
    Connecte un utilisateur deja inscrit par email/mot de passe.
    Retourne (succes: bool, session_ou_message).
    En cas de succes, le deuxieme element est la session Supabase
    (contient session.user.id, session.access_token, etc.).
    """
    try:
        resultat = supabase.auth.sign_in_with_password({"email": email, "password": mot_de_passe})
        return True, resultat.session
    except Exception as e:
        logging.error(f"ERREUR CONNEXION ({email}): {e}")
        return False, "Email ou mot de passe incorrect."


def connexion_depuis_jetons(access_token, refresh_token):
    """
    Ajoute le 2026-07-12 (Bourama : "des que tu crees un compte a la
    plateforme, tu es automatiquement connecte a tous les agents dans la
    plateforme, sans exception"). Le compte est deja unifie cote base de
    donnees (comme pour Notion, une connexion vaut pour tous les agents) --
    ce qui manquait, c'etait un pont technique entre la session Supabase
    de la plateforme Next.js (autre origine, autre stockage de session) et
    chat.py, qui n'a aucun moyen natif de la voir.

    Le pont : components/BoutonUtiliser.tsx transmet access_token et
    refresh_token de la session Next.js en cours dans l'URL qui ouvre le
    chat (si la personne est deja connectee sur la plateforme). Cette
    fonction les echange contre une session Supabase valide ICI, cote
    Streamlit -- sans redemander ni email ni mot de passe, quel que soit
    l'agent ouvert.

    Retourne (succes: bool, session_ou_message), meme convention que
    connexion() ci-dessus.
    """
    try:
        resultat = supabase.auth.set_session(access_token, refresh_token)
        return True, resultat.session
    except Exception as e:
        logging.error(f"ERREUR CONNEXION (depuis jetons plateforme) : {e}")
        return False, "Session plateforme invalide ou expirée."


def demarrer_connexion_google():
    """
    Premiere etape de la connexion Google : genere l'URL vers laquelle
    rediriger l'utilisateur, et range le code_verifier dans oauth_temp sous
    une cle aleatoire (state) qu'on retrouvera au retour.

    Retourne l'URL a ouvrir (str), ou None si la config manque.
    """
    if not URL_RETOUR:
        logging.error("Connexion Google impossible : URL_RETOUR_APP manquant.")
        return None

    state = secrets.token_urlsafe(24)
    code_verifier = generate_pkce_verifier()
    code_challenge = generate_pkce_challenge(code_verifier)

    try:
        supabase.table("oauth_temp").insert({
            "state": state,
            "code_verifier": code_verifier,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE oauth_temp : {e}")
        return None

    params = {
        "provider": "google",
        "redirect_to": URL_RETOUR,
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
        # Notion/Supabase transmettent ce parametre tel quel dans l'URL de
        # retour : on s'en sert pour retrouver la bonne ligne oauth_temp.
        "state": state,
    }
    url_autorisation = f"{SUPABASE_URL}/auth/v1/authorize?{urlencode(params)}"
    return url_autorisation


def finaliser_connexion_google(code, state):
    """
    Deuxieme etape, appelee au retour de Google (l'app relit ?code=...&state=...
    dans l'URL et appelle cette fonction). Retrouve le code_verifier dans
    oauth_temp via state, termine l'echange, nettoie la table.

    Retourne (succes: bool, session_ou_message).
    """
    try:
        ligne = supabase.table("oauth_temp").select("code_verifier").eq("state", state).execute()
    except Exception as e:
        logging.error(f"ERREUR LECTURE oauth_temp : {e}")
        return False, "Connexion Google impossible (erreur interne)."

    if not ligne.data:
        return False, "Session de connexion expiree ou deja utilisee, reessaie."

    code_verifier = ligne.data[0]["code_verifier"]

    # Nettoyage immediat : une tentative = un usage, qu'elle reussisse ou non.
    try:
        supabase.table("oauth_temp").delete().eq("state", state).execute()
    except Exception as e:
        logging.warning(f"Nettoyage oauth_temp echoue (pas bloquant) : {e}")

    try:
        resultat = supabase.auth.exchange_code_for_session({
            "auth_code": code,
            "code_verifier": code_verifier,
        })
        return True, resultat.session
    except Exception as e:
        logging.error(f"ERREUR FINALISATION OAUTH GOOGLE : {e}")
        return False, "Connexion Google impossible (code invalide ou expire)."


def deconnexion():
    try:
        supabase.auth.sign_out()
    except Exception as e:
        logging.warning(f"ERREUR DECONNEXION (pas bloquant) : {e}")


# --- Mot de passe oublie ---------------------------------------------------
# Trois etapes :
#   1. demarrer_reinitialisation_mot_de_passe() : envoie l'email.
#   2. La personne clique le lien -> atterrit sur notre page avec
#      ?token_hash=xxx&type=recovery en query string NORMALE (pas un
#      fragment #... comme avant). Voir l'ancienne interface Streamlit :
#      le token n'est PAS consomme a la simple ouverture de la page, ce
#      qui est le point important. Avant, on utilisait le lien tout fait
#      de Supabase ({{ .ConfirmationURL }}), qui valide -et donc consomme-
#      le token des qu'une requete HTTP l'atteint. Or beaucoup de clients
#      email (Gmail en tete) "pre-visitent" automatiquement les liens d'un
#      email pour verifier qu'ils ne sont pas malveillants -> ce
#      pre-chargement grillait le token avant meme que la personne ne
#      clique elle-meme, d'ou l'erreur "otp_expired" systematique.
#      Le nouveau lien (template Supabase modifie, voir la personne pour
#      la config) ne fait QUE transporter le token jusqu'a notre page ;
#      c'est etablir_session_depuis_token_hash(), appelee seulement quand
#      la personne clique sur NOTRE bouton "Confirmer", qui le consomme
#      reellement -> un simple pre-chargement automatique par un client
#      email n'a plus aucun effet.
#   3. etablir_session_depuis_token_hash() + mettre_a_jour_mot_de_passe().

def demarrer_reinitialisation_mot_de_passe(email, redirection=None):
    """
    Envoie l'email de reinitialisation. Retourne TOUJOURS (True, message),
    meme si l'email n'existe pas en base : ne jamais reveler cote UI si un
    email est inscrit ou non (evite qu'un tiers teste des adresses au
    hasard pour decouvrir qui a un compte).

    `redirection` DOIT contenir au moins un parametre de query existant
    (donc deja un "?" dans l'URL, ex: ".../?agent=xxx" ou ".../?ctx=..."),
    pour que le template email puisse lui accoler "&token_hash=..." sans
    produire une URL invalide (deux "?"). Voir les appelants
    (l'ancien formulaire Streamlit, mes_agents.py, chat.py).
    """
    try:
        options = {"redirect_to": redirection} if redirection else {}
        supabase.auth.reset_password_for_email(email, options)
    except Exception as e:
        logging.error(f"ERREUR REINITIALISATION MOT DE PASSE ({email}) : {e}")
    return True, "Si un compte existe avec cet email, un lien de réinitialisation vient d'être envoyé."


def etablir_session_depuis_token_hash(token_hash, type_otp="recovery"):
    """
    Deuxieme etape : appelee uniquement quand la personne clique sur le
    bouton "Confirmer" (voir l'ancienne interface Streamlit) -> c'est ce
    clic explicite, et rien avant, qui consomme le token. Authentifie la
    session Supabase correspondante, prealable indispensable a
    mettre_a_jour_mot_de_passe() (on ne peut pas changer le mot de passe
    de "personne").
    """
    try:
        resultat = supabase.auth.verify_otp({"token_hash": token_hash, "type": type_otp})
        return True, resultat.session
    except Exception as e:
        logging.error(f"ERREUR VERIFICATION TOKEN (recuperation mdp) : {e}")
        return False, "Lien de réinitialisation invalide, expiré, ou déjà utilisé. Redemande-en un nouveau."


def mettre_a_jour_mot_de_passe(nouveau_mot_de_passe):
    """
    Derniere etape : change le mot de passe de la session actuellement
    authentifiee (voir etablir_session_depuis_token_hash juste au-dessus).
    """
    try:
        supabase.auth.update_user({"password": nouveau_mot_de_passe})
        return True, "Mot de passe mis à jour avec succès."
    except Exception as e:
        logging.error(f"ERREUR MISE A JOUR MOT DE PASSE : {e}")
        return False, "Impossible de mettre à jour le mot de passe (trop court, ou lien expiré)."
