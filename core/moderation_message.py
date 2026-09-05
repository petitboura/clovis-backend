# Extrait de main.py le 05/09/2026 (demande Bourama : diviser les fichiers
# trop longs). Verification du message brut de l'utilisateur avant le reste
# du traitement (voir MODERATION_ENTREE_ACTIVE dans constantes_agent.py --
# desactivee au 05/09/2026 mais fonction laissee intacte).
import json
import logging
from groq import Groq
from constantes_agent import get_secret, MODELE_MODERATION, POLITIQUE_MODERATION

def _verifier_message_utilisateur(message: str) -> tuple[bool, str | None]:
    """
    Verifie un message via gpt-oss-safeguard-20b + POLITIQUE_MODERATION
    (voir plus haut -- remplace Llama Guard 4, retire par Groq). Retourne
    (est_sur: bool, categorie: str|None -- presente seulement si
    est_sur=False). En cas d'erreur reseau/API OU si le JSON renvoye est
    illisible, on laisse passer plutot que de bloquer tout le chat pour un
    souci technique isole sur CE modele de moderation (pas le modele
    principal) -- (True, None) avec un log d'avertissement.
    """
    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=8.0)
        completion = client.chat.completions.create(
            model=MODELE_MODERATION,
            messages=[
                {"role": "system", "content": POLITIQUE_MODERATION},
                {"role": "user", "content": message},
            ],
            reasoning_effort="low",  # priorite a la latence, c'est un feu vert/rouge avant le vrai appel
        )
        resultat = json.loads(completion.choices[0].message.content or "{}")
        if not resultat.get("violation"):
            return True, None
        return False, resultat.get("category")
    except Exception as e:
        logging.warning(f"Modération d'entrée indisponible (gpt-oss-safeguard), message laissé passer : {e}")
        return True, None


