# -*- coding: utf-8 -*-
"""
STORIES - registre des histoires disponibles dans l'application
=================================================================
L'application ne contient plus aucune histoire codee en dur : elle
demarre completement vide, avec pour seul choix, au premier lancement,
la creation d'une nouvelle histoire depuis l'ecran de selection ("Nouvelle
histoire"). Chaque histoire ainsi creee partage le meme moteur de jeu
(dice_engine.py) et la meme interface (dice_web.py) -- tout ce qui la
distingue (image de fond, sauvegarde, totem de depart, texte d'univers
envoye a l'IA) est stocke a part, via le mecanisme CUSTOM_STORIES_FILE
ci-dessous.

La cle API Mistral, elle, N'EST PAS ici : elle est partagee entre toutes
les histoires (voir app_config.json / load_app_config() dans dice_web.py).

STORIES reste un dict (vide) pour que tout le reste du code (all_stories,
switch_story, etc.) continue de fonctionner exactement comme avant, sans
distinction entre "histoire integree" et "histoire personnalisee" cote
appelant.
"""

import base64
import json
import os
import re

import image_utils

STORIES = {}
STORY_ORDER = []

# ---------------------------------------------------------------------
# Histoires creees a la volee depuis l'application (page "Nouvelle
# histoire" du selecteur) -- seule facon de peupler l'application,
# puisqu'aucune histoire n'est plus codee en dur (voir STORIES ci-dessus,
# volontairement vide). Une histoire personnalisee est
# decrite par un simple fichier JSON (CUSTOM_STORIES_FILE) + son image de
# fond enregistree a part sur le disque (CUSTOM_STORY_BG_DIR) -- tout est
# relatif au repertoire de travail courant, comme dice_state*.json ou
# app_config.json (voir dice_web.py). L'image du premier totem, elle,
# passe par le mecanisme EXISTANT des totems ajoutes en cours de partie
# (totem_images/, voir _save_totem_image() dans dice_web.py) : seul son
# nom de fichier est garde ici, et c'est switch_story() qui l'ajoute comme
# totem de depart (avec sa propre jauge) au tout premier lancement de
# cette histoire.
# ---------------------------------------------------------------------

CUSTOM_STORIES_FILE = "custom_stories.json"
CUSTOM_STORY_BG_DIR = "custom_story_bg"
ALLOWED_BG_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}


def _slugify_story_title(title):
    base = re.sub(r"[^a-z0-9]+", "_", (title or "").strip().lower()).strip("_")
    base = base or "histoire"
    existing = set(STORIES.keys()) | {m["slug"] for m in _load_custom_meta()}
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}_{n}"
        n += 1
    return slug


def _load_custom_meta():
    """Liste des histoires personnalisees, dans leur ordre de creation.
    Chaque entree est un petit dict de metadonnees (pas encore l'image de
    fond decodee -- voir _build_story_entry pour ca)."""
    if os.path.exists(CUSTOM_STORIES_FILE):
        try:
            with open(CUSTOM_STORIES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [m for m in data if isinstance(m, dict) and m.get("slug")]
        except (OSError, ValueError):
            pass
    return []


def _save_custom_meta(meta_list):
    with open(CUSTOM_STORIES_FILE, "w", encoding="utf-8") as f:
        json.dump(meta_list, f, ensure_ascii=False, indent=2)


def create_custom_story(title, subtitle, lore_text, bg_image_bytes, bg_image_ext,
                         totem_label, totem_image_filename,
                         totem_powers="", totem_special=""):
    """Cree une nouvelle histoire : enregistre son image de fond sur le
    disque et ajoute une entree dans CUSTOM_STORIES_FILE. Renvoie le slug
    attribue (utilise ensuite par dice_web.switch_story() pour y basculer
    immediatement).

    totem_powers / totem_special sont optionnels et suivent exactement le
    meme format que pour un totem ajoute en cours de partie (voir
    /add_custom_totem dans dice_web.py) : powers_text est une chaine de
    pouvoirs separes par des virgules, special est une capacite unique en
    texte libre."""
    title = (title or "").strip() or "Nouvelle histoire"
    slug = _slugify_story_title(title)

    os.makedirs(CUSTOM_STORY_BG_DIR, exist_ok=True)
    # Redimensionnee/recompressee ici (meme mecanique que pour Animorph et
    # Poudlard ci-dessus) : une photo de telephone non retouchee etait la
    # cause du "trop grosse pour etre visible" sur les histoires creees
    # depuis l'application. Toujours reencodee en JPEG par ce traitement.
    bg_image_bytes = image_utils.resize_bg_bytes(bg_image_bytes)
    ext = "jpg"
    bg_filename = f"{slug}.{ext}"
    with open(os.path.join(CUSTOM_STORY_BG_DIR, bg_filename), "wb") as f:
        f.write(bg_image_bytes)

    meta = {
        "slug": slug,
        "title": title,
        "subtitle": (subtitle or "").strip(),
        "lore_text": (lore_text or "").strip(),
        "bg_image_file": bg_filename,
        "totem_label": (totem_label or "Totem de depart").strip(),
        "totem_image_filename": totem_image_filename,
        "totem_powers": (totem_powers or "").strip(),
        "totem_special": (totem_special or "").strip(),
        "save_file": f"dice_state_{slug}.json",
    }
    meta_list = _load_custom_meta()
    meta_list.append(meta)
    _save_custom_meta(meta_list)
    return slug


def delete_custom_story(slug):
    """Supprime definitivement une histoire personnalisee : son entree
    dans CUSTOM_STORIES_FILE, son image de fond, sa sauvegarde de partie
    (dice_state_<slug>.json) et l'image de son totem de depart si elle en
    avait une.

    Ne touche JAMAIS aux histoires integrees (Animorph, Poudlard) : si le
    slug fourni ne correspond a aucune histoire personnalisee (par
    exemple parce que c'est une histoire integree, ou un slug inconnu),
    ne fait rien et renvoie False. Renvoie True si une histoire a bien
    ete supprimee."""
    meta_list = _load_custom_meta()
    meta = next((m for m in meta_list if m["slug"] == slug), None)
    if meta is None:
        return False

    meta_list = [m for m in meta_list if m["slug"] != slug]
    _save_custom_meta(meta_list)

    bg_file = meta.get("bg_image_file")
    if bg_file:
        bg_path = os.path.join(CUSTOM_STORY_BG_DIR, bg_file)
        if os.path.exists(bg_path):
            os.remove(bg_path)

    save_file = meta.get("save_file")
    if save_file and os.path.exists(save_file):
        os.remove(save_file)

    # L'image du totem de depart est enregistree dans totem_images/ (voir
    # _save_totem_image() dans dice_web.py), pas dans CUSTOM_STORY_BG_DIR --
    # on la supprime aussi, puisqu'elle a ete creee specifiquement pour le
    # totem de depart de CETTE histoire (jamais partagee avec une autre).
    totem_image_filename = meta.get("totem_image_filename")
    if totem_image_filename:
        totem_image_path = os.path.join("totem_images", totem_image_filename)
        if os.path.exists(totem_image_path):
            os.remove(totem_image_path)

    return True


def is_custom_story(slug):
    """Vrai si slug correspond a une histoire personnalisee (creee depuis
    l'application), donc supprimable -- faux pour les histoires
    integrees (Animorph, Poudlard) ou un slug inconnu."""
    return any(m["slug"] == slug for m in _load_custom_meta())


def _build_story_entry(meta):
    """Reconstruit une entree au meme format que celles de STORIES a
    partir des metadonnees d'une histoire personnalisee -- appele a
    chaque fois qu'on a besoin de la liste complete des histoires (voir
    all_stories()), jamais mis en cache : l'image de fond est relue et
    reencodee a chaque fois, mais ca ne se produit qu'au chargement d'une
    page (selecteur, changement d'histoire), jamais a chaque requete de
    jeu."""
    bg_b64 = ""
    bg_path = os.path.join(CUSTOM_STORY_BG_DIR, meta.get("bg_image_file", ""))
    if meta.get("bg_image_file") and os.path.exists(bg_path):
        with open(bg_path, "rb") as f:
            bg_b64 = base64.b64encode(f.read()).decode("ascii")

    lore_text = meta.get("lore_text") or ""

    return {
        "slug": meta["slug"],
        "title": meta.get("title") or meta["slug"],
        "header_title": meta.get("title") or meta["slug"],
        "subtitle": meta.get("subtitle") or "",
        "save_file": meta.get("save_file") or f"dice_state_{meta['slug']}.json",
        "bg_image_b64": bg_b64,
        "thumbnail_b64": None,
        "pip_symbols": {},
        "totems": [],
        "default_pip_symbol": "",
        "protagonist_ref": "le personnage principal",
        "fixed_allies_line": "",
        "ally_help_text": {},
        "is_custom": True,
        # La description d'univers fournie a la creation remplace, pour
        # cette histoire, tout ce qui concernait l'univers des autres
        # histoires (Marvel/Animorph, Harry Potter/Poudlard...) -- elle
        # s'ajoute simplement a la mecanique de jeu commune (des, jauges,
        # menace...) deja envoyee a l'IA, comme le fait lore_paragraphs
        # pour les histoires integrees.
        "lore_paragraphs": [lore_text] if lore_text else [],
        "seed_state_file": None,
        # Totem de depart (voir switch_story() dans dice_web.py) : ajoute
        # automatiquement comme totem personnalise au tout premier
        # lancement de cette histoire, avec sa propre image comme
        # "constellation" affichee sur le de de reussite.
        "default_totem": {
            "label": meta.get("totem_label") or "Totem de depart",
            "image_filename": meta.get("totem_image_filename"),
            "powers_text": meta.get("totem_powers") or "",
            "special": meta.get("totem_special") or "",
        },
    }


def all_stories():
    """Fusionne les histoires integrees (STORIES, codees en dur) et
    celles creees par le joueur depuis l'application. A utiliser partout
    ou stories.STORIES etait utilise directement pour lister/retrouver
    une histoire."""
    merged = dict(STORIES)
    for meta in _load_custom_meta():
        merged[meta["slug"]] = _build_story_entry(meta)
    return merged


def all_story_order():
    """Comme STORY_ORDER, mais en y ajoutant les histoires personnalisees
    a la suite, dans leur ordre de creation."""
    return STORY_ORDER + [m["slug"] for m in _load_custom_meta()]
