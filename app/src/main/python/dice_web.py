#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INTERFACE WEB - Des de l'aventure
===================================
A lancer avec Pydroid 3, en gardant dice_engine.py dans le MEME dossier.

Installation (une seule fois, si pas deja fait pour le poker) :
    pip install flask

Lancement :
    Executez ce fichier (bouton Play dans Pydroid 3).
    Puis ouvrez votre navigateur sur : http://127.0.0.1:5001
"""

import json
import math
import os
import random
import shutil
import uuid
from flask import Flask, request, redirect, url_for, render_template_string, jsonify, send_from_directory

import dice_engine
from dice_engine import (DiceSession, FATE_FACES, FATE_BY_KEY, SUCCESS_LABELS,
                          PIP_SYMBOLS, TOTEM_ENERGY_THRESHOLD, THREAT_THRESHOLD)
import image_utils
import mistral_client
import stories
from bg_key_page_data import BG_IMAGE_B64 as KEY_PAGE_BG_B64
from bg_classic_dice_data import BG_IMAGE_B64 as CLASSIC_DICE_BG_B64
from dice_icon_data import ICON_B64 as CLASSIC_DICE_ICON_B64

# Note : contrairement aux images de fond des histoires (voir stories.py),
# cette image n'est PAS passee par image_utils.resize_bg_b64() -- elle
# n'a pas besoin d'etre mise au format "portrait" puisqu'elle est affichee
# en bandeau ("hero") en haut de la page, decoupee par le CSS
# (object-fit:cover), pas en fond plein ecran. Elle est deja redimensionnee
# a une taille raisonnable directement dans bg_key_page_data.py.

app = Flask(__name__)
# Limite la taille des requetes (en pratique : les images de totems
# uploadees) a 5 Mo -- raisonnable sur mobile (stockage/memoire limites).
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


@app.before_request
def _ensure_story_selected():
    """Garde-fou : si une route de jeu (lancer, jauges...) est appelee
    alors qu'aucune histoire n'est encore active (session is None), on
    redirige vers la page de choix au lieu de planter. Ne devrait
    normalement jamais arriver en usage normal (l'interface de jeu n'est
    meme pas affichee tant qu'une histoire n'est pas choisie), mais reste
    une securite peu couteuse."""
    if session is None and request.endpoint not in (
        "index", "select_story", "change_story",
        "create_story_form", "do_create_story",
        "debug_images",
        "configure_key_page", "do_configure_key", "skip_key_page", "do_set_model",
        "do_delete_story", "classic_dice_page", "do_classic_dice_roll",
        "do_classic_dice_clear",
    ):
        return redirect(url_for("index"))


# Dossier ou sont sauvegardees les images de totems ajoutees par le joueur,
# relatif au repertoire de travail (le meme que dice_state.json). Cree a la
# demande, seulement quand une premiere image est effectivement envoyee.
TOTEM_IMAGES_DIR = "totem_images"
ALLOWED_TOTEM_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}

# Fichier de config au niveau de l'APPLICATION (pas d'une histoire en
# particulier) : pour l'instant, seulement la cle API Mistral, partagee
# entre toutes les histoires (un seul compte gratuit suffit largement).
APP_CONFIG_FILE = "app_config.json"

# Sauvegarde de la page "Des classiques" (voir plus bas) : totalement
# independante des histoires et de dice_engine -- juste un historique de
# lancers du de de reussite (1-6, points classiques) et/ou du de du destin
# (6 symboles fixes, voir FATE_FACES), pour un usage en partie papier. Un
# seul fichier partage, comme la cle API, puisque cette page n'appartient
# a aucune histoire en particulier.
CLASSIC_DICE_STATE_FILE = "classic_dice_state.json"
CLASSIC_DICE_MAX_HISTORY = 30  # au-dela, les lancers les plus anciens sont oublies


def load_classic_dice_state():
    if os.path.exists(CLASSIC_DICE_STATE_FILE):
        try:
            with open(CLASSIC_DICE_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("history"), list):
                return data
        except (OSError, ValueError):
            pass
    return {"history": [], "next_id": 1}


def save_classic_dice_state(state):
    with open(CLASSIC_DICE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def roll_classic_dice(kind):
    """Lance le de de reussite (kind="success"), le de du destin
    (kind="fate"), ou les deux ensemble (kind="both"), et enregistre le
    resultat dans l'historique (voir CLASSIC_DICE_STATE_FILE), en gardant
    au plus CLASSIC_DICE_MAX_HISTORY entrees. Renvoie l'entree creee."""
    state = load_classic_dice_state()
    entry = {
        "id": state["next_id"],
        "success": random.randint(1, 6) if kind in ("success", "both") else None,
        "fate": random.choice(FATE_FACES)["key"] if kind in ("fate", "both") else None,
    }
    state["next_id"] += 1
    state["history"].append(entry)
    state["history"] = state["history"][-CLASSIC_DICE_MAX_HISTORY:]
    save_classic_dice_state(state)
    return entry


def _last_classic_dice_values(history):
    """Parcourt l'historique a l'envers pour retrouver la derniere valeur
    du de de reussite et la derniere du de du destin, meme si elles n'ont
    pas ete tirees lors du meme lancer (ex: on relance seulement le de du
    destin -- le de de reussite doit rester affiche avec sa valeur
    precedente)."""
    last_success, last_fate = None, None
    for entry in reversed(history):
        if last_success is None and entry.get("success") is not None:
            last_success = entry["success"]
        if last_fate is None and entry.get("fate") is not None:
            last_fate = entry["fate"]
        if last_success is not None and last_fate is not None:
            break
    return last_success, last_fate


def clear_classic_dice_history():
    save_classic_dice_state({"history": [], "next_id": 1})


def load_app_config():
    if os.path.exists(APP_CONFIG_FILE):
        try:
            with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    "mistral_api_key": str(data.get("mistral_api_key") or ""),
                    "mistral_model": str(data.get("mistral_model") or mistral_client.DEFAULT_MODEL),
                }
        except (OSError, ValueError):
            pass
    return {"mistral_api_key": "", "mistral_model": mistral_client.DEFAULT_MODEL}


def save_app_config(config):
    with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_mistral_key():
    return load_app_config()["mistral_api_key"]


def set_mistral_key(key):
    config = load_app_config()
    config["mistral_api_key"] = (key or "").strip()
    save_app_config(config)


def clear_mistral_key():
    config = load_app_config()
    config["mistral_api_key"] = ""
    save_app_config(config)


def has_mistral_key():
    return bool(get_mistral_key())


def get_mistral_model():
    return load_app_config()["mistral_model"]


def set_mistral_model(model):
    config = load_app_config()
    config["mistral_model"] = (model or "").strip() or mistral_client.DEFAULT_MODEL
    save_app_config(config)


# ---------------------------------------------------------------------
# Histoire active : rien n'est charge tant qu'aucune histoire n'a ete
# choisie (voir switch_story() et la page d'accueil / selecteur). PIP_SYMBOLS
# (dice_engine) et TOTEMS (plus bas dans ce fichier) sont des conteneurs
# MUTABLES vides au demarrage, remplis par switch_story() -- jamais
# reassignes en un nouvel objet, pour que tout le code qui les reference
# (y compris via un import qui a garde son propre nom local) voie toujours
# leur contenu a jour.
# ---------------------------------------------------------------------
CURRENT_STORY = None       # slug de l'histoire active (ou None)
CURRENT_STORY_CONFIG = {}  # entree stories.STORIES courante
session = None             # DiceSession active (creee par switch_story())

# Vrai des que la page de configuration de la cle API a ete affichee une
# premiere fois lors de ce lancement de l'appli -- evite qu'elle
# reapparaisse a chaque retour sur "/" une fois qu'on l'a deja vue (elle
# reste neanmoins accessible a tout moment via son propre lien/route).
KEY_PAGE_SEEN = False


def switch_story(slug):
    """Bascule l'application entiere sur l'histoire demandee : recharge sa
    sauvegarde (creee vide si premiere fois), son roster de totems de
    depart, son image de fond, et son contexte specifique pour l'IA.
    La cle Mistral (partagee entre histoires) n'est jamais touchee ici.

    Appelable a tout moment (pas seulement au demarrage) : l'histoire
    quittee garde sa propre sauvegarde intacte sur son propre fichier,
    on peut y revenir plus tard sans rien perdre."""
    global CURRENT_STORY, CURRENT_STORY_CONFIG, session

    story = stories.all_stories().get(slug)
    if not story:
        return False

    # Roster de totems de depart : mutation EN PLACE (jamais de
    # reassignation d'un nouvel objet) pour que toutes les references
    # deja prises ailleurs (imports compris) restent a jour.
    dice_engine.PIP_SYMBOLS.clear()
    dice_engine.PIP_SYMBOLS.update(story["pip_symbols"])
    dice_engine.DEFAULT_PIP_SYMBOL = story["default_pip_symbol"]
    dice_engine.SAVE_FILE = story["save_file"]

    TOTEMS[:] = story["totems"]

    CURRENT_STORY = slug
    CURRENT_STORY_CONFIG = story

    _rebuild_totem_derived_globals()
    _rebuild_base_css()

    # Partie de depart fournie (Android : seed copie au tout premier
    # lancement) -- uniquement si cette histoire en definit une ET que sa
    # sauvegarde n'existe pas encore (ne jamais ecraser une partie en cours).
    seed_file = story.get("seed_state_file")
    if seed_file and not os.path.exists(dice_engine.SAVE_FILE) and os.path.exists(seed_file):
        shutil.copyfile(seed_file, dice_engine.SAVE_FILE)

    new_session = DiceSession()
    was_loaded = new_session.load()

    # Totem de depart d'une histoire personnalisee (voir stories.py /
    # create_custom_story) : ajoute UNIQUEMENT au tout premier lancement
    # de cette histoire (pas de sauvegarde existante), jamais rejoue au
    # rechargement d'une partie en cours -- sinon on le dupliquerait a
    # chaque fois qu'on revient sur cette histoire.
    default_totem = story.get("default_totem")
    if not was_loaded and default_totem and default_totem.get("label"):
        key = new_session.add_custom_totem(
            default_totem["label"],
            powers_text=default_totem.get("powers_text", ""),
            special=default_totem.get("special", ""),
            image_filename=default_totem.get("image_filename"),
        )
        if key:
            new_session.pip_symbol = key
            new_session.save()

    session = new_session
    return True

PIP_POSITIONS = {
    1: [(2, 2)],
    2: [(1, 1), (3, 3)],
    3: [(1, 1), (2, 2), (3, 3)],
    4: [(1, 1), (1, 3), (3, 1), (3, 3)],
    5: [(1, 1), (1, 3), (2, 2), (3, 1), (3, 3)],
    6: [(1, 1), (1, 3), (2, 1), (2, 3), (3, 1), (3, 3)],
}

BASE_CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Bangers&family=Nunito:wght@400;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#14161a; --paper:#fbf3e1; --red:#e0263c; --blue:#1d3fd6;
    --yellow:#ffcd3c; --purple:#6a4c93; --line:rgba(20,22,26,0.15);
    /* Couleurs du badge-embleme (empreinte + A). Modifiez ces 4 lignes
       pour recolorer le badge a votre gout. */
    --emblem-ring:#ffcd3c; --emblem-bg:#e0263c;
    --emblem-fg:#ffcd3c; --emblem-letter:#14161a;
  }
  *{box-sizing:border-box;}
  html,body{margin:0; padding:0;}
  body{
    color:#fff; font-family:'Nunito',-apple-system,sans-serif;
    padding:16px; padding-bottom:60px;
    background-color:#2a2118;
    background-image:
      radial-gradient(circle, rgba(20,22,26,0.08) 1.1px, transparent 1.1px),
      linear-gradient(180deg, rgba(20,18,15,0.15) 0%, rgba(20,18,15,0.35) 60%, rgba(20,18,15,0.5) 100%),
      url('data:image/jpeg;base64,__BG_IMAGE_B64__');
    background-size: 14px 14px, auto, cover;
    background-position: 0 0, 0 0, center center;
    background-repeat: repeat, no-repeat, no-repeat;
    background-attachment: scroll, scroll, scroll;
  }
  .totem-row{display:flex; gap:8px; margin:6px 0 4px 0; flex-wrap:wrap; justify-content:center;}
  .totem-badge{
    width:36px; height:36px; border-radius:50%; background:#fffdf7;
    border:2.5px solid var(--ink); display:flex; align-items:center; justify-content:center;
    font-size:1.2rem; box-shadow:2px 2px 0 var(--ink);
    cursor:pointer; transition:transform 0.1s;
  }
  .totem-badge:active{transform:scale(0.9);}
  .totem-badge.symbol-active{background:var(--yellow); box-shadow:2px 2px 0 var(--ink), 0 0 0 3px var(--red);}
  .totem-badge.big{width:50px; height:50px; font-size:1.5rem;}
  .totem-badge.mode-active{background:var(--yellow); box-shadow:2px 2px 0 var(--ink), 0 0 0 3px var(--blue);}
  .totem-badge.symbol-excluded{opacity:0.3; filter:grayscale(0.7);}
  .pip-shield{display:inline-block; width:var(--pip-size, 1.4rem); height:var(--pip-size, 1.4rem);}
  .pip-shield.badge-size{width:1.15rem; height:1.15rem;}
  .pip-araignee{display:inline-block; width:var(--pip-size, 1.4rem); height:var(--pip-size, 1.4rem);}
  .pip-araignee.badge-size{width:1.15rem; height:1.15rem;}
  .totem-modal-overlay{
    display:none; position:fixed; inset:0; z-index:50;
    background:rgba(10,10,10,0.72); align-items:center; justify-content:center;
    padding:20px;
  }
  .totem-modal-box{
    background:#fffdf7; color:var(--ink); border:4px solid var(--ink); border-radius:14px;
    padding:20px; max-width:340px; width:100%; box-shadow:6px 6px 0 var(--ink);
    text-align:center; max-height:80vh; overflow-y:auto;
  }
  .totem-modal-icon{font-size:3rem; line-height:1;}
  .totem-modal-box h2{color:var(--blue); margin:6px 0 10px 0; text-shadow:none;}
  .totem-modal-box ul{text-align:left; padding-left:22px; margin:0 0 12px 0;}
  .totem-modal-box li{margin-bottom:4px; font-weight:700; font-size:0.95rem;}
  .totem-modal-special{
    background:var(--yellow); border:2px solid var(--ink); border-radius:8px;
    padding:8px 10px; font-weight:700; font-size:0.88rem; text-align:left; margin-bottom:14px;
  }
  .header-block{text-align:center; margin-bottom:6px;}
  h1{
    font-family:'Anton',sans-serif; letter-spacing:1px; font-weight:400;
    font-size:2.3rem; margin:6px 0 2px 0; color:#fff; text-transform:uppercase;
    -webkit-text-stroke:1.5px var(--ink); text-stroke:1.5px var(--ink);
    text-shadow:3px 3px 0 var(--red), 3px 3px 0 var(--ink);
    line-height:1.05;
  }
  h2{
    font-family:'Bangers',cursive; font-weight:400; letter-spacing:0.5px;
    font-size:1.3rem; margin:18px 0 10px 0; color:var(--yellow);
    text-shadow:2px 2px 0 var(--ink), -1px -1px 0 var(--ink), 1px -1px 0 var(--ink), -1px 1px 0 var(--ink);
  }
  .sub{
    color:#fff; opacity:0.9; font-size:0.9rem; margin-bottom:16px; font-weight:700;
    text-shadow:1px 1px 3px rgba(0,0,0,0.6); text-align:center;
  }
  .card{
    background:transparent; border:none; border-radius:0;
    padding:12px 4px; margin-bottom:6px; box-shadow:none;
  }
  .card p{color:#fff; text-shadow:1px 1px 3px rgba(0,0,0,0.75);}
  .dice-row{display:flex; gap:16px; justify-content:center; flex-wrap:wrap; margin:10px 0;}
  .die-box{
    width:150px; height:150px; background:#fff; border:4px solid var(--ink);
    border-radius:14px; box-shadow:5px 5px 0 var(--ink);
    display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:repeat(3,1fr);
    padding:10px; transform:rotate(-1deg); overflow:hidden;
  }
  .die-box.fate{
    display:flex; align-items:center; justify-content:center; flex-direction:column;
    transform:rotate(1deg); background:#fff7e0;
  }
  .die-box.die-dimmed{opacity:0.35; filter:grayscale(0.7); transition:opacity 0.2s, filter 0.2s;}
  .pip{display:flex; align-items:center; justify-content:center; font-size:var(--pip-size, 1.6rem); line-height:1; overflow:hidden;}
  .pip.pip-single{grid-column:1 / -1; grid-row:1 / -1;}
  .fate-emoji{font-size:3.6rem; line-height:1;}
  .fate-label{font-family:'Bangers',cursive; font-size:1rem; color:var(--purple); margin-top:4px; text-align:center;}
  .emblem-placeholder{
    grid-column:1 / -1; grid-row:1 / -1;
    width:100%; height:100%; display:flex; align-items:center; justify-content:center;
  }
  .emblem-placeholder svg{width:72%; height:72%; opacity:0.7;}
  .die-caption{
    text-align:center; font-family:'Bangers',cursive; font-size:1.1rem; margin-top:6px;
    color:#fff; text-shadow:1px 1px 3px rgba(0,0,0,0.8);
  }
  .result-text{
    background:var(--yellow); border:3px solid var(--ink); border-radius:10px;
    padding:10px 14px; margin-top:12px; font-weight:800; text-align:center;
    box-shadow:3px 3px 0 var(--ink); color:var(--ink);
  }
  label{
    font-size:0.85rem; font-weight:700; color:#fff; opacity:0.9;
    text-shadow:1px 1px 3px rgba(0,0,0,0.7); display:block;
  }
  input,textarea{
    width:100%; padding:9px; margin:5px 0 12px 0; border-radius:6px;
    border:2px solid var(--ink); background:#fff; color:var(--ink);
    font-size:0.95rem; font-family:'Nunito',sans-serif;
  }
  button, .btn{
    background:var(--red); color:#fff; border:3px solid var(--ink); border-radius:10px;
    padding:12px 18px; font-family:'Bangers',cursive; font-size:1.1rem; letter-spacing:0.5px;
    cursor:pointer; margin:4px 6px 4px 0; display:inline-block; text-decoration:none;
    box-shadow:3px 3px 0 var(--ink); transition:transform 0.05s;
  }
  button:active, .btn:active{transform:translate(2px,2px); box-shadow:1px 1px 0 var(--ink);}
  button.blue{background:var(--blue);}
  button.purple{background:var(--purple);}
  button.secondary{background:#fff; color:var(--ink);}
  button.danger{background:#8a8a8a;}
  .history-item{
    border-bottom:1px dashed rgba(255,255,255,0.3); padding:8px 0; font-size:0.92rem;
    color:#fff; text-shadow:1px 1px 3px rgba(0,0,0,0.75);
  }
  .history-item:last-child{border-bottom:none;}
  .history-note{font-style:italic; opacity:0.85;}
  textarea.copybox{font-family:monospace; font-size:0.8rem; min-height:200px;}
  .copy-wrap{position:relative;}
  button.small{
    padding:5px 10px; font-size:0.8rem; margin:0 0 0 8px; border-width:2px;
    box-shadow:2px 2px 0 var(--ink);
  }
  .totem-gauge-row{
    display:flex; align-items:center; gap:8px; margin:4px 0;
  }
  .totem-gauge-icon{font-size:1.1rem; width:22px; text-align:center;}
  .totem-gauge-track{
    flex:1; height:10px; background:rgba(255,255,255,0.25); border:1.5px solid var(--ink);
    border-radius:6px; overflow:hidden;
  }
  .totem-gauge-fill{
    height:100%; background:var(--blue); transition:width 0.25s;
  }
  .totem-gauge-fill.ready{background:var(--yellow);}
  .totem-gauge-fill.danger{background:var(--red);}
  .totem-gauge-val{
    font-size:0.75rem; color:#fff; opacity:0.85; min-width:38px; text-align:right;
    text-shadow:1px 1px 2px rgba(0,0,0,0.7);
  }
</style>
<script>
function copyBox(id, btnId){
  var el = document.getElementById(id);
  el.select(); el.setSelectionRange(0, 999999);
  navigator.clipboard.writeText(el.value).then(function(){
    var b = document.getElementById(btnId);
    var old = b.innerText; b.innerText = "Copie !";
    setTimeout(function(){ b.innerText = old; }, 1200);
  });
}
</script>
"""
BASE_CSS_TEMPLATE = BASE_CSS
# BASE_CSS (utilisee par le reste du fichier) est recalculee a chaque
# changement d'histoire par _rebuild_base_css(), a partir du template
# ci-dessus + l'image de fond de l'histoire active. Vide par defaut tant
# qu'aucune histoire n'est choisie (page de selection sans fond charge).
BASE_CSS = ""


def _rebuild_base_css():
    global BASE_CSS
    bg = (CURRENT_STORY_CONFIG or {}).get("bg_image_b64") or ""
    BASE_CSS = BASE_CSS_TEMPLATE.replace("__BG_IMAGE_B64__", bg)


EMBLEM_SVG = """<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <circle cx="50" cy="50" r="48" fill="var(--emblem-ring)"/>
  <circle cx="50" cy="50" r="41" fill="var(--emblem-bg)"/>
  <g fill="var(--emblem-fg)">
    <ellipse cx="50" cy="66" rx="19" ry="15"/>
    <ellipse cx="27" cy="42" rx="9" ry="12" transform="rotate(-25 27 42)"/>
    <ellipse cx="41" cy="26" rx="8.5" ry="11.5" transform="rotate(-8 41 26)"/>
    <ellipse cx="59" cy="26" rx="8.5" ry="11.5" transform="rotate(8 59 26)"/>
    <ellipse cx="73" cy="42" rx="9" ry="12" transform="rotate(25 73 42)"/>
  </g>
  <text x="50" y="66" text-anchor="middle" dominant-baseline="central"
        font-family="Anton, sans-serif"
        font-size="26" fill="var(--emblem-letter)">A</text>
</svg>"""


def render_bouclier_svg(cls):
    """Bouclier a la Captain America : anneaux concentriques rouge/blanc/
    rouge, cercle bleu, etoile blanche au centre dont les pointes
    atteignent le bord du cercle bleu."""
    star_points = ("50.0,33.0 53.8,44.7 66.2,44.7 56.2,52.0 60.0,63.8 "
                   "50.0,56.5 40.0,63.8 43.8,52.0 33.8,44.7 46.2,44.7")
    return f"""<svg class="{cls}" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
      <circle cx="50" cy="50" r="48" fill="var(--red)" stroke="rgba(0,0,0,0.35)" stroke-width="2"/>
      <circle cx="50" cy="50" r="37" fill="#fff"/>
      <circle cx="50" cy="50" r="27" fill="var(--red)"/>
      <circle cx="50" cy="50" r="17" fill="var(--blue)"/>
      <polygon points="{star_points}" fill="#fff"/>
    </svg>"""


_svg_uid_counter = 0


def _next_svg_uid():
    global _svg_uid_counter
    _svg_uid_counter += 1
    return _svg_uid_counter


def _tapered_polygon(points, widths):
    """Construit le contour (polygone rempli) d'une forme qui suit une
    ligne brisee (points) en s'affinant progressivement : chaque point a
    sa propre demi-largeur (widths). Une derniere largeur de 0 termine
    en pointe nette."""
    left, right = [], []
    n = len(points)
    for i, p in enumerate(points):
        w = widths[i]
        p_prev = points[i - 1] if i > 0 else points[i]
        p_next = points[i + 1] if i < n - 1 else points[i]
        dx, dy = p_next[0] - p_prev[0], p_next[1] - p_prev[1]
        length = math.hypot(dx, dy) or 1
        perp = (-dy / length, dx / length)
        left.append((p[0] + perp[0] * w, p[1] + perp[1] * w))
        right.append((p[0] - perp[0] * w, p[1] - perp[1] * w))
    outline = left + list(reversed(right))
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in outline)


def _leg_polygon(attach, knee, tip, w_base=2.8, w_mid=1.3):
    """Patte a un seul coude net (3 points), affinee jusqu'a une pointe."""
    return _tapered_polygon([attach, knee, tip], [w_base, w_mid, 0])


def _hooked_leg_polygon(attach, knee, hook, tip, w_base=2.6, w_knee=1.6, w_hook=0.7):
    """Patte a 2 coudes (4 points) : elle part du corps, s'ecarte largement
    vers le cote (knee), puis se replie ("hook") pour finir en pointe fine
    (tip) -- inspire des pattes anguleuses et crochues typiques des
    silhouettes d'araignees stylisees, sans copier un logo precis."""
    return _tapered_polygon([attach, knee, hook, tip], [w_base, w_knee, w_hook, 0])


def render_araignee_svg(cls):
    """Araignee noire, silhouette fidele au dessin original de l'utilisateur
    (contour extrait puis mis a l'echelle pour tenir entierement, tete en
    haut, a l'interieur d'un ecu (blason) francais ancien rouge tisse d'une
    toile d'araignee)."""
    shield_path = "M15,8 L85,8 L85,45 C85,70 68,85 50,97 C32,85 15,70 15,45 Z"
    clip_id = f"shieldClip-{_next_svg_uid()}"
    web_stroke = 'stroke="rgba(255,255,255,0.55)" stroke-width="1" fill="none"'
    spider_fill = "var(--ink)"
    spider_points = (
        "74.95,9.50 65.80,12.93 59.01,19.51 59.22,12.00 57.01,10.00 "
        "56.65,16.79 55.01,13.43 53.79,15.36 50.14,13.79 48.71,15.79 "
        "49.07,10.79 47.64,10.29 46.14,12.43 47.21,16.86 44.99,19.58 "
        "39.85,13.29 33.05,10.00 26.12,10.93 26.19,12.65 31.91,11.86 "
        "39.63,15.79 43.92,22.80 31.62,19.15 18.47,20.08 16.68,28.45 "
        "20.97,21.44 32.70,21.30 43.71,24.66 43.64,25.95 32.63,29.16 "
        "22.33,43.53 22.90,54.76 24.19,43.82 33.63,30.81 44.14,27.73 "
        "33.63,36.60 32.12,61.05 36.84,73.71 38.06,72.63 33.91,60.91 "
        "35.27,37.24 44.85,30.09 45.85,31.59 41.49,36.88 38.92,45.39 "
        "39.35,53.76 42.06,60.62 46.78,65.63 53.50,67.41 58.94,65.48 "
        "63.87,59.91 66.44,52.19 66.44,44.32 64.01,36.88 58.87,30.16 "
        "67.23,34.88 71.24,62.70 67.02,73.78 68.59,74.42 73.38,62.20 "
        "68.88,34.31 59.51,28.73 59.80,27.30 69.23,30.02 79.10,43.82 "
        "80.39,52.90 80.75,42.82 70.95,28.73 60.37,25.59 60.01,23.59 "
        "79.74,22.51 81.67,26.95 83.32,26.52 80.67,20.87 59.58,21.37 "
        "66.37,14.72 74.31,11.64 78.10,14.58"
    )

    return f"""<svg class="{cls}" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <clipPath id="{clip_id}">
          <path d="{shield_path}"/>
        </clipPath>
      </defs>
      <path d="{shield_path}" fill="var(--red)" stroke="rgba(0,0,0,0.4)" stroke-width="2"/>
      <g clip-path="url(#{clip_id})">
        <line x1="50" y1="50" x2="110.0" y2="50.0" {web_stroke}/>
        <line x1="50" y1="50" x2="92.4" y2="92.4" {web_stroke}/>
        <line x1="50" y1="50" x2="50.0" y2="110.0" {web_stroke}/>
        <line x1="50" y1="50" x2="7.6" y2="92.4" {web_stroke}/>
        <line x1="50" y1="50" x2="-10.0" y2="50.0" {web_stroke}/>
        <line x1="50" y1="50" x2="7.6" y2="7.6" {web_stroke}/>
        <line x1="50" y1="50" x2="50.0" y2="-10.0" {web_stroke}/>
        <line x1="50" y1="50" x2="92.4" y2="7.6" {web_stroke}/>
        <polygon points="64.0,50.0 59.9,59.9 50.0,64.0 40.1,59.9 36.0,50.0 40.1,40.1 50.0,36.0 59.9,40.1" {web_stroke}/>
        <polygon points="74.0,50.0 67.0,67.0 50.0,74.0 33.0,67.0 26.0,50.0 33.0,33.0 50.0,26.0 67.0,33.0" {web_stroke}/>
        <polygon points="84.0,50.0 74.0,74.0 50.0,84.0 26.0,74.0 16.0,50.0 26.0,26.0 50.0,16.0 74.0,26.0" {web_stroke}/>
      </g>
      <polygon points="{spider_points}" fill="{spider_fill}"/>
    </svg>"""


def render_pip_symbol(pip_symbol_key, badge=False):
    """Rendu d'un symbole de pip. Cas speciaux pour le bouclier et
    l'araignee : dessines en CSS/SVG (couleurs rouge/bleu) plutot qu'en
    emoji (les emojis ne peuvent pas etre recolores). Pour un totem
    ajoute par le joueur avec sa propre image, affiche cette image ;
    sinon utilise son emoji (ou celui de base pour les symboles fixes)."""
    if pip_symbol_key == "bouclier":
        cls = "pip-shield badge-size" if badge else "pip-shield"
        return render_bouclier_svg(cls)
    if pip_symbol_key == "araignee":
        cls = "pip-araignee badge-size" if badge else "pip-araignee"
        return render_araignee_svg(cls)
    info = session.all_symbols().get(pip_symbol_key)
    if info and info.get("image"):
        size = "1.15rem" if badge else "1em"
        return (f'<img src="{url_for("totem_image", filename=info["image"])}" '
                f'alt="{info["label"]}" style="width:{size}; height:{size}; '
                f'object-fit:contain; vertical-align:-0.15em;">')
    if info:
        return info["emoji"] or "\u26ab"
    return "\u26ab"  # aucun symbole choisi / totem inexistant -> pip generique (pas de crash)


PIP_SIZE_BY_COUNT = {
    1: "5.0rem", 2: "2.4rem", 3: "2.0rem",
    4: "1.8rem", 5: "1.6rem", 6: "1.4rem",
}


def render_success_die(value, pip_choice, used=True):
    """pip_choice : liste des symboles a utiliser, un par pip (meme taille
    que le nombre de pips de la valeur). Permet le mode fixe (meme symbole
    partout), aleatoire (un symbole tire au sort pour toute la face) ou
    mixe (un symbole different par pip)."""
    used_cls = "" if used else " die-dimmed"
    if value is None:
        return (f'<div class="die-box{used_cls}" id="success-die-box">'
                f'<div class="emblem-placeholder">{EMBLEM_SVG}</div></div>')
    pips = PIP_POSITIONS[value]
    size = PIP_SIZE_BY_COUNT.get(value, "1.6rem")
    if value == 1:
        # Un seul symbole : on lui laisse toute la case pour l'exploiter
        # au maximum, plutot que la seule cellule centrale de la grille.
        cells = f'<div class="pip pip-single">{render_pip_symbol(pip_choice[0])}</div>'
    else:
        cells = "".join(
            f'<div class="pip" style="grid-row:{r}; grid-column:{c};">{render_pip_symbol(sym)}</div>'
            for (r, c), sym in zip(pips, pip_choice)
        )
    return (f'<div class="die-box{used_cls}" id="success-die-box" '
            f'style="--pip-size:{size};">{cells}</div>')


def render_fate_die(key, used=True):
    used_cls = "" if used else " die-dimmed"
    if key is None:
        return (f'<div class="die-box fate{used_cls}" id="fate-die-box">'
                f'<div class="emblem-placeholder">{EMBLEM_SVG}</div></div>')
    face = FATE_BY_KEY[key]
    return (f'<div class="die-box fate{used_cls}" id="fate-die-box"><div class="fate-emoji">{face["emoji"]}</div>'
            f'<div class="fate-label">{face["label"]}</div></div>')


# TOTEMS (et ses derives ci-dessous) sont peuples par switch_story() selon
# l'histoire active -- voir stories.py pour les donnees par histoire.
# Conteneurs MUTABLES (jamais reassignes en un nouvel objet) pour que tout
# le code qui les reference reste a jour apres un changement d'histoire.
TOTEMS = []
TOTEMS_BY_KEY = {}
ALLY_HELP_TEXT = {}


def _rebuild_totem_derived_globals():
    """A appeler apres toute modification de TOTEMS (ou de
    CURRENT_STORY_CONFIG), typiquement au changement d'histoire :
    recalcule tout ce qui en derive et qui ne depend QUE des totems de
    base de l'histoire (pas des totems ajoutes en cours de partie, qui
    peuvent changer a tout moment -- voir _totem_modal_info() et
    render_totem_row_html() ci-dessous, recalcules eux a chaque
    affichage)."""
    global TOTEMS_BY_KEY, ALLY_HELP_TEXT
    TOTEMS_BY_KEY = {t["key"]: t for t in TOTEMS}
    ALLY_HELP_TEXT = dict(CURRENT_STORY_CONFIG.get("ally_help_text") or {})


def _totem_modal_info():
    """Fusionne les totems de base de l'histoire active (avec leurs
    pouvoirs/capacite definis dans stories.py) et les totems ajoutes par
    le joueur en cours de partie (session.custom_totems -- y compris le
    totem de depart d'une histoire personnalisee, ajoute via ce meme
    systeme, voir switch_story()) en un seul dict
    {cle: {icon, label, powers, special}}.

    C'est la source commune de la vignette qui s'affiche au clic sur un
    totem, que ce soit dans la rangee sous le titre ou dans les jauges
    totemiques. Recalculee a CHAQUE appel (jamais mise en cache dans une
    variable globale) pour toujours refleter immediatement un totem tout
    juste ajoute ou retire, sans attendre un changement d'histoire."""
    info = {
        t["key"]: {"icon": t["icon"], "label": t["label"],
                   "powers": t.get("powers") or [], "special": t.get("special") or ""}
        for t in TOTEMS
    }
    for t in session.custom_totems:
        info[t["key"]] = {
            "icon": t.get("emoji") or "\U0001F43E",
            "label": t["label"],
            "powers": t.get("powers") or [],
            "special": t.get("special") or "",
        }
    return info


def render_totem_row_html():
    """Rangee de badges affichee sous le titre : totems de base deja
    acquis des le debut de l'histoire (Animorph, Poudlard...) + tous les
    totems ajoutes par le joueur en cours de partie -- y compris, pour
    une histoire personnalisee, son unique totem de depart (qui n'existe
    que via le systeme des totems ajoutes, voir switch_story()). Vide
    tant qu'aucun totem n'est encore acquis."""
    info = _totem_modal_info()
    badges = "".join(
        f'<div class="totem-badge" onclick="openTotemModal(\'{key}\')">{t["icon"]}</div>'
        for key, t in info.items()
    )
    # Toujours enveloppee dans #totemRow (meme vide) pour pouvoir etre
    # rafraichie via AJAX quand un totem est ajoute/retire en cours de
    # partie, sans recharger toute la page -- voir applyGaugeAndPicker().
    return f'<div class="totem-row" id="totemRow">{badges}</div>'


PIP_POSITIONS_JSON = json.dumps({str(k): v for k, v in PIP_POSITIONS.items()})
FATE_FACES_JSON = json.dumps(FATE_FACES)


def render_totem_modal_html():
    """Vignette (modale) affichee au clic sur un totem : icone, nom,
    liste des pouvoirs et capacite speciale. Regeneree a chaque affichage
    de page (voir _totem_modal_info()) pour toujours correspondre a
    l'histoire active et aux totems ajoutes en cours de partie."""
    totem_info_json = json.dumps(_totem_modal_info())
    return f"""
<div id="totem-modal-overlay" class="totem-modal-overlay" onclick="closeTotemModal(event)">
  <div class="totem-modal-box" onclick="event.stopPropagation()">
    <div id="totem-modal-icon" class="totem-modal-icon"></div>
    <h2 id="totem-modal-title" style="margin-top:6px;"></h2>
    <ul id="totem-modal-powers"></ul>
    <p id="totem-modal-special" class="totem-modal-special"></p>
    <button onclick="closeTotemModal(event)">Fermer</button>
  </div>
</div>
<script>
const TOTEM_INFO = {totem_info_json};
function openTotemModal(key){{
  var t = TOTEM_INFO[key];
  if (!t) return;
  document.getElementById('totem-modal-icon').innerText = t.icon;
  document.getElementById('totem-modal-title').innerText = t.label;
  var ul = document.getElementById('totem-modal-powers');
  ul.innerHTML = "";
  t.powers.forEach(function(p){{
    var li = document.createElement('li');
    li.innerText = p;
    ul.appendChild(li);
  }});
  var specialEl = document.getElementById('totem-modal-special');
  specialEl.innerText = t.special || "";
  specialEl.style.display = t.special ? 'block' : 'none';
  document.getElementById('totem-modal-overlay').style.display = 'flex';
}}
function closeTotemModal(e){{
  document.getElementById('totem-modal-overlay').style.display = 'none';
}}
function refreshSymbolPicker(url, params){{
  fetch(url, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams(params).toString()
  }})
    .then(function(r){{ return r.text(); }})
    .then(function(html){{
      var el = document.getElementById('symbolPicker');
      if (el) {{ el.outerHTML = html; }}
    }});
}}
function pickSymbol(key){{
  refreshSymbolPicker('/set_pip_symbol', {{symbol: key}});
}}
function pickMode(mode){{
  refreshSymbolPicker('/set_pip_mode', {{mode: mode}});
}}
function toggleEnabledSymbol(key){{
  refreshSymbolPicker('/toggle_enabled_symbol', {{symbol: key}});
}}
function refreshAllowedValuesPicker(url, params){{
  fetch(url, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams(params || {{}}).toString()
  }})
    .then(function(r){{ return r.text(); }})
    .then(function(html){{
      var el = document.getElementById('allowedValuesPicker');
      if (el) {{ el.outerHTML = html; }}
    }});
}}
function toggleAllowedValue(value){{
  refreshAllowedValuesPicker('/toggle_allowed_value', {{value: value}});
}}
function resetAllowedValues(){{
  refreshAllowedValuesPicker('/reset_allowed_values');
}}
function refreshAllowedFatePicker(url, params){{
  fetch(url, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams(params || {{}}).toString()
  }})
    .then(function(r){{ return r.text(); }})
    .then(function(html){{
      var el = document.getElementById('allowedFatePicker');
      if (el) {{ el.outerHTML = html; }}
    }});
}}
function toggleAllowedFate(key){{
  refreshAllowedFatePicker('/toggle_allowed_fate', {{key: key}});
}}
function resetAllowedFate(){{
  refreshAllowedFatePicker('/reset_allowed_fate');
}}
</script>
"""


def roll_animation_script():
    """Script d'animation genere par requete (depend du symbole actuellement
    choisi). Simule un 'roulement' visuel : defilement rapide de faces
    aleatoires qui ralentit avant de s'arreter, puis soumet le vrai lancer
    (calcule cote serveur) une fois l'animation terminee."""
    current_pip_html_json = json.dumps(render_pip_symbol(session.pip_symbol))
    pip_html_map_json = json.dumps({key: render_pip_symbol(key) for key in PIP_SYMBOLS})
    enabled_symbols_json = json.dumps(session.enabled_symbols or list(PIP_SYMBOLS.keys()))
    pip_mode_json = json.dumps(session.pip_mode)
    pip_size_json = json.dumps(PIP_SIZE_BY_COUNT)
    return f"""
    <script>
    const PIP_POSITIONS_JS = {PIP_POSITIONS_JSON};
    const FATE_FACES_JS = {FATE_FACES_JSON};
    const CURRENT_PIP_HTML = {current_pip_html_json};
    const PIP_MODE_JS = {pip_mode_json};
    const PIP_HTML_MAP_JS = {pip_html_map_json};
    const ENABLED_SYMBOLS_JS = {enabled_symbols_json};
    const PIP_SIZE_JS = {pip_size_json};

    function pickPipHtml(){{
      var keys = ENABLED_SYMBOLS_JS.length ? ENABLED_SYMBOLS_JS : Object.keys(PIP_HTML_MAP_JS);
      var k = keys[Math.floor(Math.random()*keys.length)];
      return PIP_HTML_MAP_JS[k];
    }}

    function renderSuccessFrame(value){{
      var positions = PIP_POSITIONS_JS[String(value)];
      var html = '';
      var uniformHtml = (PIP_MODE_JS === 'random') ? pickPipHtml() : CURRENT_PIP_HTML;
      if (value === 1){{
        var symHtml1 = (PIP_MODE_JS === 'mixed' || PIP_MODE_JS === 'random') ? pickPipHtml() : uniformHtml;
        html = '<div class="pip pip-single">'+symHtml1+'</div>';
      }} else {{
        positions.forEach(function(pos){{
          var symHtml = (PIP_MODE_JS === 'mixed') ? pickPipHtml() : uniformHtml;
          html += '<div class="pip" style="grid-row:'+pos[0]+'; grid-column:'+pos[1]+';">'+symHtml+'</div>';
        }});
      }}
      var box = document.getElementById('success-die-box');
      if (box) {{ box.style.setProperty('--pip-size', PIP_SIZE_JS[String(value)] || '1.6rem'); }}
      return html;
    }}
    function renderFateFrame(face){{
      return '<div class="fate-emoji">'+face.emoji+'</div><div class="fate-label">'+face.label+'</div>';
    }}
    function animateDie(elementId, kind, callback){{
      var el = document.getElementById(elementId);
      if (!el) {{ callback(); return; }}
      var steps = 0, maxSteps = 14, delay = 55;
      function tick(){{
        if (kind === 'success'){{
          var v = 1 + Math.floor(Math.random()*6);
          el.innerHTML = renderSuccessFrame(v);
        }} else {{
          var f = FATE_FACES_JS[Math.floor(Math.random()*FATE_FACES_JS.length)];
          el.innerHTML = renderFateFrame(f);
        }}
        steps++;
        delay = delay * 1.22;
        if (steps < maxSteps){{
          setTimeout(tick, delay);
        }} else {{
          callback();
        }}
      }}
      tick();
    }}
    function startRoll(kind){{
      var btns = document.querySelectorAll('#rollForm button');
      btns.forEach(function(b){{ b.disabled = true; }});
      var pending = 0;
      function finish(){{
        pending--;
        if (pending <= 0){{ submitRoll(kind, btns); }}
      }}
      if (kind === 'success' || kind === 'both'){{ pending++; animateDie('success-die-box', 'success', finish); }}
      if (kind === 'fate' || kind === 'both'){{ pending++; animateDie('fate-die-box', 'fate', finish); }}
    }}
    function applyExtras(data){{
      var gEl = document.getElementById('totemGauges');
      if (gEl && data.gauges) {{ gEl.outerHTML = data.gauges; }}
      var tEl = document.getElementById('threatGauge');
      if (tEl && data.threat) {{ tEl.outerHTML = data.threat; }}
      var qEl = document.getElementById('sideQuests');
      if (qEl && data.quests) {{ qEl.outerHTML = data.quests; }}
      var nEl = document.getElementById('narratorNote');
      if (nEl && typeof data.narrator_note === 'string') {{ nEl.outerHTML = data.narrator_note; }}
      var aiEl = document.getElementById('aiStoryPanel');
      if (aiEl && typeof data.ai_story === 'string') {{ aiEl.outerHTML = data.ai_story; }}
    }}
    function showAiWritingIndicator(){{
      var feedEl = document.getElementById('aiStoryFeed');
      if (feedEl) {{
        feedEl.insertAdjacentHTML('beforeend',
          '<div class="sub" id="aiWritingIndicator" style="margin-top:8px;">'
          + '&#8987; Le narrateur ecrit...</div>');
        feedEl.scrollTop = feedEl.scrollHeight;
      }}
    }}
    function submitRoll(kind, btns){{
      showAiWritingIndicator();
      fetch('/roll', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{action: kind}}).toString()
      }})
        .then(function(r){{ return r.json(); }})
        .then(function(data){{
          var diceEl = document.getElementById('diceResultArea');
          if (diceEl) {{ diceEl.outerHTML = data.dice; }}
          var histEl = document.getElementById('historyList');
          if (histEl) {{ histEl.outerHTML = data.history; }}
          applyExtras(data);
        }})
        .finally(function(){{
          document.querySelectorAll('#rollForm button').forEach(function(b){{ b.disabled = false; }});
        }});
    }}
    function applyResult(data){{
      var diceEl = document.getElementById('diceResultArea');
      if (diceEl) {{ diceEl.outerHTML = data.dice; }}
      var histEl = document.getElementById('historyList');
      if (histEl) {{ histEl.outerHTML = data.history; }}
      applyExtras(data);
    }}
    function useTotemEnergy(key){{
      showAiWritingIndicator();
      fetch('/use_totem_energy', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{key: key}}).toString()
      }})
        .then(function(r){{ return r.json(); }})
        .then(function(data){{
          var diceEl = document.getElementById('diceResultArea');
          if (diceEl) {{ diceEl.outerHTML = data.dice; }}
          var histEl = document.getElementById('historyList');
          if (histEl) {{ histEl.outerHTML = data.history; }}
          var gEl = document.getElementById('totemGauges');
          if (gEl && data.gauges) {{ gEl.outerHTML = data.gauges; }}
          var nEl = document.getElementById('narratorNote');
          if (nEl && typeof data.narrator_note === 'string') {{ nEl.outerHTML = data.narrator_note; }}
          var aiEl = document.getElementById('aiStoryPanel');
          if (aiEl && typeof data.ai_story === 'string') {{ aiEl.outerHTML = data.ai_story; }}
        }});
    }}
    function applyGaugeAndPicker(data){{
      var gEl = document.getElementById('totemGauges');
      if (gEl && data.gauges) {{ gEl.outerHTML = data.gauges; }}
      var sEl = document.getElementById('symbolPicker');
      if (sEl && data.symbol_picker) {{ sEl.outerHTML = data.symbol_picker; }}
      var rEl = document.getElementById('totemRow');
      if (rEl && data.totem_row) {{ rEl.outerHTML = data.totem_row; }}
      if (data.totem_info) {{
        Object.keys(TOTEM_INFO).forEach(function(k){{ delete TOTEM_INFO[k]; }});
        Object.assign(TOTEM_INFO, data.totem_info);
      }}
    }}
    function addCustomTotem(){{
      var nameEl = document.getElementById('totemNameInput');
      var name = nameEl ? nameEl.value.trim() : '';
      if (!name) {{ alert('Donne au moins un nom au totem.'); return; }}
      var fd = new FormData();
      fd.append('label', name);
      fd.append('powers', document.getElementById('totemPowersInput').value);
      fd.append('special', document.getElementById('totemSpecialInput').value);
      fd.append('emoji', document.getElementById('totemEmojiInput').value);
      var fileInput = document.getElementById('totemImageInput');
      if (fileInput && fileInput.files.length > 0) {{ fd.append('image', fileInput.files[0]); }}
      fetch('/add_custom_totem', {{ method: 'POST', body: fd }})
        .then(function(r){{ return r.json(); }})
        .then(function(data){{
          applyGaugeAndPicker(data);
          nameEl.value = '';
          document.getElementById('totemPowersInput').value = '';
          document.getElementById('totemSpecialInput').value = '';
          document.getElementById('totemEmojiInput').value = '';
          if (fileInput) {{ fileInput.value = ''; }}
        }});
    }}
    function removeCustomTotem(key){{
      if (!confirm("Retirer ce totem ? Sa jauge et son image sont supprimees definitivement.")) {{ return; }}
      fetch('/remove_custom_totem', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{key: key}}).toString()
      }})
        .then(function(r){{ return r.json(); }})
        .then(function(data){{ applyGaugeAndPicker(data); }});
    }}
    function completeSideQuest(questId){{
      fetch('/complete_side_quest', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{quest_id: questId}}).toString()
      }})
        .then(function(r){{ return r.text(); }})
        .then(function(html){{
          var qEl = document.getElementById('sideQuests');
          if (qEl) {{ qEl.outerHTML = html; }}
        }});
    }}
    function doUndo(){{
      fetch('/undo', {{ method: 'POST' }})
        .then(function(r){{ return r.json(); }})
        .then(applyResult);
    }}
    function doClear(){{
      if (!confirm("Effacer tout l'historique ?")) {{ return; }}
      fetch('/clear', {{ method: 'POST' }})
        .then(function(r){{ return r.json(); }})
        .then(applyResult);
    }}
    function addStoryEntry(){{
      var el = document.getElementById('storyEntryInput');
      var text = el ? el.value.trim() : '';
      if (!text) {{ return; }}
      fetch('/add_story_entry', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{text: text}}).toString()
      }})
        .then(function(r){{ return r.json(); }})
        .then(function(data){{
          if (el) {{ el.value = ''; }}
          var b = document.querySelector('button[onclick="addStoryEntry()"]');
          if (b) {{
            var old = b.innerText;
            b.innerText = 'Ajoute !';
            setTimeout(function(){{ b.innerText = old; }}, 1200);
          }}
        }});
    }}
    function copyFullPrompt(){{
      fetch('/full_prompt')
        .then(function(r){{ return r.text(); }})
        .then(function(text){{
          var el = document.getElementById('fullPromptBox');
          el.value = text;
          el.select(); el.setSelectionRange(0, 999999);
          navigator.clipboard.writeText(el.value).then(function(){{
            var b = document.getElementById('fullPromptBtn');
            var old = b.innerText; b.innerText = 'Copie !';
            setTimeout(function(){{ b.innerText = old; }}, 1500);
          }});
        }});
    }}
    function refreshAiPanel(url, params){{
      fetch(url, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams(params || {{}}).toString()
      }})
        .then(function(r){{ return r.text(); }})
        .then(function(html){{
          var panel = document.getElementById('aiStoryPanel');
          if (panel) {{ panel.outerHTML = html; }}
        }});
    }}
    function clearMistralKey(){{
      if (!confirm("Retirer la cle et revenir au mode manuel (bouton copier) ?")) {{ return; }}
      refreshAiPanel('/clear_mistral_key');
    }}
    function resetAiConversation(){{
      if (!confirm("Reinitialiser TOUTE la partie (des, jauges, menace, "
                   + "quetes, totems et conversation IA) et revenir a l'etat "
                   + "de l'installation de l'application ? Cette action est "
                   + "irreversible.")) {{ return; }}
      fetch('/reset_ai_conversation', {{method: 'POST'}})
        .then(function(){{ window.location.reload(); }});
    }}
    function sendFullPromptToAi(){{
      showAiWritingIndicator();
      refreshAiPanel('/send_full_prompt');
    }}
    function sendAiMessage(){{
      var el = document.getElementById('aiFreeMessageInput');
      var text = el ? el.value.trim() : '';
      if (!text) {{ return; }}
      showAiWritingIndicator();
      refreshAiPanel('/send_ai_message', {{text: text}});
      if (el) {{ el.value = ''; }}
    }}
    </script>
    """


def back_button_trap_script(target_url):
    """Piege le bouton 'retour' materiel du telephone (WebView) pour qu'il
    ramene toujours vers `target_url` (le selecteur d'histoire) au lieu de
    suivre l'historique de navigation brut du WebView.

    Pourquoi ce piege est necessaire : sur Android, le bouton retour du
    telephone appelle webView.goBack(), qui rejoue l'historique de PAGES
    chargees (redirections HTTP comprises), pas l'historique "logique" de
    l'appli. Or plusieurs ecrans (page de cle API, selecteur d'histoire,
    ecran de jeu) partagent souvent la meme URL "/" ou s'enchainent via des
    redirections HTTP -- resultat, un simple retour peut renvoyer vers la
    page de cle API au lieu du selecteur d'histoire.

    Astuce : on empile un etat factice (history.pushState) des le chargement
    de cette page. Le bouton retour du telephone "depile" alors cet etat
    SANS quitter la page (evenement 'popstate', pas de rechargement reseau),
    ce qui nous laisse decider nous-memes ou l'utilisateur doit atterrir :
    ici, toujours le selecteur d'histoire, quel que soit l'historique reel."""
    return f"""
    <script>
      (function() {{
        try {{
          history.pushState({{backTrap: true}}, '', location.href);
        }} catch (e) {{}}
        window.addEventListener('popstate', function(e) {{
          window.location.href = {json.dumps(target_url)};
        }});
      }})();
    </script>
    """


def layout(title, body):
    header_title = (CURRENT_STORY_CONFIG or {}).get("header_title", "Les Des de l'Aventure")
    change_story_url = url_for('change_story')
    return render_template_string(f"""
    <!DOCTYPE html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>{BASE_CSS}</head>
    <body>
    <div class="header-block">
      <h1>{header_title}</h1>
      {render_totem_row_html()}
      <div class="sub">{title}</div>
      <div class="sub" style="margin-top:6px;">
        <a href="{change_story_url}" style="color:inherit;">&#128257; Changer d'histoire</a>
      </div>
    </div>
    {{{{ body|safe }}}}
    {render_totem_modal_html()}
    {roll_animation_script()}
    {back_button_trap_script(change_story_url)}
    </body></html>
    """, body=body)


def last_of(field):
    for r in reversed(session.history):
        if r.get(field) is not None:
            return r
    return None


def render_symbol_picker_html():
    def badge_html(key, info):
        if session.pip_mode == "single":
            cls = "symbol-active" if key == session.pip_symbol else ""
            onclick = f"pickSymbol('{key}')"
        else:
            cls = "" if key in session.enabled_symbols else "symbol-excluded"
            onclick = f"toggleEnabledSymbol('{key}')"
        return (f'<div class="totem-badge {cls}" onclick="{onclick}" '
                f'title="{info["label"]}">{render_pip_symbol(key, badge=True)}</div>')

    all_syms = session.all_symbols()
    # Symboles de base d'abord (ordre fixe habituel), puis les totems
    # ajoutes par le joueur a la suite -- par rangees de 5, quel que soit
    # le nombre total (le nombre de rangees s'adapte automatiquement).
    symbol_keys = list(PIP_SYMBOLS.keys()) + [t["key"] for t in session.custom_totems]
    rows_keys = [symbol_keys[i:i + 5] for i in range(0, len(symbol_keys), 5)]

    aleatoire_html = (
        f'<div class="totem-badge big{" mode-active" if session.pip_mode == "random" else ""}" '
        f'onclick="pickMode(\'random\')" title="Symbole aleatoire a chaque lancer">\U0001f3b2</div>'
    )
    mixe_html = (
        f'<div class="totem-badge big{" mode-active" if session.pip_mode == "mixed" else ""}" '
        f'onclick="pickMode(\'mixed\')" title="Melange de symboles sur la meme face">\U0001f500</div>'
    )

    rows_html = "".join(
        '<div class="totem-row" style="justify-content:center; margin:0;">'
        + "".join(badge_html(k, all_syms[k]) for k in row) + "</div>"
        for row in rows_keys
    )
    if not symbol_keys:
        rows_html = ('<p class="sub" style="margin:0; text-align:center;">Aucun totem pour '
                     "l'instant \u2014 ajoutes-en un dans la carte \u00abJauges "
                     'tot\u00e9miques\u00bb ci-dessous des que l\'histoire t\'en offre '
                     "l'occasion.</p>")

    return (
        '<div id="symbolPicker" style="display:flex; align-items:center; justify-content:center; '
        'gap:10px; flex-wrap:wrap; margin-top:26px;">'
        + aleatoire_html
        + '<div style="display:flex; flex-direction:column; gap:8px; align-items:center;">'
        + rows_html
        + "</div>"
        + mixe_html
        + "</div>"
    )


def render_allowed_values_picker_html():
    """Selecteur multi-choix des valeurs autorisees pour le de de reussite :
    chaque valeur peut etre cochee/decochee independamment (ex: ne garder
    que 1, 5 et 6). Le tirage se fait uniquement parmi les valeurs cochees."""
    allowed = session.allowed_success_values
    all_active = len(allowed) == 6

    def value_badge(v):
        cls = "symbol-active" if v in allowed else "symbol-excluded"
        return (f'<div class="totem-badge {cls}" onclick="toggleAllowedValue({v})" '
                f'title="Inclure/exclure la valeur {v} du tirage">{v}</div>')

    reset_html = (f'<div class="totem-badge big{" mode-active" if all_active else ""}" '
                  f'onclick="resetAllowedValues()" title="Reactiver les 6 valeurs">&#8635;</div>')

    if all_active:
        label = "Toutes les valeurs sont possibles (1 a 6)"
    else:
        label = "Valeurs possibles : " + ", ".join(str(v) for v in allowed)

    return (
        '<div id="allowedValuesPicker" style="display:flex; flex-direction:column; '
        'align-items:center; gap:8px; margin-top:16px;">'
        + '<div class="totem-row" style="justify-content:center; margin:0;">'
        + "".join(value_badge(v) for v in range(1, 7))
        + reset_html
        + "</div>"
        + f'<div class="sub" style="font-size:0.85rem;">{label}</div>'
        + "</div>"
    )


def render_allowed_fate_picker_html():
    """Selecteur multi-choix des symboles autorises pour le de du destin :
    meme principe que le picker des valeurs de reussite, mais avec les 6
    emojis du destin. Le tirage se fait uniquement parmi les symboles
    coches (le '?' garde en plus sa regle propre : pas de repetition tant
    qu'une quete secondaire est ouverte)."""
    allowed = session.allowed_fate_keys
    all_active = len(allowed) == len(FATE_FACES)

    def fate_badge(f):
        cls = "symbol-active" if f["key"] in allowed else "symbol-excluded"
        return (f'<div class="totem-badge {cls}" onclick="toggleAllowedFate(\'{f["key"]}\')" '
                f'title="Inclure/exclure {f["label"]} du tirage">{f["emoji"]}</div>')

    reset_html = (f'<div class="totem-badge big{" mode-active" if all_active else ""}" '
                  f'onclick="resetAllowedFate()" title="Reactiver les 6 symboles">&#8635;</div>')

    if all_active:
        label = "Tous les symboles du destin sont possibles"
    else:
        names = ", ".join(FATE_BY_KEY[k]["label"] for k in allowed)
        label = "Symboles possibles : " + names

    return (
        '<div id="allowedFatePicker" style="display:flex; flex-direction:column; '
        'align-items:center; gap:8px; margin-top:16px;">'
        + '<div class="totem-row" style="justify-content:center; margin:0;">'
        + "".join(fate_badge(f) for f in FATE_FACES)
        + reset_html
        + "</div>"
        + f'<div class="sub" style="font-size:0.85rem;">{label}</div>'
        + "</div>"
    )


def render_totem_gauges_html():
    """Une barre de jauge par symbole/totem/allie (base + totems ajoutes
    par le joueur). Un bouton 'Utiliser' apparait des qu'une jauge est
    pleine (>=TOTEM_ENERGY_THRESHOLD) ; un bouton de suppression apparait
    uniquement sur les totems ajoutes par le joueur (jamais sur les
    symboles de base)."""
    modal_info = _totem_modal_info()
    rows = []
    for key, info in session.all_symbols().items():
        energy = session.totem_energy.get(key, 0)
        pct = min(100, round(100 * energy / TOTEM_ENERGY_THRESHOLD))
        ready = energy >= TOTEM_ENERGY_THRESHOLD
        btn = (f'<button type="button" class="small" onclick="useTotemEnergy(\'{key}\')">'
               f'&#10024; Utiliser</button>') if ready else ""
        bar_cls = "totem-gauge-fill ready" if ready else "totem-gauge-fill"
        if info.get("image"):
            icon_html = (f'<img src="{url_for("totem_image", filename=info["image"])}" '
                         f'alt="{info["label"]}" style="width:1.4rem; height:1.4rem; '
                         f'object-fit:contain; vertical-align:-0.25em;">')
        else:
            icon_html = info["emoji"]
        remove_btn = (
            f'<button type="button" class="small danger" onclick="removeCustomTotem(\'{key}\')" '
            f'title="Retirer ce totem">&#128465;</button>'
        ) if info.get("is_custom") else ""
        # Icone cliquable UNIQUEMENT quand une fiche (pouvoirs/capacite)
        # existe reellement pour ce symbole -- voir _totem_modal_info() :
        # totems de base de l'histoire + totems ajoutes par le joueur.
        # Les symboles sans fiche (allies fixes lies a une face du de,
        # comme Araignee/Bouclier/Etoile sur Animorph) restent affiches
        # normalement mais ne declenchent pas la vignette.
        icon_click = (
            f' onclick="openTotemModal(\'{key}\')" style="cursor:pointer;"'
            if key in modal_info else ""
        )
        rows.append(
            '<div class="totem-gauge-row">'
            f'<span class="totem-gauge-icon" title="{info["label"]}"{icon_click}>{icon_html}</span>'
            f'<div class="totem-gauge-track"><div class="{bar_cls}" style="width:{pct}%"></div></div>'
            f'<span class="totem-gauge-val">{energy}/{TOTEM_ENERGY_THRESHOLD}</span>'
            f'{btn}{remove_btn}'
            '</div>'
        )
    if not rows:
        return ('<div id="totemGauges"><p class="sub" style="margin:0;">Aucun totem pour '
                "l'instant. Ajoutes-en un ci-dessous des qu'un ami, une creature ou une "
                "nouvelle competence apparait dans l'histoire.</p></div>")
    return f'<div id="totemGauges">{"".join(rows)}</div>'


def render_threat_gauge_html():
    pct = min(100, round(100 * session.threat_level / THREAT_THRESHOLD))
    danger_cls = " danger" if pct >= 70 else ""
    return (
        '<div id="threatGauge" style="margin-top:10px;">'
        f'<div class="sub" style="font-size:0.85rem;">Jauge de menace : {session.threat_level}/{THREAT_THRESHOLD}</div>'
        f'<div class="totem-gauge-track"><div class="totem-gauge-fill{danger_cls}" style="width:{pct}%"></div></div>'
        '</div>'
    )


def render_side_quests_html():
    open_quests = session.open_side_quests()
    if not open_quests:
        items = '<p class="sub" style="font-size:0.85rem;">(aucune quete secondaire en attente)</p>'
    else:
        rows = []
        for q in open_quests:
            icon = "\U0001f465" if q["kind"] == "ami" else "\U0001f381"
            txt = "Se faire un nouvel ami" if q["kind"] == "ami" else "Trouver un nouvel objet (ou totem)"
            rows.append(
                f'<div class="history-item">{icon} {txt}'
                f' <button type="button" class="small" onclick="completeSideQuest({q["id"]})">'
                f'Terminee</button></div>'
            )
        items = "".join(rows)
    return f'<div id="sideQuests">{items}</div>'


def narrator_note_for_record(record):
    """Genere le texte a copier-coller pour signaler a l'IA narratrice un
    evenement '?'/'!' du de du destin. Vide si le lancer ne concerne pas
    l'un de ces deux symboles."""
    if not record or record.get("fate") not in ("question", "exclamation"):
        return ""
    face = FATE_BY_KEY[record["fate"]]
    text = f"{face['emoji']} {face['label']} : {face['desc']}"
    side_quest = record.get("side_quest")
    if side_quest:
        kind_txt = "se faire un nouvel ami" if side_quest["kind"] == "ami" else "trouver un nouvel objet (ou un nouveau totem)"
        text += f"\n(Quete secondaire debloquee : {kind_txt}.)"
    return text


def ai_event_text(record):
    """Texte d'evenement pour la narration automatique : le meme contenu
    que celui affiche/copiable pour un lancer (describe_record), complete
    par la note '?'/'!' si besoin. C'est l'equivalent automatique de ce
    qu'on collait auparavant a la main dans une IA externe."""
    if not record:
        return ""
    parts = [session.describe_record(record)]
    note = narrator_note_for_record(record)
    if note:
        parts.append(note)
    return "\n".join(parts)


def run_ai_narrator(event_text):
    """Envoie un evenement de jeu a l'IA narratrice si une cle Mistral est
    configuree, et enregistre l'echange dans la conversation (persistee
    avec le reste de la partie). Ne fait rien -- renvoie (None, None) --
    si aucune cle n'est configuree : l'appli reste alors en mode manuel
    (bouton "copier le prompt"), sans aucun appel reseau.

    Renvoie (reponse_ou_None, erreur_ou_None). En cas d'erreur (reseau,
    cle invalide, limite atteinte...), le lancer de des lui-meme n'est
    JAMAIS perdu : cette fonction ne fait que rapporter l'erreur pour
    affichage, le mode manuel de secours (bouton copier) reste disponible."""
    if not has_mistral_key() or not event_text:
        return None, None
    if not session.ai_conversation:
        session.add_ai_message("system", build_mechanics_context(auto_mode=True))
    session.add_ai_message("user", event_text)
    text, error = mistral_client.chat(get_mistral_key(), session.ai_messages_to_send(),
                                       model=get_mistral_model())
    if error:
        return None, error
    session.add_ai_message("assistant", text)
    return text, None


def render_ai_panel_html(transient_error=None):
    """Bloc "Narration automatique" affiche sur la page principale :
    - si aucune cle Mistral n'est configuree : formulaire pour en coller
      une (compte gratuit, sans carte bancaire) ;
    - sinon : fil de l'histoire generee par l'IA au fil des lancers, avec
      un bouton pour reinitialiser la conversation (sans toucher au reste
      de la partie) ou retirer la cle (retour au mode manuel).
    transient_error, si fourni, affiche un bandeau d'erreur ponctuel (non
    sauvegarde) pour le dernier appel a l'IA -- le mode manuel restant
    toujours disponible via le bouton "copier le prompt complet"."""
    if not has_mistral_key():
        return (
            '<div id="aiStoryPanel">'
            '<p class="sub" style="margin:0 0 10px 0;">'
            "Aucune cle API Mistral configuree pour l'instant -- l'histoire "
            "ne s'ecrit donc pas ici automatiquement (le bouton \"copier le "
            "prompt complet\" plus bas reste disponible en mode manuel)."
            "</p>"
            f'<a class="btn" href="{url_for("configure_key_page")}" '
            'style="display:inline-block; text-decoration:none;">'
            '&#128273; Configurer la cle API</a>'
            '</div>'
        )

    rows = []
    for msg in session.ai_conversation:
        if msg["role"] == "user":
            rows.append(
                '<div class="sub" style="margin:10px 0 2px 0; font-size:0.82rem;">'
                f'&#127922; {msg["content"].replace(chr(10), "<br>")}</div>'
            )
        elif msg["role"] == "assistant":
            rows.append(
                '<div class="result-text" style="text-align:left; margin-top:2px;">'
                f'{msg["content"].replace(chr(10), "<br>")}</div>'
            )
    feed = "".join(rows) if rows else (
        '<p class="sub">(rien pour l\'instant -- lance un de, utilise le bouton '
        '"Envoyer le prompt a l\'IA" plus bas, ou ecris un message ci-dessous '
        'pour planter le decor et demarrer l\'aventure)</p>'
    )
    error_html = (
        f'<div class="sub" style="color:var(--red); margin-bottom:8px;">'
        f'&#9888;&#65039; {transient_error}</div>'
        if transient_error else ""
    )
    return (
        '<div id="aiStoryPanel">'
        '<div class="sub" style="margin-bottom:8px;">'
        '&#9989; Narration automatique active (Mistral)</div>'
        + error_html
        + f'<div id="aiStoryFeed" style="max-height:340px; overflow-y:auto;">{feed}</div>'
        + '<label style="margin-top:12px;">Message libre a l\'IA (demarrer l\'aventure, '
        + 'decrire une action de Gabin...)</label>'
        + '<textarea id="aiFreeMessageInput" placeholder="Ex: Commence l\'aventure : '
        + 'Gabin explore une jungle mysterieuse au coucher du soleil..."></textarea>'
        + '<button type="button" onclick="sendAiMessage()">&#9993;&#65039; Envoyer a l\'IA</button>'
        + '<div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">'
        + '<button type="button" class="small secondary" onclick="resetAiConversation()">'
        + '&#8635; Reinitialiser toute la partie</button>'
        + '<button type="button" class="small danger" onclick="clearMistralKey()">'
        + '&#10060; Retirer la cle</button>'
        + '</div>'
        + '</div>'
    )


def render_narrator_note_html(text):
    """Boite copiable a signaler a l'IA narratrice (pouvoir/allie invoque,
    ou evenement '?'/'!'). Vide (mais presente dans le DOM) si rien a
    signaler pour l'instant."""
    if not text:
        return '<div id="narratorNote"></div>'
    return (
        '<div id="narratorNote" class="result-text" style="text-align:left;">'
        '<div class="sub" style="margin:0 0 6px 0; text-align:left; color:var(--ink);">'
        'A copier pour me le signaler :</div>'
        f'<textarea class="copybox" id="narratorBox" readonly style="min-height:70px;">{text}</textarea>'
        '<button type="button" class="small" id="narratorCopyBtn" '
        'onclick="copyBox(\'narratorBox\',\'narratorCopyBtn\')">Copier</button>'
        '</div>'
    )


def build_mechanics_context(auto_mode=False):
    """Bloc de texte contenant uniquement les mecaniques du jeu et ce qui
    est attendu du narrateur (sans l'historique de l'aventure) -- reutilise
    a deux endroits :
      - auto_mode=False : comme premiere partie du "prompt complet" a
        copier manuellement (build_full_prompt), avec les instructions
        pour l'ancien flux (blocs a coller, resumes de chapitre).
      - auto_mode=True : comme message "system" envoye a l'IA en mode
        narration automatique (voir run_ai_narrator) -- les evenements de
        jeu sont alors transmis directement par l'appli, donc les
        instructions de fin de paragraphe sont adaptees en consequence
        (plus de mention de blocs a copier-coller).

    Volontairement tres compact (formulations courtes, faits regroupes sur
    une ligne par entree) : c'est ce bloc-la qui est renvoye a l'identique
    a chaque appel (copie manuelle ou requete IA), donc le condenser
    reduit la taille de CHAQUE envoi.

    Les libelles ci-dessous (succes/destin/totems) sont des versions
    resumees ecrites specialement pour l'IA -- l'affichage dans l'appli
    (SUCCESS_LABELS, FATE_FACES, TOTEMS) garde lui ses textes complets et
    n'est pas modifie. Chaque regle, chaque symbole, chaque pouvoir et
    chaque seuil reste present ; seule la formulation est plus courte."""

    # Versions condensees (memes informations, formulation plus courte)
    # des textes affiches dans l'interface -- utilisees uniquement ici.
    success_compact = {
        1: "Echec critique, rattrapable (jamais la fin de l'histoire)",
        2: "Echec ou reussite tres dure",
        3: "Reussite partielle",
        4: "Bonne reussite",
        5: "Tres bonne reussite",
        6: "Reussite heroique",
    }
    fate_compact = {
        "coeur": "allie/protection : un ami ou heros intervient, guerison, lien "
                 "renforce, ou miracle",
        "question": "quete secondaire debloquee (nouvel ami ou nouvel objet/totem), "
                    "a faire quand on veut",
        "soleil": "benediction : energie positive, pouvoir renforce/stabilise, "
                  "protection, amelioration durable",
        "etoile": "chance exceptionnelle : opportunite rare, decouverte precieuse, "
                  "ou recompense speciale",
        "exclamation": "aide arrive : une connaissance, ou un animal lie a un "
                       "totem, intervient",
        "spirale": "chaos/transformation : effet imprevisible, mutation, ou "
                   "consequence inattendue",
    }
    totems_compact = {
        "aigle": (["vol", "vision exceptionnelle", "vitesse aerienne",
                   "controle du vent", "rafales", "vol precis"],
                  "Ascension Absolue : monte tres haut, vue globale depuis le ciel"),
        "loup": (["force", "endurance", "odorat", "protection", "pistage"], ""),
        "renard": (["agilite", "discretion", "ruse", "precision", "tactique"], ""),
        "jaguar": (["vision nocturne", "intuition du danger", "perception spirituelle",
                    "lien mental avec Gabin", "conseils"],
                   "Fureur Astrale : 1x/aventure, boost reflexes/vitesse/lucidite"),
        "bond": (["sauts tres hauts", "sauts tres longs", "atterrissage maitrise",
                  "mobilite", "peut porter un allie"], ""),
        "profondeurs": (["respiration aquatique", "vitesse aquatique",
                         "resistance a la pression", "perception des vibrations",
                         "echo-sens"],
                        "Vague Primordiale : onde qui repousse et change les courants"),
    }

    protagonist_ref = CURRENT_STORY_CONFIG.get("protagonist_ref", "le joueur")
    lines = []
    lines.append("=== CONTEXTE IA NARRATRICE ===")
    if auto_mode:
        lines.append(f"Narrateur d'une histoire heroique interactive, 2e personne ('tu'), "
                      f"adressee a {protagonist_ref}. Mecaniques ci-dessous ; l'histoire se "
                      "poursuit directement dans cette conversation, evenement par evenement.")
    else:
        lines.append(f"Narrateur d'une histoire heroique interactive, 2e personne ('tu'), "
                      f"adressee a {protagonist_ref}. Mecaniques ci-dessous, puis l'histoire deja vecue.")
    lines.append("")
    lines.append("DE DE REUSSITE (1-6, jamais de fin d'histoire meme sur un 1, "
                  "toujours moyen de se rattraper) :")
    lines.append(" | ".join(f"{v}={success_compact[v]}" for v in SUCCESS_LABELS))
    lines.append("")
    lines.append("DE DU DESTIN (6 symboles ; le ? ne retombe pas tant qu'une quete "
                  "secondaire ouverte n'est pas terminee, une seule active a la fois) :")
    lines.append(" | ".join(f"{f['emoji']}{f['label']}={fate_compact[f['key']]}"
                             for f in FATE_FACES))
    lines.append("")
    lines.append("TOTEMS/ALLIES (symbole choisi sur le de de reussite) :")
    for t in TOTEMS:
        compact = totems_compact.get(t["key"])
        powers, special = compact if compact else (t["powers"], t["special"])
        suffix = f" — spe: {special}" if special else ""
        lines.append(f"{t['icon']}{t['label']}: {', '.join(powers)}{suffix}")
    for t in session.custom_totems:
        icon = t.get("emoji") or "\U0001F43E"
        powers_txt = ", ".join(t["powers"]) if t["powers"] else "(pouvoirs non precises)"
        special_txt = f" — spe: {t['special']}" if t.get("special") else ""
        lines.append(f"{icon}{t['label']} (ajoute par le joueur): {powers_txt}{special_txt}")
    fixed_allies_line = CURRENT_STORY_CONFIG.get("fixed_allies_line") or ""
    if fixed_allies_line:
        lines.append(fixed_allies_line)
    if not TOTEMS and not session.custom_totems:
        lines.append("(aucun pour l'instant -- tout reste a decouvrir en jouant)")
    lines.append(
        f"Jauge par totem/allie : +score obtenu (symbole unique) ou +1/symbole "
        f"(melange) a chaque lancer concerne -> pleine a {TOTEM_ENERGY_THRESHOLD} "
        "points, alors utilisable (capacite ou aide correspondante)."
    )
    lines.append("")
    lines.append(
        f"JAUGE DE MENACE : +3 sur un 1, -1 sur un 5 ou 6. A {THREAT_THRESHOLD} "
        "points, complication secondaire inattendue puis retombe a 0."
    )
    for paragraph in CURRENT_STORY_CONFIG.get("lore_paragraphs") or []:
        lines.append("")
        lines.append(paragraph)
    lines.append("")
    lines.append(
        "TON DU RECIT : l'histoire s'adresse a un enfant -- ecris de maniere "
        "vivante et chaleureuse, et parseme regulierement tes paragraphes de "
        "quelques emojis/petites icones pertinentes (\u2728\U0001F31F\U0001F43E "
        "etc.) pour illustrer les evenements et egayer le texte. Reste sobre : "
        "quelques emojis bien places par paragraphe suffisent, jamais un "
        "amoncellement d'icones qui alourdirait la reponse pour rien."
    )
    lines.append("")
    if auto_mode:
        lines.append(
            "ATTENDU DE TOI : histoire collaborative et immersive integrant directement "
            "les evenements de jeu que je te transmets (lancers de des, pouvoirs "
            "utilises, quetes...) ; a chaque action/incertitude tu me demandes "
            "explicitement de lancer le de de reussite, le de du destin, ou les deux, "
            f"et tu attends le resultat suivant avant de continuer ; tu t'adresses "
            f"toujours a {protagonist_ref} en 'tu'."
        )
    else:
        lines.append(
            "ATTENDU DE TOI : histoire collaborative et immersive integrant mes "
            "lancers ; a chaque action/incertitude tu me demandes de lancer "
            "reussite/destin/les deux et attends mon resultat avant de continuer ; "
            "si j'utilise une jauge pleine ou qu'un ?/! survient je te colle un "
            "petit bloc genere par l'appli pour te le signaler precisement ; a la "
            "fin de chaque chapitre tu me donnes un resume a coller dans l'appli "
            f"pour garder une trace permanente ; tu t'adresses toujours a "
            f"{protagonist_ref} en 'tu'."
        )
    return "\n".join(lines)


def build_full_prompt():
    """Assemble le bloc de mecaniques (mode manuel) + l'histoire deja
    vecue -- a copier tel quel pour demarrer une conversation avec une IA
    externe sans tout re-expliquer. Utilise par le bouton "Copier le
    prompt complet" (mode manuel, toujours disponible en secours meme
    quand la narration automatique est active)."""
    lines = [build_mechanics_context(auto_mode=False), "", "--- HISTOIRE DEJA VECUE ---"]
    story = session.story_log_text()
    lines.append(story if story else "(aucun chapitre enregistre pour l'instant, on commence "
                                       "une aventure toute neuve)")
    return "\n".join(lines)


def build_ai_kickoff_message():
    """Message complet envoye a l'IA quand on clique sur "Envoyer le prompt
    a l'IA" (mode automatique) : mecaniques (version narration auto) +
    histoire deja vecue si des chapitres ont ete enregistres manuellement,
    puis une instruction finale demandant explicitement de demarrer (ou
    poursuivre) le prochain chapitre.

    Sert a amorcer la conversation automatique sans attendre un premier
    lancer -- utile en tout debut d'aventure, ou pour la relancer avec le
    contexte complet apres un "Reinitialiser toute la partie"."""
    lines = [build_mechanics_context(auto_mode=True)]
    story = session.story_log_text()
    if story:
        lines.append("")
        lines.append("--- HISTOIRE DEJA VECUE ---")
        lines.append(story)
        lines.append("")
        lines.append("Commence maintenant le prochain chapitre de l'histoire, "
                      "dans la continuite directe de ce qui precede.")
    else:
        lines.append("")
        protagonist_ref = CURRENT_STORY_CONFIG.get("protagonist_ref", "le joueur")
        lines.append("Aucun chapitre n'a encore ete joue. Commence maintenant le tout "
                      "premier chapitre de cette aventure : plante le decor et presente "
                      f"la situation de depart de {protagonist_ref}, puis demande-moi le "
                      "premier lancer des que la situation l'exige.")
    return "\n".join(lines)


def render_continue_card_html():
    """Carte "Continuer l'aventure ailleurs" / "Demarrer ou relancer un
    chapitre" : deux comportements distincts selon le mode.
    - Sans cle Mistral (mode manuel) : bouton "copier le prompt complet"
      inchange, a coller dans une IA externe.
    - Avec une cle Mistral (mode automatique) : le meme bouton devient
      "Envoyer le prompt a l'IA" -- il transmet directement les mecaniques
      + l'histoire deja vecue a l'IA narratrice, avec une instruction de
      demarrer le prochain chapitre. C'est le moyen d'amorcer l'aventure
      avant le tout premier lancer (aucun lancer n'est necessaire pour que
      l'IA ait de quoi commencer a raconter)."""
    if has_mistral_key():
        return f"""
    <div class="card">
      <h2 style="margin-top:0">Demarrer ou relancer un chapitre</h2>
      <p class="sub" style="margin-bottom:8px;">
        Envoie les mecaniques du jeu et l'histoire deja vecue directement a
        l'IA, avec une instruction de demarrer le prochain chapitre -- utile
        avant le tout premier lancer, ou pour relancer le fil de l'histoire
        apres avoir reinitialise la conversation IA.
      </p>
      <button type="button" onclick="sendFullPromptToAi()">
        &#128640; Envoyer le prompt a l'IA
      </button>
      <p class="sub" style="margin:10px 0 0 0; font-size:0.8rem;">
        {len(session.story_log)} chapitre(s) enregistre(s) dans le journal.
      </p>
    </div>
    """
    return f"""
    <div class="card">
      <h2 style="margin-top:0">Continuer l'aventure ailleurs</h2>
      <p class="sub" style="margin-bottom:8px;">
        Un seul bouton pour tout transmettre (mecaniques + histoire deja vecue)
        a une IA narratrice, ici ou ailleurs, sans tout re-expliquer.
      </p>
      <textarea id="fullPromptBox" style="position:absolute; left:-9999px; top:-9999px;"></textarea>
      <button type="button" id="fullPromptBtn" onclick="copyFullPrompt()">
        &#128203; Copier le prompt complet pour une IA
      </button>
      <p class="sub" style="margin:10px 0 0 0; font-size:0.8rem;">
        {len(session.story_log)} chapitre(s) enregistre(s) dans le journal.
      </p>
    </div>
    """


def render_dice_result_html():
    last_success_rec = last_of("success")
    last_fate_rec = last_of("fate")
    success_val = last_success_rec["success"] if last_success_rec else None
    fate_key = last_fate_rec["fate"] if last_fate_rec else None
    success_pip_choice = None
    if last_success_rec:
        success_pip_choice = last_success_rec.get("pip_choice") or (
            [session.pip_symbol] * last_success_rec["success"]
        )

    success_used, fate_used = True, True
    if session.history:
        last_type = session.history[-1]["type"]
        if last_type == "success":
            fate_used = False
        elif last_type == "fate":
            success_used = False

    result_html = ""
    if session.history:
        last = session.history[-1]
        result_html = f'<div class="result-text">{session.describe_record(last).replace(chr(10), "<br>")}</div>'

    return f"""<div id="diceResultArea">
      <div class="dice-row">
        <div style="text-align:center;">
          {render_success_die(success_val, success_pip_choice, used=success_used)}
          <div class="die-caption">De de reussite</div>
          <button type="button" onclick="startRoll('success')">&#127922; Lancer</button>
        </div>
        <div style="text-align:center;">
          {render_fate_die(fate_key, used=fate_used)}
          <div class="die-caption">De du destin</div>
          <button type="button" onclick="startRoll('fate')" class="purple">&#128302; Lancer</button>
        </div>
      </div>
      <button type="button" onclick="startRoll('both')" class="blue" style="width:100%; margin-top:10px;">
        &#9889; Lancer les deux des ensemble
      </button>
      {result_html}
    </div>"""


def render_history_list_html():
    if not session.history:
        return '<div id="historyList"><p>(aucun lancer pour l\'instant)</p></div>'
    last = session.history[-1]
    text = session.describe_record(last)
    display_html = text.replace("\n", "<br>")
    return (
        f'<div id="historyList">'
        f'<div class="history-item">#{last["id"]} &mdash; {display_html}</div>'
        f'<textarea id="lastRollBox" class="copybox" readonly style="min-height:70px; margin-top:8px;">{text}</textarea>'
        f'<button type="button" id="lastRollCopyBtn" onclick="copyBox(\'lastRollBox\',\'lastRollCopyBtn\')">'
        f'Copier le dernier lancer</button>'
        f'</div>'
    )


def render_configure_key_page():
    """Page de configuration de la cle API Mistral : affichee juste apres
    le lancement de l'appli, avant meme le choix d'une histoire (voir
    index() et KEY_PAGE_SEEN plus haut). Reste aussi accessible a tout
    moment via son propre lien (route /configure_key), par exemple pour
    changer ou retirer la cle plus tard -- la cle etant partagee entre
    toutes les histoires, elle n'a plus besoin d'etre demandee une
    deuxieme fois une fois configuree ici."""
    key = get_mistral_key()
    if key:
        masked = ("*" * max(0, len(key) - 4)) + key[-4:]
        status_html = f"""
        <div class="key-status">
          <div class="key-status-dot">&#9989;</div>
          <div>
            <div style="font-weight:800;">Narration automatique activee</div>
            <div class="hint" style="margin-top:2px; font-family:monospace;">{masked}</div>
          </div>
        </div>
        <a class="btn" href="{url_for('index')}">Continuer vers les histoires &#8594;</a>
        <button type="button" class="btn secondary" onclick="toggleKeyForm()">Changer la cle</button>
        <form method="post" action="{url_for('do_configure_key')}" onsubmit="return confirm('Retirer la cle API et revenir au mode manuel ?');" style="margin-top:8px;">
          <input type="hidden" name="remove" value="1">
          <button type="submit" class="btn danger">Retirer la cle</button>
        </form>
        <div id="keyFormBox" style="display:none; margin-top:16px; padding-top:16px; border-top:2px dashed var(--line);">
          {_configure_key_form_fields()}
        </div>
        """
    else:
        status_html = f"""
        <p class="hint" style="margin:0 0 12px 0; font-size:0.92rem;">
          Colle ici une cle API Mistral gratuite (compte gratuit sur
          <a href="https://console.mistral.ai/" target="_blank" rel="noopener"
             style="color:var(--blue);">console.mistral.ai</a>, email + mot de
          passe, sans carte bancaire) pour que l'histoire s'ecrive toute
          seule a chaque lancer, quelle que soit l'histoire choisie
          ensuite.
        </p>
        <form method="post" action="{url_for('do_configure_key')}">
          {_configure_key_form_fields()}
          <button type="submit" class="btn">&#128273; Activer la narration automatique</button>
        </form>
        <a class="btn secondary" href="{url_for('skip_key_page')}">Passer pour l'instant &#8594;</a>
        """

    model_section_html = f"""
    <div style="margin-top:16px; padding-top:16px; border-top:2px dashed var(--line);">
      <div style="font-weight:800; margin-bottom:6px;">Modele utilise pour la narration</div>
      <p class="hint" style="margin:0 0 10px 0;">
        Plus le modele est riche, plus les histoires sont detaillees --
        mais aussi (legerement) plus couteux sur ton forfait Mistral.
        Modifiable a tout moment, meme en cours de partie.
      </p>
      <form method="post" action="{url_for('do_set_model')}">
        {_model_select_field()}
        <button type="submit" class="btn secondary">Enregistrer le modele</button>
      </form>
    </div>
    """

    return render_template_string(f"""
    <!DOCTYPE html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Cle API Mistral</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Bangers&family=Nunito:wght@400;700;800&display=swap" rel="stylesheet">
    <style>
      :root{{--ink:#14161a; --paper:#fbf3e1; --red:#e0263c; --blue:#1d3fd6; --yellow:#ffcd3c; --line:rgba(20,22,26,0.15);}}
      *{{box-sizing:border-box;}}
      html,body{{margin:0; padding:0;}}
      body{{
        font-family:'Nunito',-apple-system,sans-serif; color:var(--ink);
        background:#1b140c; padding-bottom:48px; min-height:100vh; min-height:100dvh;
      }}
      .hero{{width:100%; display:block; line-height:0;}}
      .hero img{{width:100%; height:auto; display:block;}}
      h1{{
        font-family:'Bangers',cursive; color:#fff; font-size:1.6rem; text-align:center;
        letter-spacing:1px; margin:16px 16px 14px 16px; text-shadow:0 2px 6px rgba(0,0,0,0.6);
      }}
      .card{{
        background:var(--paper); border:3px solid var(--ink); border-radius:14px;
        box-shadow:5px 5px 0 rgba(0,0,0,0.4); padding:20px;
        max-width:480px; margin:0 16px 0 16px;
      }}
      @media (min-width:520px){{ .card{{margin:0 auto;}} }}
      .hint{{font-size:0.82rem; opacity:0.75; font-weight:400;}}
      input[type=password], input[type=text]{{
        width:100%; font-family:monospace; font-size:1rem; padding:10px;
        border:2px solid var(--ink); border-radius:8px; background:#fff; color:var(--ink);
        margin-bottom:10px;
      }}
      .btn{{
        display:block; width:100%; margin-top:10px; padding:14px; text-align:center;
        font-family:'Bangers',cursive; font-size:1.15rem; letter-spacing:1px;
        background:var(--red); color:#fff; border:3px solid var(--ink); border-radius:10px;
        box-shadow:3px 3px 0 var(--ink); cursor:pointer; text-decoration:none;
      }}
      .btn:active{{transform:translate(2px,2px); box-shadow:1px 1px 0 var(--ink);}}
      .btn.secondary{{background:#fff; color:var(--ink);}}
      .btn.danger{{background:#8a1020;}}
      .key-status{{
        display:flex; align-items:center; gap:10px; margin-bottom:16px;
        background:#fff8ea; border:2px solid var(--ink); border-radius:10px; padding:10px 12px;
      }}
      .key-status-dot{{font-size:1.3rem;}}
    </style>
    </head>
    <body>
      <div class="hero"><img src="data:image/jpeg;base64,{KEY_PAGE_BG_B64}" alt="Le Livre des Mille Histoires"></div>
      <h1>&#128273; Cle API Mistral</h1>
      <div class="card">
        {status_html}
        {model_section_html}
      </div>
      <script>
        function toggleKeyForm(){{
          var box = document.getElementById('keyFormBox');
          if (box) {{ box.style.display = (box.style.display === 'none') ? 'block' : 'none'; }}
        }}
      </script>
    </body></html>
    """)


def _configure_key_form_fields():
    return (
        '<input type="password" name="api_key" placeholder="Cle API Mistral" autocomplete="off">'
    )


def _model_select_field():
    """Menu deroulant <select> propose sur la page de config de la cle,
    pre-selectionne sur le modele actuellement enregistre dans
    app_config.json (voir get_mistral_model() / MODEL_CHOICES dans
    mistral_client.py)."""
    current = get_mistral_model()
    options = []
    for value, label in mistral_client.MODEL_CHOICES:
        selected = " selected" if value == current else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    # Si le modele enregistre ne fait pas partie de MODEL_CHOICES (ex.
    # ancienne valeur "mistral-small-latest" d'avant cette mise a jour,
    # ou modele choisi manuellement dans le fichier), on l'ajoute quand
    # meme comme option pour ne pas le perdre silencieusement.
    if current not in dict(mistral_client.MODEL_CHOICES):
        options.insert(0, f'<option value="{current}" selected>{current} (actuel)</option>')
    return (
        '<select name="model" style="width:100%; font-size:1rem; padding:10px; '
        'border:2px solid var(--ink); border-radius:8px; background:#fff; '
        'color:var(--ink); margin-bottom:10px;">'
        + "".join(options) +
        "</select>"
    )


@app.route("/configure_key")
def configure_key_page():
    global KEY_PAGE_SEEN
    KEY_PAGE_SEEN = True
    return render_configure_key_page()


@app.route("/configure_key", methods=["POST"])
def do_configure_key():
    global KEY_PAGE_SEEN
    KEY_PAGE_SEEN = True
    if request.form.get("remove"):
        clear_mistral_key()
    else:
        key = (request.form.get("api_key") or "").strip()
        if key:
            set_mistral_key(key)
    return redirect(url_for("index"))


@app.route("/set_model", methods=["POST"])
def do_set_model():
    """Enregistre le modele Mistral choisi dans le menu deroulant de la
    page de config (partage entre toutes les histoires, comme la cle) --
    pris en compte des le prochain appel a l'IA, sans rien redemarrer."""
    global KEY_PAGE_SEEN
    KEY_PAGE_SEEN = True
    model = (request.form.get("model") or "").strip()
    if model:
        set_mistral_model(model)
    return redirect(url_for("configure_key_page"))


def _render_classic_die(value):
    """Le de de reussite (1-6, points classiques, pas de symbole/
    constellation lie a une histoire) : reutilise PIP_POSITIONS (grille
    3x3 partagee avec les des "de reussite" normaux) mais avec un simple
    point noir sur chaque pip, quelle que soit l'histoire active ou meme
    si aucune n'est active -- cette page est volontairement independante
    des histoires."""
    if value is None:
        return '<div class="classic-die"></div>'
    positions = PIP_POSITIONS[value]
    dots = "".join(
        f'<div class="classic-pip" style="grid-row:{r}; grid-column:{c};"></div>'
        for (r, c) in positions
    )
    return f'<div class="classic-die">{dots}</div>'


def render_classic_dice_page():
    """Page "Des classiques" : totalement independante des histoires
    (pas de totem, pas de sauvegarde de partie) -- le de de reussite
    (1-6, points classiques) et le de du destin, comme sur les pages
    d'histoire, avec l'historique des lancers. Pensee pour servir
    d'aide-memoire pendant une partie sur table (papier, plateau...),
    accessible directement depuis le selecteur d'histoire sans avoir
    besoin de choisir une histoire."""
    state = load_classic_dice_state()
    history = state["history"]
    last_success, last_fate = _last_classic_dice_values(history)

    dice_html = (
        f'<div style="text-align:center;">'
        f'{_render_classic_die(last_success)}'
        f'<div class="classic-die-caption">De classique</div>'
        f'</div>'
        f'<div style="text-align:center;">'
        f'{render_fate_die(last_fate, used=True)}'
        f'<div class="classic-die-caption">De du destin</div>'
        f'</div>'
    )

    if history:
        rows = []
        for entry in reversed(history):
            bits = []
            if entry.get("success") is not None:
                bits.append(f"D\u00e9 classique={entry['success']}")
            if entry.get("fate") is not None:
                face = FATE_BY_KEY[entry["fate"]]
                bits.append(f"Destin={face['emoji']} {face['label']}")
            if bits:
                rows.append(f'<div class="classic-history-item">#{entry["id"]} &mdash; ' + " | ".join(bits) + '</div>')
        history_html = "".join(rows) if rows else '<p class="classic-hint">(aucun lancer pour l\'instant)</p>'
    else:
        history_html = '<p class="classic-hint">(aucun lancer pour l\'instant)</p>'

    return render_template_string(f"""
    <!DOCTYPE html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Des classiques</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Bangers&family=Nunito:wght@400;700;800&display=swap" rel="stylesheet">
    <style>
      :root{{--ink:#14161a; --paper:#fbf3e1; --red:#e0263c; --blue:#1d3fd6; --purple:#6a4c93; --line:rgba(20,22,26,0.15);}}
      *{{box-sizing:border-box;}}
      html,body{{margin:0; padding:0;}}
      body{{
        font-family:'Nunito',-apple-system,sans-serif; color:#fff;
        min-height:100vh; min-height:100dvh; padding-bottom:48px;
        background:#000 url('data:image/jpeg;base64,{CLASSIC_DICE_BG_B64}') center/cover fixed no-repeat;
      }}
      .scrim{{
        min-height:100vh; min-height:100dvh;
        background:linear-gradient(to bottom, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.35) 30%, rgba(10,8,6,0.85) 100%);
        padding:18px 16px 32px 16px;
      }}
      .top-nav{{display:flex; align-items:center; gap:10px; margin-bottom:14px;}}
      .back-link{{
        color:#fff; text-decoration:none; font-size:1.4rem; text-shadow:0 2px 6px rgba(0,0,0,0.7);
        display:flex; align-items:center; justify-content:center;
        width:40px; height:40px; border-radius:50%; background:rgba(20,22,26,0.5);
      }}
      h1{{
        font-family:'Bangers',cursive; font-size:1.5rem; margin:0; letter-spacing:1px;
        text-shadow:0 2px 6px rgba(0,0,0,0.7);
      }}
      .card{{
        background:var(--paper); color:var(--ink); border:3px solid var(--ink);
        border-radius:14px; box-shadow:5px 5px 0 rgba(0,0,0,0.4); padding:20px;
        max-width:480px; margin:0 auto 16px auto;
      }}
      .classic-dice-row{{display:flex; justify-content:center; gap:16px; margin-bottom:6px; flex-wrap:wrap;}}
      .classic-die{{
        width:88px; height:88px; background:#fff; border:3px solid var(--ink);
        border-radius:14px; display:grid; grid-template-columns:repeat(3,1fr);
        grid-template-rows:repeat(3,1fr); padding:10px; box-shadow:2px 2px 0 rgba(0,0,0,0.25);
      }}
      .classic-pip{{
        width:14px; height:14px; border-radius:50%; background:var(--ink);
        justify-self:center; align-self:center;
      }}
      .classic-die-caption{{
        font-family:'Bangers',cursive; font-size:0.85rem; margin-top:6px; color:var(--ink); opacity:0.85;
      }}
      .classic-total{{text-align:center; font-family:'Bangers',cursive; font-size:1.3rem; margin-bottom:6px;}}
      /* Meme rendu que sur les pages d'histoire pour le de du destin
         (voir render_fate_die / .die-box.fate plus haut dans le fichier),
         reutilise ici tel quel. */
      .die-box{{
        width:150px; height:150px; background:#fff; border:4px solid var(--ink);
        border-radius:14px; box-shadow:5px 5px 0 var(--ink);
        display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:repeat(3,1fr);
        padding:10px; transform:rotate(-1deg); overflow:hidden;
      }}
      .die-box.fate{{
        display:flex; align-items:center; justify-content:center; flex-direction:column;
        transform:rotate(1deg); background:#fff7e0;
      }}
      .die-box.die-dimmed{{opacity:0.35; filter:grayscale(0.7); transition:opacity 0.2s, filter 0.2s;}}
      .fate-emoji{{font-size:3.6rem; line-height:1;}}
      .fate-label{{font-family:'Bangers',cursive; font-size:1rem; color:var(--purple); margin-top:4px; text-align:center;}}
      .emblem-placeholder{{
        grid-column:1 / -1; grid-row:1 / -1;
        width:100%; height:100%; display:flex; align-items:center; justify-content:center;
      }}
      .emblem-placeholder svg{{width:72%; height:72%; opacity:0.7;}}
      .btn{{
        display:block; width:100%; margin-top:10px; padding:14px; text-align:center;
        font-family:'Bangers',cursive; font-size:1.1rem; letter-spacing:1px;
        background:var(--red); color:#fff; border:3px solid var(--ink); border-radius:10px;
        box-shadow:3px 3px 0 var(--ink); cursor:pointer; text-decoration:none;
      }}
      .btn:active{{transform:translate(2px,2px); box-shadow:1px 1px 0 var(--ink);}}
      .btn.secondary{{background:#fff; color:var(--ink);}}
      .btn.danger{{background:#fff; color:#8a1020;}}
      .classic-hint{{opacity:0.7; font-size:0.9rem; text-align:center;}}
      .classic-history{{max-height:240px; overflow-y:auto; margin-top:4px;}}
      .classic-history-item{{
        padding:8px 4px; border-bottom:1px solid var(--line); font-family:monospace; font-size:0.95rem;
      }}
      .classic-history-item:last-child{{border-bottom:none;}}
    </style>
    </head>
    <body>
      <div class="scrim">
        <div class="top-nav">
          <a href="{url_for('change_story')}" class="back-link" aria-label="Retour">&#8592;</a>
          <h1>&#127922; Des classiques</h1>
        </div>

        <div class="card">
          <div class="classic-dice-row">{dice_html}</div>
          <form method="post" action="{url_for('do_classic_dice_roll')}">
            <input type="hidden" name="kind" value="both">
            <button type="submit" class="btn">&#9889; Lancer les deux d&eacute;s</button>
          </form>
          <div style="display:flex; gap:10px;">
            <form method="post" action="{url_for('do_classic_dice_roll')}" style="flex:1;">
              <input type="hidden" name="kind" value="success">
              <button type="submit" class="btn secondary">D&eacute; classique</button>
            </form>
            <form method="post" action="{url_for('do_classic_dice_roll')}" style="flex:1;">
              <input type="hidden" name="kind" value="fate">
              <button type="submit" class="btn secondary">D&eacute; du destin</button>
            </form>
          </div>
        </div>

        <div class="card">
          <div style="font-weight:800; margin-bottom:8px;">Historique</div>
          <div class="classic-history">{history_html}</div>
          <form method="post" action="{url_for('do_classic_dice_clear')}"
                onsubmit="return confirm('Effacer tout l\\'historique des des classiques ?');">
            <button type="submit" class="btn danger">Effacer l'historique</button>
          </form>
        </div>
      </div>
    </body></html>
    """)


@app.route("/classic_dice")
def classic_dice_page():
    return render_classic_dice_page()


@app.route("/classic_dice/roll", methods=["POST"])
def do_classic_dice_roll():
    kind = request.form.get("kind", "both")
    if kind not in ("success", "fate", "both"):
        kind = "both"
    roll_classic_dice(kind)
    return redirect(url_for("classic_dice_page"))


@app.route("/classic_dice/clear", methods=["POST"])
def do_classic_dice_clear():
    clear_classic_dice_history()
    return redirect(url_for("classic_dice_page"))


@app.route("/skip_key_page")
def skip_key_page():
    global KEY_PAGE_SEEN
    KEY_PAGE_SEEN = True
    return redirect(url_for("index"))


def render_story_selector_page():
    """Page de choix d'histoire : montree tant qu'aucune histoire n'a ete
    choisie dans cette session (premier lancement de l'appli), et
    accessible a tout moment via le lien 'Changer d'histoire' du jeu.

    Presentee comme un carrousel plein ecran : chaque histoire occupe tout
    l'ecran, et on passe de l'une a l'autre en glissant le doigt
    horizontalement (scroll-snap natif, sans dependance JS)."""
    order = stories.all_story_order()
    all_stories_map = stories.all_stories()
    slides = []
    dots = []
    for i, slug in enumerate(order):
        story = all_stories_map[slug]
        thumb = story.get("thumbnail_b64") or story["bg_image_b64"]
        delete_html = ""
        if story.get("is_custom"):
            # Poubelle affichee uniquement sur les histoires personnalisees
            # -- jamais sur Animorph/Poudlard, qui font partie de l'appli.
            # Placee en dehors du <a> (pas imbriquee dedans) et positionnee
            # par-dessus grace a z-index, pour que le clic sur la poubelle
            # ne declenche jamais la navigation "Toucher pour commencer".
            delete_html = f"""
            <form method="post" action="{url_for('do_delete_story', slug=slug)}"
                  class="delete-story-form"
                  onsubmit="return confirm('Supprimer definitivement {story['title']} ? La partie en cours, l\\'image de fond et le totem de depart associes seront effaces. Impossible a annuler.');">
              <button type="submit" class="delete-story-btn" aria-label="Supprimer cette histoire" title="Supprimer cette histoire">&#128465;&#65039;</button>
            </form>
            """
        slides.append(f"""
        <div class="slide">
          <a href="{url_for('select_story', slug=slug)}" class="slide-link">
            <img class="slide-bg" src="data:image/jpeg;base64,{thumb}" alt="{story['title']}">
            <div class="slide-overlay"></div>
            <div class="slide-content">
              <div class="slide-title">{story['title']}</div>
              <div class="slide-subtitle">{story['subtitle']}</div>
              <div class="slide-cta">Toucher pour commencer &#8594;</div>
            </div>
          </a>
          {delete_html}
        </div>
        """)
        dots.append(f'<span class="dot{" active" if i == 0 else ""}"></span>')

    # Derniere "diapositive" du carrousel : pas une histoire, mais un lien
    # vers la page de creation d'une nouvelle histoire.
    slides.append(f"""
    <div class="slide">
      <a href="{url_for('create_story_form')}" class="slide-link new-story-link">
        <div class="slide-overlay"></div>
        <div class="slide-content">
          <div class="new-story-icon">&#10133;</div>
          <div class="slide-title">Nouvelle histoire</div>
          <div class="slide-subtitle">Cree ton propre univers : image de fond, description, premier totem.</div>
          <div class="slide-cta">Toucher pour creer &#8594;</div>
        </div>
      </a>
    </div>
    """)
    dots.append('<span class="dot"></span>')
    return render_template_string(f"""
    <!DOCTYPE html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Choisis ton histoire</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Bangers&family=Nunito:wght@400;700;800&display=swap" rel="stylesheet">
    <style>
      html,body{{margin:0; padding:0; height:100%; overflow:hidden; background:#000;}}
      body{{font-family:'Nunito',sans-serif; color:#fff;}}

      .page-header{{
        position:fixed; top:0; left:0; right:0; z-index:5;
        padding:18px 16px 0 16px; text-align:center; pointer-events:none;
      }}
      .page-header h1{{
        font-family:'Bangers',cursive; font-size:1.4rem; margin:0;
        letter-spacing:1px; text-shadow:0 2px 6px rgba(0,0,0,0.7);
      }}

      .carousel{{
        display:flex; height:100vh; height:100dvh; width:100vw;
        overflow-x:auto; overflow-y:hidden;
        scroll-snap-type:x mandatory; -webkit-overflow-scrolling:touch;
        scrollbar-width:none; -ms-overflow-style:none;
      }}
      .carousel::-webkit-scrollbar{{display:none;}}

      .slide{{
        flex:0 0 100vw; width:100vw; height:100vh; height:100dvh;
        scroll-snap-align:start; scroll-snap-stop:always; position:relative;
      }}
      .slide-link{{
        display:block; width:100%; height:100%; position:relative;
        text-decoration:none; color:inherit;
      }}
      .slide-bg{{
        position:absolute; inset:0; width:100%; height:100%;
        object-fit:cover; display:block;
      }}
      .slide-overlay{{
        position:absolute; inset:0;
        background:linear-gradient(to bottom, rgba(0,0,0,0.25) 0%, rgba(0,0,0,0.05) 40%, rgba(0,0,0,0.9) 100%);
      }}
      .slide-content{{
        position:absolute; left:0; right:0; bottom:0;
        padding:0 28px 64px 28px; box-sizing:border-box; text-align:center;
      }}
      .slide-title{{
        font-family:'Bangers',cursive; font-size:2.6rem; letter-spacing:1px;
        margin:0 0 8px 0; text-shadow:0 2px 8px rgba(0,0,0,0.7);
      }}
      .slide-subtitle{{
        font-size:1rem; opacity:0.92; line-height:1.5;
        max-width:420px; margin:0 auto; text-shadow:0 1px 4px rgba(0,0,0,0.6);
      }}
      .slide-cta{{
        margin-top:18px; font-size:0.8rem; opacity:0.75;
        letter-spacing:0.5px; text-transform:uppercase;
      }}

      .dots{{
        position:fixed; left:0; right:0; bottom:20px; z-index:5;
        display:flex; justify-content:center; gap:8px; pointer-events:none;
      }}
      .dot{{
        width:8px; height:8px; border-radius:50%;
        background:rgba(255,255,255,0.35);
        transition:background 0.2s, transform 0.2s;
      }}
      .dot.active{{background:#fff; transform:scale(1.3);}}

      .new-story-link{{
        background:linear-gradient(160deg, #2a2118 0%, #14161a 100%);
      }}
      .new-story-icon{{
        width:64px; height:64px; border-radius:50%; margin:0 auto 16px auto;
        background:var(--yellow, #ffcd3c); color:#14161a;
        display:flex; align-items:center; justify-content:center;
        font-size:1.8rem; box-shadow:0 4px 14px rgba(0,0,0,0.5);
      }}

      .delete-story-form{{
        position:absolute; right:16px; bottom:16px; z-index:6; margin:0;
      }}
      .delete-story-btn{{
        width:46px; height:46px; border-radius:50%;
        background:rgba(20,22,26,0.65); border:2px solid rgba(255,255,255,0.55);
        color:#fff; font-size:1.25rem; cursor:pointer;
        display:flex; align-items:center; justify-content:center;
        box-shadow:0 2px 8px rgba(0,0,0,0.4);
      }}
      .delete-story-btn:active{{background:rgba(138,16,32,0.9); transform:scale(0.95);}}
    </style>
    </head>
    <body>
      <div class="page-header">
        <h1>&#127775; Choisis ton histoire</h1>
        <a href="{url_for('classic_dice_page')}" style="pointer-events:auto; position:absolute; top:14px; left:16px; width:34px; height:34px; display:flex; align-items:center; justify-content:center;">
          <img src="data:image/png;base64,{CLASSIC_DICE_ICON_B64}" alt="Des classiques" style="width:100%; height:100%; object-fit:contain; filter:brightness(0) invert(1) drop-shadow(0 1px 3px rgba(0,0,0,0.4)); mix-blend-mode:difference;">
        </a>
        <a href="{url_for('configure_key_page')}" style="pointer-events:auto; position:absolute; top:18px; right:16px; color:#fff; opacity:0.85; text-decoration:none; font-size:1.3rem; text-shadow:0 2px 6px rgba(0,0,0,0.7);">&#128273;</a>
      </div>
      <div class="carousel" id="carousel">
        {"".join(slides)}
      </div>
      <div class="dots" id="dots">
        {"".join(dots)}
      </div>
      <script>
        (function() {{
          var carousel = document.getElementById('carousel');
          var dots = document.querySelectorAll('#dots .dot');
          function updateActiveDot() {{
            if (!carousel.clientWidth) return;
            var idx = Math.round(carousel.scrollLeft / carousel.clientWidth);
            dots.forEach(function(d, i) {{ d.classList.toggle('active', i === idx); }});
          }}
          carousel.addEventListener('scroll', function() {{
            window.requestAnimationFrame(updateActiveDot);
          }}, {{ passive: true }});
        }})();
      </script>
    </body></html>
    """)


@app.route("/select_story/<slug>")
def select_story(slug):
    if slug in stories.all_stories():
        switch_story(slug)
    return redirect(url_for("index"))


@app.route("/delete_story/<slug>", methods=["POST"])
def do_delete_story(slug):
    """Supprime definitivement une histoire personnalisee (creee depuis
    la page "Nouvelle histoire" du selecteur) : sa sauvegarde, son image
    de fond, l'image de son totem de depart, et son entree dans
    custom_stories.json (voir stories.delete_custom_story()).

    Les histoires integrees (Animorph, Poudlard) ne sont jamais
    supprimables par cette route : delete_custom_story() renvoie False
    si le slug ne correspond a aucune histoire personnalisee, et on ne
    fait rien de plus dans ce cas -- le bouton poubelle n'est de toute
    facon affiche que sur les histoires personnalisees (voir
    render_story_selector_page()), cette verification est une securite
    supplementaire cote serveur.

    Si l'histoire supprimee etait l'histoire active, on revient a aucune
    histoire active (comme au tout premier lancement de l'appli) pour
    forcer le retour au selecteur plutot que de continuer a jouer sur une
    sauvegarde qui vient d'etre effacee."""
    global CURRENT_STORY, CURRENT_STORY_CONFIG, session
    stories.delete_custom_story(slug)
    if CURRENT_STORY == slug:
        CURRENT_STORY = None
        CURRENT_STORY_CONFIG = {}
        session = None
    return redirect(url_for("change_story"))


@app.route("/change_story")
def change_story():
    """Affiche le selecteur d'histoire a la demande (lien "Changer
    d'histoire" du jeu), sans modifier l'histoire actuellement active tant
    qu'un nouveau choix n'a pas ete fait -- on peut annuler en revenant en
    arriere sans rien perdre."""
    return render_story_selector_page()


def render_create_story_page(errors=None, values=None):
    """Page 'Nouvelle histoire' : image de fond, courte description
    d'univers (ajoutee au prompt envoye a l'IA, a la place de celle des
    autres histoires), et image du premier totem (affichee comme
    constellation sur le de de reussite). Page autonome, independante de
    toute histoire active (comme le selecteur), pour rester accessible
    avant meme qu'une histoire ait ete choisie."""
    values = values or {}
    error_html = ""
    if errors:
        items = "".join(f"<li>{e}</li>" for e in errors)
        error_html = f'<div class="form-errors"><ul>{items}</ul></div>'

    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    return render_template_string(f"""
    <!DOCTYPE html><html lang="fr"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <title>Nouvelle histoire</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Bangers&family=Nunito:wght@400;700;800&display=swap" rel="stylesheet">
    <style>
      :root{{--ink:#14161a; --paper:#fbf3e1; --red:#e0263c; --blue:#1d3fd6; --yellow:#ffcd3c; --line:rgba(20,22,26,0.15);}}
      *{{box-sizing:border-box;}}
      html,body{{margin:0; padding:0;}}
      body{{
        font-family:'Nunito',-apple-system,sans-serif; color:var(--ink);
        background:#2a2118; padding:20px 16px 48px 16px;
      }}
      h1{{font-family:'Bangers',cursive; color:#fff; font-size:1.7rem; text-align:center;
          letter-spacing:1px; margin:6px 0 18px 0; text-shadow:0 2px 6px rgba(0,0,0,0.6);}}
      .card{{
        background:var(--paper); border:3px solid var(--ink); border-radius:14px;
        box-shadow:5px 5px 0 rgba(0,0,0,0.4); padding:18px; max-width:520px; margin:0 auto 16px auto;
      }}
      label{{display:block; font-weight:800; margin:14px 0 6px 0;}}
      label:first-child{{margin-top:0;}}
      .hint{{font-size:0.82rem; opacity:0.75; margin-top:2px; font-weight:400;}}
      input[type=text], textarea{{
        width:100%; font-family:inherit; font-size:1rem; padding:10px;
        border:2px solid var(--ink); border-radius:8px; background:#fff; color:var(--ink);
      }}
      textarea{{min-height:110px; resize:vertical;}}
      input[type=file]{{
        width:100%; font-family:inherit; font-size:0.95rem; padding:8px;
        border:2px dashed var(--ink); border-radius:8px; background:#fff8ea;
      }}
      .btn{{
        display:inline-block; width:100%; margin-top:20px; padding:14px; text-align:center;
        font-family:'Bangers',cursive; font-size:1.2rem; letter-spacing:1px;
        background:var(--red); color:#fff; border:3px solid var(--ink); border-radius:10px;
        box-shadow:3px 3px 0 var(--ink); cursor:pointer; text-decoration:none;
      }}
      .btn:active{{transform:translate(2px,2px); box-shadow:1px 1px 0 var(--ink);}}
      .btn.secondary{{background:#fff; color:var(--ink); box-shadow:none; margin-top:10px;}}
      .form-errors{{
        background:#fff0f0; border:2px solid var(--red); color:#8a1020;
        border-radius:8px; padding:10px 14px; margin-bottom:14px; font-weight:700;
      }}
      .form-errors ul{{margin:0; padding-left:18px;}}
    </style>
    </head>
    <body>
      <h1>&#10024; Cree ta propre histoire</h1>
      <div class="card">
        {error_html}
        <form method="post" action="{url_for('do_create_story')}" enctype="multipart/form-data">
          <label>Titre de l'histoire</label>
          <input type="text" name="title" value="{esc(values.get('title'))}" placeholder="Ex : La Foret des Chuchoteurs" required>

          <label>Sous-titre <span class="hint">(optionnel, affiche sous le titre)</span></label>
          <input type="text" name="subtitle" value="{esc(values.get('subtitle'))}" placeholder="Ex : Une aventure au coeur d'une foret enchantee">

          <label>Image de fond</label>
          <div class="hint">Utilisee comme fond de tout l'ecran de jeu pour cette histoire.</div>
          <input type="file" name="bg_image" accept="image/*" required>

          <label>Description de l'univers</label>
          <div class="hint">Quelques phrases sur le monde, le ton, le personnage... Ajoutees au contexte envoye a l'IA narratrice, a la place de celui des autres histoires.</div>
          <textarea name="lore_text" placeholder="Ex : L'aventure se deroule dans une foret magique peuplee d'esprits anciens...">{esc(values.get('lore_text'))}</textarea>

          <label>Nom du premier totem</label>
          <div class="hint">Ce totem sert de constellation sur le de de reussite, et de premiere jauge du jeu.</div>
          <input type="text" name="totem_label" value="{esc(values.get('totem_label'))}" placeholder="Ex : Pierre-Lune" required>

          <label>Image du premier totem</label>
          <div class="hint">Affichee sur les faces du de de reussite pour cette histoire.</div>
          <input type="file" name="totem_image" accept="image/*" required>

          <label>Pouvoirs du totem <span class="hint">(optionnel)</span></label>
          <div class="hint">Separes par des virgules -- comme pour un totem ajoute en cours de partie.</div>
          <input type="text" name="totem_powers" value="{esc(values.get('totem_powers'))}" placeholder="Ex : Vision nocturne, Discretion, Agilite">

          <label>Capacite speciale <span class="hint">(optionnel)</span></label>
          <input type="text" name="totem_special" value="{esc(values.get('totem_special'))}" placeholder="Ex : Une fois par aventure, devient invisible quelques secondes">

          <button type="submit" class="btn">Creer l'histoire et commencer &#8594;</button>
        </form>
        <a class="btn secondary" href="{url_for('change_story')}">&larr; Retour au choix des histoires</a>
      </div>
    </body></html>
    """)


@app.route("/create_story")
def create_story_form():
    return render_create_story_page()


@app.route("/create_story", methods=["POST"])
def do_create_story():
    title = (request.form.get("title") or "").strip()
    subtitle = (request.form.get("subtitle") or "").strip()
    lore_text = (request.form.get("lore_text") or "").strip()
    totem_label = (request.form.get("totem_label") or "").strip()
    totem_powers = (request.form.get("totem_powers") or "").strip()
    totem_special = (request.form.get("totem_special") or "").strip()
    bg_file = request.files.get("bg_image")
    totem_file = request.files.get("totem_image")

    values = {
        "title": title, "subtitle": subtitle, "lore_text": lore_text,
        "totem_label": totem_label, "totem_powers": totem_powers, "totem_special": totem_special,
    }
    errors = []
    if not title:
        errors.append("Le titre de l'histoire est obligatoire.")
    if not lore_text:
        errors.append("La description de l'univers est obligatoire.")
    if not totem_label:
        errors.append("Le nom du premier totem est obligatoire.")
    if not bg_file or not bg_file.filename:
        errors.append("Une image de fond est obligatoire.")
    if not totem_file or not totem_file.filename:
        errors.append("Une image pour le premier totem est obligatoire.")

    if errors:
        return render_create_story_page(errors=errors, values=values)

    bg_bytes = bg_file.read()
    bg_ext = bg_file.filename.rsplit(".", 1)[-1].lower() if "." in bg_file.filename else "jpg"
    # L'image du totem passe par le mecanisme deja existant des totems
    # ajoutes en cours de partie (meme dossier, memes extensions
    # autorisees) -- switch_story() s'en servira comme totem de depart au
    # tout premier lancement de cette histoire.
    totem_image_filename = _save_totem_image(totem_file)

    slug = stories.create_custom_story(
        title=title, subtitle=subtitle, lore_text=lore_text,
        bg_image_bytes=bg_bytes, bg_image_ext=bg_ext,
        totem_label=totem_label, totem_image_filename=totem_image_filename,
        totem_powers=totem_powers, totem_special=totem_special,
    )

    switch_story(slug)
    return redirect(url_for("index"))


@app.route("/")
def index():
    if CURRENT_STORY is None:
        if not KEY_PAGE_SEEN:
            return redirect(url_for("configure_key_page"))
        return render_story_selector_page()

    symbol_picker_html = render_symbol_picker_html()

    body = f"""
    <div class="card">
      <form method="post" action="{url_for('do_roll')}" id="rollForm" onsubmit="return false;">
        <input type="hidden" name="action" id="rollActionField" value="">
        {render_dice_result_html()}
        {render_threat_gauge_html()}
        {render_narrator_note_html("")}
      </form>
      {symbol_picker_html}
      {render_allowed_values_picker_html()}
      {render_allowed_fate_picker_html()}
    </div>

    <div class="card">
      <h2 style="margin-top:0">Narration automatique</h2>
      {render_ai_panel_html()}
    </div>

    <div class="card">
      <h2 style="margin-top:0">Jauges totemiques</h2>
      <p class="sub" style="margin-bottom:8px;">Chaque totem/allie a sa propre jauge. Pleine, elle devient utilisable.</p>
      {render_totem_gauges_html()}

      <div style="margin-top:18px; padding-top:14px; border-top:2px dashed var(--line);">
        <label>Ajouter un totem (rare, a utiliser quand l'histoire l'introduit)</label>
        <input type="text" id="totemNameInput" placeholder="Nom (ex: Corbeau Ombreux)">
        <input type="text" id="totemPowersInput" placeholder="Pouvoirs, separes par des virgules">
        <input type="text" id="totemSpecialInput" placeholder="Capacite speciale (optionnel)">
        <input type="text" id="totemEmojiInput" placeholder="Emoji (optionnel, ex: &#129415;)">
        <label style="font-weight:400; opacity:0.8;">Ou une image a toi (optionnel, remplace l'emoji) :</label>
        <input type="file" id="totemImageInput" accept="image/*">
        <button type="button" onclick="addCustomTotem()">&#10133; Ajouter ce totem</button>
      </div>
    </div>

    <div class="card">
      <h2 style="margin-top:0">Quetes secondaires</h2>
      {render_side_quests_html()}
    </div>

    {render_continue_card_html()}

    <div class="card">
      <h2 style="margin-top:0">Dernier lancer</h2>
      {render_history_list_html()}
      <label style="margin-top:14px;">Coller ici le resume de chapitre recu de l'IA narratrice</label>
      <textarea id="storyEntryInput" placeholder="Colle ici le bloc recu a la fin d'un chapitre"></textarea>
      <button type="button" onclick="addStoryEntry()">&#128218; Ajouter au journal de l'histoire</button>
      <a class="btn secondary" href="{url_for('show_story')}">Voir le journal complet</a>
      <div style="margin-top:16px;">
        <button type="button" class="secondary" onclick="doUndo()">
          &#8617; Annuler le dernier lancer
        </button>
        <button type="button" class="danger" onclick="doClear()">Effacer tout l'historique</button>
      </div>
    </div>
    """
    return layout("Pret pour l'aventure !", body)


@app.route("/set_pip_symbol", methods=["POST"])
def do_set_pip_symbol():
    key = request.form.get("symbol", "")
    session.set_pip_symbol(key)
    return render_symbol_picker_html()


@app.route("/set_pip_mode", methods=["POST"])
def do_set_pip_mode():
    mode = request.form.get("mode", "")
    session.set_pip_mode(mode)
    return render_symbol_picker_html()


@app.route("/toggle_enabled_symbol", methods=["POST"])
def do_toggle_enabled_symbol():
    key = request.form.get("symbol", "")
    session.toggle_enabled_symbol(key)
    return render_symbol_picker_html()


@app.route("/toggle_allowed_value", methods=["POST"])
def do_toggle_allowed_value():
    try:
        value = int(request.form.get("value", ""))
    except (TypeError, ValueError):
        value = None
    if value is not None:
        session.toggle_allowed_value(value)
    return render_allowed_values_picker_html()


@app.route("/reset_allowed_values", methods=["POST"])
def do_reset_allowed_values():
    session.reset_allowed_values()
    return render_allowed_values_picker_html()


@app.route("/toggle_allowed_fate", methods=["POST"])
def do_toggle_allowed_fate():
    key = request.form.get("key", "")
    session.toggle_allowed_fate(key)
    return render_allowed_fate_picker_html()


@app.route("/reset_allowed_fate", methods=["POST"])
def do_reset_allowed_fate():
    session.reset_allowed_fate()
    return render_allowed_fate_picker_html()


@app.route("/set_mistral_key", methods=["POST"])
def do_set_mistral_key():
    key = request.form.get("key", "")
    set_mistral_key(key)
    return render_ai_panel_html()


@app.route("/clear_mistral_key", methods=["POST"])
def do_clear_mistral_key():
    clear_mistral_key()
    return render_ai_panel_html()


@app.route("/debug_images")
def debug_images():
    """Route de diagnostic (pas un lien visible dans l'appli) : ouvre
    cette adresse dans un navigateur normal du telephone (Chrome...)
    pendant que l'appli tourne en fond -- ex. http://127.0.0.1:5011/debug_images
    (adapter le port a android_bridge.PORT) -- pour verifier directement
    si Pillow est actif et quelle taille fait reellement l'image de fond
    de l'histoire en cours, sans avoir besoin d'un cable/logcat."""
    info = {
        "pillow_disponible": image_utils._PIL_AVAILABLE,
        "pillow_erreur_import": image_utils._PIL_IMPORT_ERROR,
        "histoire_active": CURRENT_STORY,
    }
    bg = (CURRENT_STORY_CONFIG or {}).get("bg_image_b64") or ""
    info["bg_base64_longueur_caracteres"] = len(bg)
    if bg:
        try:
            raw = __import__("base64").b64decode(bg)
            info["bg_taille_octets"] = len(raw)
            if image_utils._PIL_AVAILABLE:
                from PIL import Image
                img = Image.open(__import__("io").BytesIO(raw))
                info["bg_dimensions_pixels"] = list(img.size)
        except Exception as e:
            info["erreur_decodage"] = str(e)
    return jsonify(info)


@app.route("/totem_images/<path:filename>")
def totem_image(filename):
    """Sert les images de totems ajoutees par le joueur, sauvegardees en
    local sur l'appareil (jamais envoyees ailleurs).

    IMPORTANT : Flask resout un chemin de dossier RELATIF (comme
    TOTEM_IMAGES_DIR) par rapport au dossier d'installation de
    l'application (current_app.root_path), PAS par rapport au repertoire
    de travail courant (celui bascule sur le stockage interne inscriptible
    par android_bridge.start_server()). Sur Android, ces deux dossiers
    sont differents : les images sont bien ECRITES dans le stockage
    interne (_save_totem_image ci-dessous utilise un chemin relatif classique,
    non affecte par ce souci), mais Flask allait ensuite les chercher au
    mauvais endroit pour les SERVIR -- d'ou l'icone cassee. On force donc
    ici un chemin absolu, calcule par rapport au repertoire de travail
    courant, pour que lecture et ecriture pointent toujours au meme endroit.
    """
    return send_from_directory(os.path.abspath(TOTEM_IMAGES_DIR), filename)


def _save_totem_image(file_storage):
    """Sauvegarde une image de totem uploadee, avec un nom de fichier
    genere (pour eviter toute collision), et renvoie ce nom de fichier
    (ou None si aucun fichier valide n'a ete fourni). L'image est
    redimensionnee/recompressee au passage (voir image_utils.py) -- elle
    n'a besoin que d'etre nette a une taille d'icone (1em/1.15rem)."""
    if not file_storage or not file_storage.filename:
        return None
    ext = ""
    if "." in file_storage.filename:
        ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_TOTEM_IMAGE_EXTS:
        ext = "png"
    raw_bytes = file_storage.read()
    resized_bytes, resized_ext = image_utils.resize_totem_bytes(raw_bytes)
    if resized_ext:
        ext = resized_ext
    os.makedirs(TOTEM_IMAGES_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(TOTEM_IMAGES_DIR, filename), "wb") as f:
        f.write(resized_bytes)
    return filename


@app.route("/add_custom_totem", methods=["POST"])
def do_add_custom_totem():
    """Ajoute un nouveau totem en cours de partie : nom, pouvoirs,
    capacite speciale optionnelle, et soit un emoji soit une image
    fournie par le joueur (l'image prend le pas sur l'emoji si les deux
    sont donnes)."""
    label = request.form.get("label", "")
    powers = request.form.get("powers", "")
    special = request.form.get("special", "")
    emoji = request.form.get("emoji", "")
    image_filename = _save_totem_image(request.files.get("image"))
    session.add_custom_totem(label, powers_text=powers, special=special,
                              emoji=emoji, image_filename=image_filename)
    return jsonify({
        "gauges": render_totem_gauges_html(),
        "symbol_picker": render_symbol_picker_html(),
        "totem_row": render_totem_row_html(),
        "totem_info": _totem_modal_info(),
    })


@app.route("/remove_custom_totem", methods=["POST"])
def do_remove_custom_totem():
    """Retire un totem ajoute par le joueur (jauge et image comprises)."""
    key = request.form.get("key", "")
    entry = next((t for t in session.custom_totems if t["key"] == key), None)
    removed = session.remove_custom_totem(key)
    if removed and entry and entry.get("image"):
        try:
            os.remove(os.path.join(TOTEM_IMAGES_DIR, entry["image"]))
        except OSError:
            pass
    return jsonify({
        "gauges": render_totem_gauges_html(),
        "symbol_picker": render_symbol_picker_html(),
        "totem_row": render_totem_row_html(),
        "totem_info": _totem_modal_info(),
    })


@app.route("/reset_ai_conversation", methods=["POST"])
def do_reset_ai_conversation():
    """Reinitialise l'histoire ACTIVE dans l'etat qu'elle avait a
    l'installation de l'appli : conversation IA, mais aussi historique des
    des, jauges totemiques, menace, quetes secondaires et totems
    personnalises repartent de zero. Pour une histoire avec un seed fourni
    (Animorph), on revient a ce seed plutot qu'a une partie totalement
    vide ; pour les autres (Poudlard, histoires personnalisees), le totem
    de depart est re-ajoute exactement comme au tout premier lancement.
    Le reste de la page (jauges, menace, quetes...) ayant change en meme
    temps que la conversation, le front-end recharge la page entiere apres
    cet appel plutot que de ne rafraichir que le panneau IA."""
    reset_story_to_origin()
    return jsonify({"ok": True})


def reset_story_to_origin():
    """Reconstruit la sauvegarde de l'histoire active exactement comme au
    tout premier lancement de l'appli : supprime la partie existante, puis
    recopie le seed fourni (Animorph) ou re-ajoute le totem de depart
    (Poudlard / histoires personnalisees) -- meme logique que la toute
    premiere branche de switch_story(), reutilisee ici a la demande plutot
    qu'au changement d'histoire."""
    global session

    story = CURRENT_STORY_CONFIG or {}

    if os.path.exists(dice_engine.SAVE_FILE):
        os.remove(dice_engine.SAVE_FILE)

    seed_file = story.get("seed_state_file")
    if seed_file and os.path.exists(seed_file):
        shutil.copyfile(seed_file, dice_engine.SAVE_FILE)

    new_session = DiceSession()
    was_loaded = new_session.load()

    default_totem = story.get("default_totem")
    if not was_loaded and default_totem and default_totem.get("label"):
        key = new_session.add_custom_totem(
            default_totem["label"],
            powers_text=default_totem.get("powers_text", ""),
            special=default_totem.get("special", ""),
            image_filename=default_totem.get("image_filename"),
        )
        if key:
            new_session.pip_symbol = key

    new_session.save()
    session = new_session


@app.route("/send_full_prompt", methods=["POST"])
def do_send_full_prompt():
    """Bouton "Envoyer le prompt a l'IA" : transmet mecaniques + histoire
    deja vecue, avec une instruction de demarrer/poursuivre le chapitre.
    Permet d'amorcer la conversation automatique sans attendre un lancer."""
    _, ai_error = run_ai_narrator(build_ai_kickoff_message())
    return jsonify({"ai_story": render_ai_panel_html(ai_error)})


@app.route("/send_ai_message", methods=["POST"])
def do_send_ai_message():
    """Message libre envoye a l'IA a tout moment (demarrer l'aventure,
    decrire une action de Gabin entre deux lancers...)."""
    text = (request.form.get("text") or "").strip()
    ai_error = None
    if text:
        _, ai_error = run_ai_narrator(text)
    return jsonify({"ai_story": render_ai_panel_html(ai_error)})


@app.route("/roll", methods=["POST"])
def do_roll():
    action = request.form.get("action")
    record = None
    if action == "success":
        record = session.roll_success("")
    elif action == "fate":
        record = session.roll_fate("")
    elif action == "both":
        record = session.roll_both("")

    _, ai_error = run_ai_narrator(ai_event_text(record))

    return jsonify({
        "dice": render_dice_result_html(),
        "history": render_history_list_html(),
        "gauges": render_totem_gauges_html(),
        "threat": render_threat_gauge_html(),
        "quests": render_side_quests_html(),
        "narrator_note": render_narrator_note_html(narrator_note_for_record(record)),
        "ai_story": render_ai_panel_html(ai_error),
    })


@app.route("/undo", methods=["POST"])
def do_undo():
    session.undo_last()
    return jsonify({
        "dice": render_dice_result_html(),
        "history": render_history_list_html(),
        "gauges": render_totem_gauges_html(),
        "threat": render_threat_gauge_html(),
        "quests": render_side_quests_html(),
        "narrator_note": render_narrator_note_html(""),
    })


@app.route("/clear", methods=["POST"])
def do_clear():
    session.clear_history()
    return jsonify({
        "dice": render_dice_result_html(),
        "history": render_history_list_html(),
        "gauges": render_totem_gauges_html(),
        "threat": render_threat_gauge_html(),
        "quests": render_side_quests_html(),
        "narrator_note": render_narrator_note_html(""),
    })


@app.route("/use_totem_energy", methods=["POST"])
def do_use_totem_energy():
    key = request.form.get("key", "")
    effect_text = ""
    spent = session.spend_totem_energy(key)
    if spent:
        if key in ("patte", "baguette"):
            label = TOTEMS_BY_KEY.get(key, {}).get("label", key)
            session.second_souffle(note=f"Relance ({label})")
            effect_text = (f"\U0001f504 {label} ! Le dernier lancer de reussite "
                            "est annule et relance a l'instant.")
        elif key in ALLY_HELP_TEXT:
            effect_text = ALLY_HELP_TEXT[key]
        elif key in TOTEMS_BY_KEY and TOTEMS_BY_KEY[key]["special"]:
            effect_text = TOTEMS_BY_KEY[key]["special"]
        elif key in TOTEMS_BY_KEY:
            powers = ", ".join(TOTEMS_BY_KEY[key]["powers"])
            effect_text = f"Le pouvoir du totem {TOTEMS_BY_KEY[key]['label']} se manifeste : {powers}."
        else:
            # Totem ajoute par le joueur (Animorph ou Poudlard) : aucune fiche
            # fixe, on construit le texte a partir de ce que le joueur a
            # lui-meme renseigne a l'ajout.
            info = session.all_symbols().get(key)
            if info:
                if info.get("special"):
                    effect_text = info["special"]
                elif info.get("powers"):
                    effect_text = f"Le pouvoir de {info['label']} se manifeste : {', '.join(info['powers'])}."
                else:
                    effect_text = f"{info['label']} intervient pour aider !"
    ai_error = None
    if spent and effect_text:
        _, ai_error = run_ai_narrator(effect_text)
    return jsonify({
        "spent": spent,
        "effect": effect_text,
        "dice": render_dice_result_html(),
        "history": render_history_list_html(),
        "gauges": render_totem_gauges_html(),
        "narrator_note": render_narrator_note_html(effect_text),
        "ai_story": render_ai_panel_html(ai_error),
    })


@app.route("/complete_side_quest", methods=["POST"])
def do_complete_side_quest():
    try:
        quest_id = int(request.form.get("quest_id", ""))
    except (TypeError, ValueError):
        quest_id = None
    if quest_id is not None:
        session.complete_side_quest(quest_id)
    return render_side_quests_html()


@app.route("/add_story_entry", methods=["POST"])
def do_add_story_entry():
    text = request.form.get("text", "")
    added = session.add_story_entry(text)
    return jsonify({"added": added, "chapters": len(session.story_log)})


@app.route("/story")
def show_story():
    text = session.story_log_text() or "(aucun chapitre enregistre pour l'instant)"
    body = f"""
    <div class="card">
      <p>Journal complet de l'histoire, chapitre par chapitre.</p>
      <textarea id="storybox" class="copybox" readonly style="min-height:320px;">{text}</textarea>
      <button id="storycopybtn" onclick="copyBox('storybox','storycopybtn')">Copier</button>
      <a class="btn secondary" href="{url_for('index')}">&larr; Retour</a>
    </div>
    """
    return layout("Journal de l'histoire", body)


@app.route("/full_prompt")
def full_prompt():
    return build_full_prompt(), 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    print("Ouvrez votre navigateur sur : http://127.0.0.1:5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
