#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLIENT MISTRAL - narration automatique
=======================================
Client minimal pour l'API Mistral (endpoint "chat completions"), ecrit
avec uniquement la bibliotheque standard (urllib) -- aucune dependance
externe a installer, pour rester coherent avec le reste du projet
(compatible Pydroid 3 / Chaquopy sans rien ajouter au pip install).

Utilise par dice_web.py pour la narration automatique : a chaque
lancer, l'appli envoie le contexte du jeu + l'evenement au modele et
recupere la suite de l'histoire, sans que l'utilisateur ait besoin de
copier-coller quoi que ce soit dans un site externe.

Documentation officielle : https://docs.mistral.ai/
"""

import json
import urllib.request
import urllib.error

API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MODEL = "mistral-small-2603"     # nom precis (plutot qu'un alias "-latest")
                                          # pour garder un cout/comportement stable
                                          # et previsible dans le temps
DEFAULT_TIMEOUT = 40                     # secondes
DEFAULT_MAX_TOKENS = 1400                # cf. dice_web.py : 700 coupait trop
                                          # souvent la narration en plein milieu
                                          # de phrase des que la reponse etait un
                                          # peu developpee (le francais consomme
                                          # plus de tokens/mot que l'anglais).

# Modeles proposes dans le menu deroulant de dice_web.py (page de config
# de la cle API). Chaque entree est (identifiant_exact_pour_l_API, label
# affiche a l'utilisateur). Garder des noms de version precis (pas
# d'alias "-latest") pour que le cout/comportement reste stable dans le
# temps -- voir la remarque sur DEFAULT_MODEL ci-dessus.
MODEL_CHOICES = [
    ("ministral-8b-2512",
     "Ministral 8B -- tres economique, style plus simple"),
    ("mistral-small-2603",
     "Mistral Small -- rapide et economique (recommande)"),
    ("mistral-medium-latest",
     "Mistral Medium -- histoires plus riches, un peu plus cher"),
]


def chat(api_key, messages, model=DEFAULT_MODEL,
         max_tokens=DEFAULT_MAX_TOKENS, timeout=DEFAULT_TIMEOUT,
         prompt_cache_key=None):
    """Envoie une conversation (liste de {"role": "system"/"user"/"assistant",
    "content": str}) a l'API Mistral.

    prompt_cache_key (optionnel) : identifiant stable (ex: le slug de
    l'histoire en cours) a fournir pour beneficier du "prompt caching"
    cote Mistral -- quand deux appels consecutifs partagent le meme
    debut de prompt (ici : le message systeme + le resume long terme,
    qui ne changent pas d'un tour a l'autre tant que le resume n'est
    pas mis a jour), les tokens de ce prefixe sont factures a 10% du
    tarif normal au lieu du plein tarif. Reduit le cout sans rien
    changer au contenu envoye ni au comportement de l'API (qui reste
    sans etat : chaque appel doit toujours contenir tout le contexte
    voulu, la cle ne fait qu'accelerer/reduire le cout du calcul cote
    serveur quand le debut du prompt est identique a un appel recent).

    Renvoie toujours un tuple (texte, erreur) et ne leve jamais
    d'exception : toute erreur reseau, HTTP ou de format est convertie
    en message clair, pour que l'appli reste utilisable (mode manuel de
    secours) meme si l'IA est temporairement injoignable.
      - succes  -> (texte_de_la_reponse, None)
      - echec   -> (None, "message d'erreur lisible")
    """
    if not api_key:
        return None, "Aucune cle API Mistral configuree."
    if not messages:
        return None, "Rien a envoyer a l'IA."

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": max_tokens,
    }
    if prompt_cache_key:
        payload["prompt_cache_key"] = prompt_cache_key
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            detail_msg = detail.get("message") or detail.get("error") or str(detail)
            # Certaines erreurs Mistral imbriquent le message utile dans un
            # sous-objet ({"error": {"message": "...", "type": "..."}})
            # plutot que directement a la racine -- on essaie de le
            # depiler pour ne pas afficher juste "{'message': ..., ...}".
            if isinstance(detail_msg, dict):
                detail_msg = detail_msg.get("message") or str(detail_msg)
        except Exception:
            detail_msg = getattr(e, "reason", str(e))
        if e.code == 401:
            return None, "Cle API Mistral refusee (401) : verifie qu'elle est correcte."
        if e.code == 429:
            # On affiche desormais le detail renvoye par Mistral (type de
            # limite depassee, message precis...) en plus du code, au lieu
            # d'un texte fixe qui masquait la vraie cause quand ce n'etait
            # pas un simple pic de trafic (ex : plan non active, quota
            # mensuel epuise...).
            return None, (f"Limite Mistral atteinte (429) : {detail_msg} "
                           "Reessaie dans un instant ou verifie ton plan sur console.mistral.ai.")
        return None, f"Erreur Mistral ({e.code}) : {detail_msg}"
    except urllib.error.URLError as e:
        return None, f"Impossible de joindre l'API Mistral (reseau ?) : {e.reason}"
    except Exception as e:  # securite : ne jamais planter l'appli pour ca
        return None, f"Erreur inattendue en contactant Mistral : {e}"

    try:
        data = json.loads(raw)
        text = data["choices"][0]["message"]["content"]
        text = (text or "").strip()
        if not text:
            return None, "Reponse vide renvoyee par l'API Mistral."
        return text, None
    except (KeyError, IndexError, ValueError, TypeError):
        return None, "Reponse de l'API Mistral illisible (format inattendu)."
