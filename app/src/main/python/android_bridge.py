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
     joueur qui cree sa premiere histoire via l'ecran de selection) et
     le lance dans un thread en arriere-plan, sur 127.0.0.1:PORT (voir
     PORT ci-dessous).

     Aucune histoire n'etant plus fournie avec l'appli, il n'y a plus de
     sauvegarde de depart a copier ici : chaque histoire (creee depuis
     l'appli) part desormais de zero au premier lancement, geree
     entierement par switch_story() / create_custom_story() dans
     dice_web.py et stories.py.

Le WebView de MainActivity charge ensuite directement cette adresse : tout
se passe a l'interieur de l'application, sans jamais ouvrir de navigateur
externe.

IMPORTANT -- pourquoi PORT n'est PAS 5001 ici :
127.0.0.1 (la boucle locale) est partage par TOUT l'appareil Android, pas
cloisonne par application comme le reste (stockage, memoire...). Si deux
applications differentes essaient chacune de se brancher sur le meme port
pendant qu'elles tournent toutes les deux en arriere-plan, la premiere a
avoir demarre garde le port et la seconde ne peut plus se connecter --
elle reste bloquee, comme si elle ne s'ouvrait plus. Cette version
utilise donc un port different (5011) de celui de la toute premiere
version (5001), pour que les deux applications puissent tourner en meme
temps sans jamais se gener, meme laissees ouvertes toutes les deux en fond.
"""

import os
import threading

# Port du serveur interne de CETTE version. Doit rester different de
# celui de toute autre variante de l'appli installee en parallele sur le
# meme telephone -- voir l'explication ci-dessus. Doit correspondre
# exactement a la valeur de "serverUrl" dans MainActivity.kt.
PORT = 5011

_started = False
_lock = threading.Lock()


def start_server():
    global _started
    with _lock:
        if _started:
            return "already_running"
        _started = True

        home = os.environ["HOME"]
        os.chdir(home)

        import dice_web  # cree l'app Flask (aucune histoire choisie au demarrage)

        def _run():
            dice_web.app.run(
                host="127.0.0.1",
                port=PORT,
                debug=False,
                use_reloader=False,
                threaded=True,
            )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return "started"
