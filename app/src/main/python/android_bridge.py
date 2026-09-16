# -*- coding: utf-8 -*-
"""
Pont entre l'application Android (Kotlin) et le serveur Flask existant.

Ce module est appele une seule fois au demarrage de l'appli (depuis
MainActivity.kt). Il :
  1. Place le repertoire de travail sur le stockage interne de l'appli
     (os.environ["HOME"], fourni automatiquement par Chaquopy), qui est
     le seul endroit inscriptible sur Android.
  2. Importe dice_web (qui cree l'objet Flask "app", sans choisir
     d'histoire : l'application demarre entierement vide, et c'est le
     joueur qui cree sa premiere histoire via l'ecran de selection).

     Aucune histoire n'etant fournie avec l'appli, il n'y a aucune
     sauvegarde de depart a copier ici : chaque histoire (creee depuis
     l'appli) part de zero au premier lancement, geree entierement par
     switch_story() / create_custom_story() dans dice_web.py et
     stories.py.

IMPORTANT -- il n'y a plus aucun serveur reseau ici.
Auparavant, dice_web.app tournait via app.run() sur 127.0.0.1:PORT, et
MainActivity chargeait cette adresse dans une WebView -- avec le risque
que deux applis installees en parallele se disputent le meme port TCP
(127.0.0.1 est partage par tout l'appareil, pas cloisonne par appli).

Desormais, aucune socket n'est jamais ouverte. La WebView charge une
adresse purement virtuelle ("http://127.0.0.1/", jamais reellement
contactee), et chaque requete est interceptee cote Android puis simulee
ici via app.test_client() -- le client de test integre a Flask, qui
execute la requete directement en memoire, sans reseau.

Deux chemins d'entree cote Android pour ces requetes (voir MainActivity.kt) :
  - Les GET/HEAD (pages, CSS, JS, images) sont interceptes nativement par
    WebViewClient.shouldInterceptRequest().
  - Les POST (actions de jeu, upload d'image) sont interceptes par un
    script JS injecte dans la page, car l'API Android ne donne pas acces
    au corps d'une requete POST au niveau de shouldInterceptRequest.

Les deux chemins appellent la meme fonction handle_request() ci-dessous.

Le client de test est cree UNE SEULE FOIS et reutilise pour toutes les
requetes : cela donne les sessions Flask "gratuitement" (le cookie de
session est gere par le client de test lui-meme, cote Python), sans avoir
besoin de faire transiter le moindre Cookie/Set-Cookie entre Kotlin, JS
et Python.
"""

import os
import threading
import json
import base64

_started = False
_lock = threading.Lock()
_client = None
# Un seul verrou global protege maintenant a la fois la creation du
# client de test ET tout le traitement d'une requete (voir handle_request
# ci-dessous). Les requetes issues de la WebView sont ainsi serialisees,
# comme elles l'etaient implicitement avec l'ancien serveur Flask en
# mode threaded=False -- ce qui evite toute concurrence non protegee sur
# CURRENT_STORY, session, et les fichiers dice_state_*.json.
_request_lock = threading.Lock()

# En-tetes de reponse qu'on ne renvoie jamais tels quels a la WebView --
# soit parce qu'ils n'ont pas de sens hors d'une vraie connexion reseau
# (Content-Length, Content-Encoding, Transfer-Encoding, Connection), soit
# parce que la gestion de session ne passe plus par les cookies (voir
# plus haut) et qu'il vaut mieux ne pas les exposer inutilement.
_STRIP_RESPONSE_HEADERS = {
    "content-length", "content-encoding", "transfer-encoding",
    "connection", "set-cookie",
}


def start_server():
    """Prepare le repertoire de travail et importe dice_web.

    Ne demarre plus aucun serveur reseau (voir docstring du module) :
    des que dice_web est importe, l'app Flask est prete et handle_request()
    peut servir des requetes immediatement -- inutile d'attendre ou de
    faire des tentatives repetees cote Kotlin.
    """
    global _started
    with _lock:
        if _started:
            return "already_running"
        _started = True

        home = os.environ["HOME"]
        os.chdir(home)

        import dice_web  # cree l'app Flask (aucune histoire choisie au demarrage)
        assert dice_web.app is not None

        return "started"


def _get_client():
    # Appelee uniquement depuis handle_request(), qui detient deja
    # _request_lock : pas besoin d'un verrou separe ici.
    global _client
    if _client is None:
        import dice_web
        # Pas de "with" : on garde ce client vivant pour toute la duree
        # de vie de l'appli, afin que son cookie jar interne persiste
        # d'une requete a l'autre et que les sessions Flask marchent.
        _client = dice_web.app.test_client()
    return _client


def handle_request(method, path, headers_json="{}", body_b64=""):
    """Simule une requete HTTP contre l'app Flask, sans reseau.

    Appelee depuis Kotlin (GET/HEAD, via shouldInterceptRequest) et depuis
    le pont JS injecte dans la page (POST, formulaires, uploads).

    Renvoie une chaine JSON : {"status": int, "headers": {...}, "body_b64": str}
    """
    try:
        headers = json.loads(headers_json) if headers_json else {}
    except (ValueError, TypeError):
        headers = {}

    try:
        body = base64.b64decode(body_b64) if body_b64 else b""
    except Exception:
        body = b""

    # On retire nous-memes tout Cookie entrant : la session est geree en
    # interne par le client de test persistant, pas par des cookies
    # transmis depuis la WebView.
    header_items = [(k, v) for k, v in headers.items() if k.lower() != "cookie"]

    # Tout le traitement (recuperation/creation du client inclus) se fait
    # sous _request_lock : les requetes de la WebView sont ainsi
    # serialisees, ce qui protege l'etat global de dice_web.py (session,
    # CURRENT_STORY, fichiers dice_state_*.json) contre les acces
    # concurrents -- par ex. plusieurs GET /totem_images/<filename> lances
    # en parallele par la page.
    with _request_lock:
        try:
            client = _get_client()
            # follow_redirects=True : les routes en Post/Redirect/Get
            # (create_story, delete_story, configure_key, set_model,
            # classic_dice/clear, ...) renvoient un 302 que Werkzeug
            # convertit alors lui-meme en GET vers la cible, et c'est le
            # HTML final (200) qui revient ici. Sans ca, le script JS
            # cote WebView recevait la mini-page de redirection de
            # Werkzeug (elle aussi en text/html, mais sans contenu utile)
            # au lieu de la vraie page de destination.
            resp = client.open(
                path, method=method, data=body, headers=header_items,
                follow_redirects=True,
            )
            out_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower() not in _STRIP_RESPONSE_HEADERS
            }
            result = {
                "status": resp.status_code,
                "headers": out_headers,
                "body_b64": base64.b64encode(resp.get_data()).decode("ascii"),
            }
        except Exception as exc:
            result = {
                "status": 500,
                "headers": {"Content-Type": "text/plain; charset=utf-8"},
                "body_b64": base64.b64encode(
                    ("Erreur interne : %s" % exc).encode("utf-8")
                ).decode("ascii"),
            }

    return json.dumps(result)
