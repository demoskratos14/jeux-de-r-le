#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOTEUR DE DES - Histoire heroique
==================================
Compatible Pydroid 3 (aucune dependance externe).

Deux des :
- Le DE DE REUSSITE (classique, 1 a 6) : de vrais echecs sont possibles (1
  et 2), mais jamais de defaite pure qui mettrait fin a l'histoire -- il y
  a toujours moyen de se rattraper. Le 6 est une reussite exceptionnelle.
- Le DE DU DESTIN (6 symboles) : un evenement narratif qui vient corser ou
  enrichir la scene.

Utilisation typique :
1. Lancez le script -> menu pour lancer un des, l'autre, ou les deux.
2. Chaque lancer est archive dans un historique persistant (dice_state.json).
3. L'historique complet peut etre affiche/copie a tout moment.
"""

import random
import json
import os

SAVE_FILE = "dice_state.json"

# Seuil (en points) a partir duquel une jauge totemique devient utilisable.
TOTEM_ENERGY_THRESHOLD = 15
# Seuil de la jauge de menace avant qu'une complication inattendue survienne.
THREAT_THRESHOLD = 10
# Nombre de messages recents (hors message systeme) renvoyes a l'API Mistral
# a chaque appel, pour rester sous la limite de contexte du modele. La
# conversation complete reste conservee dans la sauvegarde pour l'affichage.
AI_HISTORY_WINDOW = 24

SUCCESS_LABELS = {
    1: "Echec critique / consequence importante (mais jamais la fin de l'histoire : il y a toujours moyen de se rattraper)",
    2: "Echec, ou reussite tres difficile",
    3: "Reussite partielle",
    4: "Bonne reussite",
    5: "Tres bonne reussite",
    6: "Reussite exceptionnelle, heroique !",
}

# Symboles disponibles pour les "constellations" (pips) du de de reussite.
# Choix inspires de l'histoire d'Animorph : son mentor, ses totems, ses allies.
PIP_SYMBOLS = {
    # Peuple au demarrage par dice_web.switch_story(), selon l'histoire
    # active. Reste un dict MUTABLE (jamais reassigne, seulement
    # vide+rempli via .clear()/.update()) pour que toutes les references
    # existantes (imports "from dice_engine import PIP_SYMBOLS" compris)
    # voient toujours son contenu a jour.
}
DEFAULT_PIP_SYMBOL = ""

# Ordre fixe des 6 faces du de du destin
FATE_FACES = [
    {"key": "coeur", "emoji": "\u2764\ufe0f", "label": "Coeur",
     "desc": "Allie / protection / aide providentielle : un ami ou heros peut "
             "intervenir, une guerison peut survenir, un lien peut se renforcer "
             "ou un miracle se produire."},
    {"key": "question", "emoji": "\u2753", "label": "Point d'interrogation",
     "desc": "Une quete secondaire se debloque, a faire quand on veut : "
             "l'occasion de se faire un nouvel ami, ou de trouver un nouvel "
             "objet (voire un nouveau totem)."},
    {"key": "soleil", "emoji": "\u2600\ufe0f", "label": "Soleil",
     "desc": "Benediction / amelioration : energie positive, pouvoir renforce "
             "ou stabilise, protection, amelioration durable."},
    {"key": "etoile", "emoji": "\u2b50", "label": "Etoile",
     "desc": "Chance exceptionnelle : opportunite rare, decouverte precieuse, "
             "coup de chance ou recompense speciale."},
    {"key": "exclamation", "emoji": "\u2757", "label": "Point d'exclamation",
     "desc": "De l'aide arrive : une personne que l'on connait vient preter "
             "main forte, ou un animal lie a l'un de nos totems intervient "
             "pour nous aider."},
    {"key": "spirale", "emoji": "\U0001f300", "label": "Spirale",
     "desc": "Chaos / transformation : effet imprevisible, mutation, "
             "transformation inhabituelle ou consequence qui peut modifier "
             "l'histoire de facon inattendue."},
]
FATE_BY_KEY = {f["key"]: f for f in FATE_FACES}


class DiceSession:
    def __init__(self):
        self.history = []   # liste de dicts : {id, type, success, fate, note}
        self.next_id = 1
        self.pip_symbol = DEFAULT_PIP_SYMBOL
        # Mode d'affichage du de de reussite :
        #   "single" -> un seul symbole fixe (pip_symbol) sur toutes les faces
        #   "random" -> un symbole tire au sort (parmi enabled_symbols) a
        #               chaque lancer, identique sur toute la face
        #   "mixed"  -> chaque pip de la face peut avoir un symbole different,
        #               tire au sort parmi enabled_symbols
        self.pip_mode = "single"
        self.enabled_symbols = list(PIP_SYMBOLS.keys())

        # Totems/allies ajoutes par le joueur en cours de partie, en plus
        # des symboles de base (PIP_SYMBOLS, fixes dans le code). Chaque
        # entree : {"key", "label", "emoji", "image" (nom de fichier ou
        # None), "powers" (liste de str), "special" (str, optionnelle)}.
        # Voir all_symbols()/all_symbol_keys() pour la fusion avec les
        # symboles de base, utilisee partout ailleurs dans le moteur.
        self.custom_totems = []
        # Valeurs autorisees pour le de de reussite : par defaut les 6 sont
        # actives. On peut en decocher certaines (ex: ne garder que 1, 5 et
        # 6) pour representer un avantage/desavantage narratif ; le tirage
        # se fait alors uniquement parmi les valeurs restantes. Reste actif
        # jusqu'a reactivation manuelle, pour tous les lancers (roll_success
        # et roll_both). Au moins une valeur reste toujours active.
        self.allowed_success_values = [1, 2, 3, 4, 5, 6]

        # Symboles autorises pour le de du destin : meme principe que
        # allowed_success_values, mais pour les 6 faces du destin (coeur,
        # ?, soleil, etoile, !, spirale). On peut en decocher certains
        # pour orienter le ton d'un passage (ex: ne garder que des
        # symboles positifs). Au moins un symbole reste toujours actif.
        # Le '?' garde en plus sa regle propre (pas de repetition tant
        # qu'une quete secondaire est ouverte), appliquee dans _fate_pool.
        self.allowed_fate_keys = [f["key"] for f in FATE_FACES]

        # Jauge d'energie totemique : un compteur par symbole/totem/allie.
        # Se remplit du score obtenu (dans les modes "single"/"random", ou
        # elle est la meme pour tous les pips de la face), ou de +1 par
        # symbole apparu (dans le mode "mixed", ou chaque pip peut differer).
        # Une fois >= TOTEM_ENERGY_THRESHOLD, la jauge est utilisable :
        # capacite speciale du totem, aide d'un allie, ou "Second Souffle"
        # pour la Patte (relance immediate du dernier lancer).
        self.totem_energy = {k: 0 for k in PIP_SYMBOLS}

        # Jauge de menace : monte a chaque reussite fragile (valeur 1),
        # redescend legerement sur une bonne reussite (5 ou 6). Quand elle
        # atteint THREAT_THRESHOLD, une complication secondaire inattendue
        # survient et la jauge se reinitialise.
        self.threat_level = 0

        # Quetes secondaires debloquees par le symbole "?" du de du destin :
        # liste de dicts {id, kind ("ami" ou "objet"), status}.
        self.side_quests = []
        self.next_quest_id = 1

        # Journal de l'histoire : liste des resumes de chapitre colles par
        # l'utilisateur (generes par l'IA narratrice a la fin de chaque
        # chapitre), conserves pour garder une trace complete de l'aventure.
        self.story_log = []

        # --- Narration automatique (optionnelle) ---
        # NB: la cle API Mistral n'est PAS geree ici (elle est au niveau de
        # l'application entiere, partagee entre toutes les histoires -- voir
        # app_config.json / load_app_config() dans dice_web.py), une seule
        # cle suffisant largement pour toutes.
        # Conversation avec l'IA narratrice : liste de {"role", "content"}.
        # Le premier message (role "system") est le contexte de mecaniques
        # du jeu ; chaque lancer/evenement ajoute un message "user", chaque
        # reponse de l'IA un message "assistant". C'est le journal complet
        # de l'histoire en mode automatique (affiche integralement), mais
        # seule une fenetre recente en est renvoyee a l'API a chaque appel
        # (voir ai_messages_to_send) pour ne pas depasser son contexte.
        self.ai_conversation = []

    # ---------- persistance ----------
    def save(self):
        data = {"history": self.history, "next_id": self.next_id,
                 "pip_symbol": self.pip_symbol, "pip_mode": self.pip_mode,
                 "enabled_symbols": self.enabled_symbols,
                 "allowed_success_values": self.allowed_success_values,
                 "allowed_fate_keys": self.allowed_fate_keys,
                 "totem_energy": self.totem_energy,
                 "threat_level": self.threat_level,
                 "side_quests": self.side_quests,
                 "next_quest_id": self.next_quest_id,
                 "story_log": self.story_log,
                 "ai_conversation": self.ai_conversation,
                 "custom_totems": self.custom_totems}
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(SAVE_FILE):
            with open(SAVE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.history = data.get("history", [])
            self.next_id = data.get("next_id", 1)
            self.pip_symbol = data.get("pip_symbol", DEFAULT_PIP_SYMBOL)
            pip_mode = data.get("pip_mode", "single")
            self.pip_mode = pip_mode if pip_mode in ("single", "random", "mixed") else "single"

            # Totems ajoutes par le joueur : charges AVANT enabled_symbols et
            # totem_energy, car ces deux-la dependent de all_symbol_keys()
            # (base + totems ajoutes) pour ne pas perdre ceux-ci au filtrage.
            raw_custom = data.get("custom_totems") or []
            self.custom_totems = []
            for t in raw_custom:
                if not isinstance(t, dict) or not t.get("key") or not t.get("label"):
                    continue
                if t["key"] in PIP_SYMBOLS:
                    continue  # ne doit jamais ecraser un symbole de base
                self.custom_totems.append({
                    "key": t["key"],
                    "label": str(t["label"]),
                    "emoji": str(t.get("emoji") or ""),
                    "image": t.get("image") if isinstance(t.get("image"), str) else None,
                    "powers": [p for p in (t.get("powers") or []) if isinstance(p, str)],
                    "special": str(t.get("special") or ""),
                })
            valid_keys = self.all_symbol_keys()

            enabled = data.get("enabled_symbols") or []
            enabled = [k for k in enabled if k in valid_keys]
            self.enabled_symbols = enabled or list(valid_keys)
            min_value = data.get("allowed_success_values")
            allowed = [v for v in (min_value or []) if v in (1, 2, 3, 4, 5, 6)]
            self.allowed_success_values = sorted(set(allowed)) or [1, 2, 3, 4, 5, 6]

            allowed_fate = data.get("allowed_fate_keys")
            allowed_fate = [k for k in (allowed_fate or []) if k in FATE_BY_KEY]
            self.allowed_fate_keys = allowed_fate or [f["key"] for f in FATE_FACES]

            energy = data.get("totem_energy") or {}
            self.totem_energy = {k: int(energy.get(k, 0)) for k in valid_keys}

            try:
                self.threat_level = max(0, int(data.get("threat_level", 0)))
            except (TypeError, ValueError):
                self.threat_level = 0

            quests = data.get("side_quests") or []
            self.side_quests = [q for q in quests if isinstance(q, dict) and "id" in q]
            self.next_quest_id = data.get("next_quest_id", len(self.side_quests) + 1)

            story_log = data.get("story_log") or []
            self.story_log = [s for s in story_log if isinstance(s, str) and s.strip()]

            conv = data.get("ai_conversation") or []
            self.ai_conversation = [
                m for m in conv
                if isinstance(m, dict) and m.get("role") in ("system", "user", "assistant")
                and isinstance(m.get("content"), str) and m.get("content").strip()
            ]
            return True
        return False

    def set_pip_symbol(self, key):
        if key in self.all_symbol_keys():
            self.pip_symbol = key
            self.pip_mode = "single"
            self.save()
            return True
        return False

    def set_pip_mode(self, mode):
        """Active le mode 'aleatoire' ou 'mixe'. Reactive au passage toutes
        les constellations (l'utilisateur peut ensuite en decocher). Si le
        mode demande est deja actif, un second clic le desactive et
        revient au choix d'une constellation simple."""
        if mode not in ("random", "mixed"):
            return False
        if self.pip_mode == mode:
            self.pip_mode = "single"
        else:
            self.pip_mode = mode
            self.enabled_symbols = list(self.all_symbol_keys())
        self.save()
        return True

    def toggle_enabled_symbol(self, key):
        """Coche/decoche un symbole dans le pool utilise par les modes
        aleatoire/mixe. On garde toujours au moins un symbole actif."""
        if key not in self.all_symbol_keys():
            return False
        if key in self.enabled_symbols:
            if len(self.enabled_symbols) > 1:
                self.enabled_symbols.remove(key)
        else:
            self.enabled_symbols.append(key)
        self.save()
        return True

    # ---------- totems ajoutes par le joueur ----------
    def all_symbol_keys(self):
        """Ensemble de toutes les cles de symboles valides : celles de
        base (PIP_SYMBOLS) + celles des totems ajoutes par le joueur."""
        return set(PIP_SYMBOLS.keys()) | {t["key"] for t in self.custom_totems}

    def all_symbols(self):
        """Fusionne les symboles de base et les totems ajoutes par le
        joueur en un seul dict {cle: {emoji, label, powers, special,
        image, is_custom}}, utilise partout ou l'appli doit afficher ou
        proposer TOUS les symboles disponibles (choix du pip, jauges
        totemiques, contexte envoye a l'IA...)."""
        merged = {}
        for k, v in PIP_SYMBOLS.items():
            merged[k] = {"emoji": v["emoji"], "label": v["label"], "image": None,
                         "powers": [], "special": "", "is_custom": False}
        for t in self.custom_totems:
            merged[t["key"]] = {
                "emoji": t.get("emoji") or "\U0001F43E",
                "label": t["label"],
                "image": t.get("image"),
                "powers": t.get("powers") or [],
                "special": t.get("special") or "",
                "is_custom": True,
            }
        return merged

    def _slugify_totem_key(self, label):
        base = "".join(c.lower() if c.isalnum() else "_" for c in label).strip("_")
        while "__" in base:
            base = base.replace("__", "_")
        base = base or "totem"
        key = base
        existing = self.all_symbol_keys()
        n = 2
        while key in existing:
            key = f"{base}_{n}"
            n += 1
        return key

    def add_custom_totem(self, label, powers_text="", special="", emoji="", image_filename=None):
        """Ajoute un nouveau totem/allie en cours de partie : nouvelle
        entree dans le selecteur de symboles, nouvelle jauge d'energie
        (a 0), active par defaut. Renvoie la cle attribuee, ou None si le
        nom est vide."""
        label = (label or "").strip()
        if not label:
            return None
        key = self._slugify_totem_key(label)
        powers = [p.strip() for p in (powers_text or "").split(",") if p.strip()]
        self.custom_totems.append({
            "key": key,
            "label": label,
            "emoji": (emoji or "").strip(),
            "image": image_filename,
            "powers": powers,
            "special": (special or "").strip(),
        })
        if key not in self.enabled_symbols:
            self.enabled_symbols.append(key)
        self.totem_energy[key] = 0
        self.save()
        return key

    def remove_custom_totem(self, key):
        """Retire un totem ajoute par le joueur (jauge et image comprises
        -- le fichier image lui-meme doit etre supprime cote appelant).
        Sans effet sur les symboles de base (PIP_SYMBOLS)."""
        before = len(self.custom_totems)
        self.custom_totems = [t for t in self.custom_totems if t["key"] != key]
        if len(self.custom_totems) == before:
            return False
        if key in self.enabled_symbols and len(self.enabled_symbols) > 1:
            self.enabled_symbols.remove(key)
        self.totem_energy.pop(key, None)
        self.save()
        return True

    def toggle_allowed_value(self, value):
        """Coche/decoche une valeur (1-6) du de de reussite dans le pool de
        tirage. On garde toujours au moins une valeur active."""
        if value not in (1, 2, 3, 4, 5, 6):
            return False
        if value in self.allowed_success_values:
            if len(self.allowed_success_values) > 1:
                self.allowed_success_values.remove(value)
        else:
            self.allowed_success_values.append(value)
            self.allowed_success_values.sort()
        self.save()
        return True

    def reset_allowed_values(self):
        """Reactive les 6 valeurs (desactive toute contrainte)."""
        self.allowed_success_values = [1, 2, 3, 4, 5, 6]
        self.save()

    def toggle_allowed_fate(self, key):
        """Coche/decoche un symbole du de du destin dans le pool de tirage.
        On garde toujours au moins un symbole actif."""
        if key not in FATE_BY_KEY:
            return False
        if key in self.allowed_fate_keys:
            if len(self.allowed_fate_keys) > 1:
                self.allowed_fate_keys.remove(key)
        else:
            self.allowed_fate_keys.append(key)
        self.save()
        return True

    def reset_allowed_fate(self):
        """Reactive les 6 symboles du de du destin (desactive toute contrainte)."""
        self.allowed_fate_keys = [f["key"] for f in FATE_FACES]
        self.save()

    def _roll_success_value(self):
        pool = self.allowed_success_values or [1, 2, 3, 4, 5, 6]
        return random.choice(pool)

    # ---------- jauge d'energie totemique ----------
    def _apply_totem_energy(self, value, pip_choice, pip_mode):
        if pip_mode == "mixed":
            for k in pip_choice:
                if k:
                    self.totem_energy[k] = self.totem_energy.get(k, 0) + 1
        else:
            k = pip_choice[0] if pip_choice else None
            if k:
                self.totem_energy[k] = self.totem_energy.get(k, 0) + value

    def is_totem_ready(self, key):
        return self.totem_energy.get(key, 0) >= TOTEM_ENERGY_THRESHOLD

    def spend_totem_energy(self, key):
        """Consomme la jauge d'un totem/allie si elle est pleine. Renvoie
        True si elle a bien ete depensee (et donc que l'effet peut etre
        declenche cote appelant), False si elle n'etait pas encore prete."""
        if key not in self.all_symbol_keys() or not self.is_totem_ready(key):
            return False
        self.totem_energy[key] = 0
        self.save()
        return True

    # ---------- jauge de menace ----------
    def _apply_threat(self, value):
        """Met a jour la jauge de menace selon le score obtenu. Renvoie True
        si le seuil vient d'etre atteint (une complication inattendue doit
        survenir), auquel cas la jauge est reinitialisee."""
        if value == 1:
            self.threat_level += 3
        elif value >= 5:
            self.threat_level = max(0, self.threat_level - 1)
        if self.threat_level >= THREAT_THRESHOLD:
            self.threat_level = 0
            return True
        return False

    # ---------- quetes secondaires (?) ----------
    def _spawn_side_quest(self):
        kind = random.choice(["ami", "objet"])
        quest = {"id": self.next_quest_id, "kind": kind, "status": "ouverte"}
        self.next_quest_id += 1
        self.side_quests.append(quest)
        return quest

    def complete_side_quest(self, quest_id):
        for q in self.side_quests:
            if q["id"] == quest_id:
                q["status"] = "terminee"
                self.save()
                return True
        return False

    def open_side_quests(self):
        return [q for q in self.side_quests if q["status"] == "ouverte"]

    # ---------- journal de l'histoire ----------
    def add_story_entry(self, text):
        text = (text or "").strip()
        if not text:
            return False
        self.story_log.append(text)
        self.save()
        return True

    def story_log_text(self):
        if not self.story_log:
            return ""
        chapters = []
        for i, entry in enumerate(self.story_log, start=1):
            chapters.append(f"--- Chapitre {i} ---\n{entry}")
        return "\n\n".join(chapters)

    # ---------- narration automatique (IA) ----------
    def add_ai_message(self, role, content):
        if role not in ("system", "user", "assistant"):
            return
        content = (content or "").strip()
        if not content:
            return
        self.ai_conversation.append({"role": role, "content": content})
        self.save()

    def reset_ai_conversation(self):
        """Efface uniquement la conversation avec l'IA narratrice (elle
        sera regeneree, mecaniques comprises, au prochain lancer) -- le
        reste de la partie (jauges, quetes, historique des des...) n'est
        pas touche. Utile si la conversation devient tres longue ou part
        dans une mauvaise direction."""
        self.ai_conversation = []
        self.save()

    def ai_messages_to_send(self):
        """Messages a effectivement transmettre a l'API : le message
        systeme (mecaniques du jeu, toujours conserve) + une fenetre
        recente de l'echange, pour rester sous la limite de contexte du
        modele. L'integralite de la conversation reste conservee dans
        self.ai_conversation (et dans la sauvegarde) pour l'affichage."""
        if not self.ai_conversation:
            return []
        if self.ai_conversation[0]["role"] == "system":
            head = [self.ai_conversation[0]]
            rest = self.ai_conversation[1:]
        else:
            head = []
            rest = self.ai_conversation
        return head + rest[-AI_HISTORY_WINDOW:]

    def _resolve_pip_choice(self, value):
        """Determine le symbole utilise pour chaque pip d'une face, selon
        le mode courant. Le nombre de pips est toujours egal a la valeur
        (1 a 6). Si aucun symbole n'est encore disponible (ex: tout debut
        d'une histoire qui part de zero), le pool est vide : on renvoie
        alors simplement le pip_symbol courant (potentiellement vide) sans
        tirage au sort, pour ne jamais planter sur une liste vide."""
        pool = self.enabled_symbols or list(self.all_symbol_keys())
        if not pool:
            return [self.pip_symbol] * value
        if self.pip_mode == "random":
            symbol = random.choice(pool)
            return [symbol] * value
        if self.pip_mode == "mixed":
            return [random.choice(pool) for _ in range(value)]
        return [self.pip_symbol] * value

    # ---------- lancers ----------
    def roll_success(self, note=""):
        value = self._roll_success_value()
        pip_choice = self._resolve_pip_choice(value)
        self._apply_totem_energy(value, pip_choice, self.pip_mode)
        threat_triggered = self._apply_threat(value)
        record = {
            "id": self.next_id, "type": "success",
            "success": value, "fate": None, "note": note,
            "pip_choice": pip_choice,
            "pip_mode": self.pip_mode,
            "threat_triggered": threat_triggered,
        }
        self.next_id += 1
        self.history.append(record)
        self.save()
        return record

    def _fate_pool(self):
        """Pool de tirage du de du destin : les symboles coches par
        l'utilisateur (allowed_fate_keys), moins le '?' si une quete
        secondaire est deja ouverte -- pour eviter d'en accumuler
        plusieurs. Si exclure le '?' viderait entierement le pool coche
        (ex: seul le '?' est coche), il reste exceptionnellement autorise
        plutot que de bloquer le tirage."""
        allowed = self.allowed_fate_keys or [f["key"] for f in FATE_FACES]
        pool = [f for f in FATE_FACES if f["key"] in allowed]
        if self.open_side_quests():
            pool_wo_question = [f for f in pool if f["key"] != "question"]
            if pool_wo_question:
                return pool_wo_question
        return pool

    def roll_fate(self, note=""):
        face = random.choice(self._fate_pool())
        side_quest = self._spawn_side_quest() if face["key"] == "question" else None
        record = {
            "id": self.next_id, "type": "fate",
            "success": None, "fate": face["key"], "note": note,
            "side_quest": side_quest,
        }
        self.next_id += 1
        self.history.append(record)
        self.save()
        return record

    def roll_both(self, note=""):
        value = self._roll_success_value()
        face = random.choice(self._fate_pool())
        pip_choice = self._resolve_pip_choice(value)
        self._apply_totem_energy(value, pip_choice, self.pip_mode)
        threat_triggered = self._apply_threat(value)
        side_quest = self._spawn_side_quest() if face["key"] == "question" else None
        record = {
            "id": self.next_id, "type": "both",
            "success": value, "fate": face["key"], "note": note,
            "pip_choice": pip_choice,
            "pip_mode": self.pip_mode,
            "threat_triggered": threat_triggered,
            "side_quest": side_quest,
        }
        self.next_id += 1
        self.history.append(record)
        self.save()
        return record

    def second_souffle(self, note="Second Souffle"):
        """Effet de relance immediate (utilise par la Patte d'Animorph, la
        Baguette de Poudlard, ou tout autre totem futur ayant ce pouvoir) :
        retire le dernier lancer de reussite (ou mixte) de l'historique et
        relance immediatement le de de reussite a sa place."""
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i]["type"] in ("success", "both"):
                self.history.pop(i)
                break
        return self.roll_success(note)

    def undo_last(self):
        if not self.history:
            return None
        record = self.history.pop()
        self.save()
        return record

    def clear_history(self):
        self.history = []
        self.next_id = 1
        self.save()

    def reset_all(self):
        """Remet TOUT a zero : historique, jauges totemiques, menace, quetes
        secondaires, valeurs autorisees, symbole/mode choisis, conversation
        avec l'IA, totems personnalises. Pratique pour repartir d'une
        aventure neuve apres un test. Utilise le roster de base (PIP_SYMBOLS)
        actuellement charge par l'histoire active -- ne change pas
        d'histoire en cours de route."""
        self.__init__()
        self.save()

    # ---------- affichage ----------
    def describe_record(self, record):
        parts = []
        if record["success"] is not None:
            parts.append(f"De de reussite : {record['success']} "
                          f"({SUCCESS_LABELS[record['success']]})")
        if record["fate"] is not None:
            face = FATE_BY_KEY[record["fate"]]
            parts.append(f"De du destin : {face['emoji']} {face['label']} "
                          f"- {face['desc']}")
        if record.get("threat_triggered"):
            parts.append("\u26a0\ufe0f La jauge de menace explose : une complication "
                          "secondaire inattendue survient !")
        if record.get("side_quest"):
            kind = record["side_quest"]["kind"]
            kind_txt = "se faire un nouvel ami" if kind == "ami" else "trouver un nouvel objet (ou totem)"
            parts.append(f"\U0001f4dc Nouvelle quete secondaire disponible : {kind_txt}.")
        return "\n".join(parts)

    def history_text(self):
        if not self.history:
            return "(aucun lancer pour l'instant)"
        lines = []
        for r in self.history:
            prefix = f"#{r['id']}"
            if r.get("note"):
                prefix += f" [{r['note']}]"
            bits = []
            if r["success"] is not None:
                bits.append(f"Reussite={r['success']}")
            if r["fate"] is not None:
                face = FATE_BY_KEY[r["fate"]]
                bits.append(f"Destin={face['emoji']} {face['label']}")
            lines.append(f"{prefix} : " + " | ".join(bits))
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Menu console (utilisable directement, sans l'interface web)
# ----------------------------------------------------------------------

def main():
    s = DiceSession()
    if s.load():
        print(f"Session chargee ({len(s.history)} lancers deja enregistres).")
    while True:
        print("""
==================== MENU ====================
1. Lancer le de de REUSSITE seul
2. Lancer le de du DESTIN seul
3. Lancer les DEUX des ensemble
4. Voir l'historique complet
5. Annuler le dernier lancer
6. Effacer tout l'historique
0. Quitter
================================================""")
        choice = input("Choix : ").strip()
        note = ""
        if choice in ("1", "2", "3"):
            note = input("Note sur l'action (optionnel, Entree pour ignorer) : ").strip()
        if choice == "1":
            r = s.roll_success(note)
            print(f"\n>>> {r['success']} - {SUCCESS_LABELS[r['success']]}")
        elif choice == "2":
            r = s.roll_fate(note)
            face = FATE_BY_KEY[r["fate"]]
            print(f"\n>>> {face['emoji']} {face['label']} - {face['desc']}")
        elif choice == "3":
            r = s.roll_both(note)
            print(f"\n{s.describe_record(r)}")
        elif choice == "4":
            print("\n" + s.history_text())
        elif choice == "5":
            r = s.undo_last()
            print("Dernier lancer annule." if r else "Rien a annuler.")
        elif choice == "6":
            s.clear_history()
            print("Historique efface.")
        elif choice == "0":
            print("A bientot pour la suite de l'aventure !")
            break
        else:
            print("Choix invalide.")


if __name__ == "__main__":
    main()
