# -*- coding: utf-8 -*-
"""
IMAGE_UTILS - redimensionnement/compression des images de fond et de
totems (module autonome, comme mistral_client.py).
=====================================================================
CORRIGE LE BUG "image trop grosse pour etre visible" (Poudlard, nouvelles
histoires) : une photo prise directement au telephone (plusieurs milliers
de pixels de large, plusieurs Mo) embarquee telle quelle dans la page peut
tout simplement ne pas s'afficher sur Android -- la WebView (comme la
plupart des moteurs mobiles) a une limite de taille de texture pour tout
ce qui est achemine au GPU (fond d'ecran CSS compris), generalement autour
de 2048 ou 4096 pixels sur le plus grand cote. Au-dela, l'image reste
invisible (ou s'affiche de facon cassee/enorme), meme si le CSS demande un
cadrage "cover" qui devrait pourtant toujours "rentrer" a l'ecran.

L'image de fond d'Animorph (bg_animorph_data.py) a ete preparee "a la
main" a l'origine avec une technique precise : la photo de depart (au
format paysage) a ete posee sur un fond flou d'elle-meme pour obtenir un
format portrait, SANS jamais recadrer les cotes -- c'est ce qui fait
qu'on y voit toujours la composition complete (personnage + les 6
animaux). Les images de fond preparees autrement (un simple recadrage
centre, ou pas de traitement du tout) perdent au contraire une partie de
la composition, ou se retrouvent zoomees/coupees une fois affichees en
plein ecran sur un telephone (cas rencontre avec Poudlard).

Ce module applique desormais AUTOMATIQUEMENT cette meme technique
("letterbox" flou, voir _letterbox_to_portrait() plus bas) a TOUTE image
de fond -- Poudlard comprise, et toute nouvelle histoire creee depuis
l'application -- avant de la redimensionner et de l'enregistrer. Une
image DEJA au format portrait (photo prise verticalement, par exemple)
n'est en revanche jamais modifiee par cette etape : le traitement ne
s'applique qu'aux images trop "larges" (paysage ou proches du carre).

Necessite Pillow (pip install pillow) -- voir la note en bas de ce
fichier pour app/build.gradle. Si Pillow n'est pas installe, toutes les
fonctions ci-dessous renvoient l'image d'origine sans y toucher (aucun
crash de l'appli), mais le probleme de taille peut alors persister.

  >>> IMPORTANT : ajoutez "pillow" a cote de "flask" dans les
  >>> dependances pip d'app/build.gradle (bloc `pip { install "flask" }`
  >>> devient `pip { install "flask"; install "pillow" }`), sinon ce
  >>> module ne pourra rien redimensionner du tout sur le telephone.
"""

import base64
import io

try:
    from PIL import Image, ImageFilter
    _PIL_AVAILABLE = True
    _PIL_IMPORT_ERROR = None
except Exception as e:  # capture large (pas seulement ImportError) : une
    # bibliotheque native manquante/incompatible peut remonter sous
    # d'autres formes (ex. OSError/dlopen). On garde le message exact
    # pour pouvoir le consulter via /debug_images plutot que de deviner.
    _PIL_AVAILABLE = False
    _PIL_IMPORT_ERROR = f"{type(e).__name__}: {e}"


# Cote le plus long, en pixels, au-dela duquel une image de FOND est
# redimensionnee. 1600px est largement suffisant pour remplir n'importe
# quel ecran de telephone en "cover", tout en restant tres en dessous des
# limites de texture GPU qui rendent une image invisible sur certains
# telephones/WebView quand elle est trop grande.
MAX_BG_DIMENSION = 1600
BG_JPEG_QUALITY = 82

# Format (largeur / hauteur) cible pour une image de fond "portrait" --
# c'est le ratio de l'image d'Animorph (900x1500 = 0.6), qui a fait ses
# preuves a l'usage. Une image dont le ratio est deja proche de cette
# valeur (ou plus etroite, donc deja plus "verticale") est consideree
# comme deja portrait et n'est JAMAIS modifiee par _letterbox_to_portrait().
TARGET_BG_RATIO = 0.6
# Marge de tolerance : une image legerement plus large que la cible
# (jusqu'a 5%) est quand meme laissee telle quelle, pour eviter de
# "letterboxer" une image deja quasi verticale pour rien.
_PORTRAIT_TOLERANCE = 1.05
# Rayon du flou applique au fond de remplissage (haut/bas). Assez fort
# pour que ce ne soit clairement pas une "vraie" partie de l'image (l'oeil
# ne doit pas chercher a y voir un detail), sans non plus donner un aplat
# trop uniforme qui trancherait avec la photo nette du centre.
BG_LETTERBOX_BLUR_RADIUS = 45

# Les icones de totem sont affichees minuscules (1em / 1.15rem) : nul
# besoin d'une grande resolution. Les garder petites accelere aussi le
# chargement de chaque page de jeu (elles sont reaffichees a chaque lancer).
MAX_TOTEM_DIMENSION = 400
TOTEM_JPEG_QUALITY = 85


def _has_alpha(img):
    return img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)


def _resize_if_needed(img, max_dimension):
    w, h = img.size
    if max(w, h) > max_dimension:
        ratio = max_dimension / float(max(w, h))
        new_size = (max(1, round(w * ratio)), max(1, round(h * ratio)))
        img = img.resize(new_size, Image.LANCZOS)
    return img


def _letterbox_to_portrait(img, target_ratio=TARGET_BG_RATIO, blur_radius=BG_LETTERBOX_BLUR_RADIUS):
    """Convertit une image trop "large" (paysage, ou proche du carre) en
    portrait en ajoutant du remplissage FLOU en haut et en bas -- jamais en
    recadrant les cotes -- pour ne perdre aucun element de la composition
    d'origine (personnage, decor...). C'est exactement la technique utilisee
    a la main pour l'image d'Animorph, appliquee ici automatiquement.

    IMPORTANT : une image DEJA suffisamment verticale (ratio largeur/hauteur
    <= target_ratio, a la tolerance pres) est renvoyee TELLE QUELLE, sans
    aucune modification -- ni recadrage, ni ajout de bandes. Le but de cette
    fonction est uniquement de rattraper les images trop larges, pas de
    forcer un format unique."""
    w, h = img.size
    if h <= 0:
        return img
    ratio = w / float(h)
    if ratio <= target_ratio * _PORTRAIT_TOLERANCE:
        # Deja portrait (ou assez proche) : on ne touche a rien.
        return img

    canvas_w = w
    canvas_h = round(w / target_ratio)

    # Fond de remplissage : une version de l'image agrandie pour recouvrir
    # tout le canevas (comme un "cover" CSS), puis floutee -- comble les
    # bandes vides en haut/bas avec une continuite visuelle de la meme
    # image plutot qu'une couleur unie ou du noir.
    canvas_ratio = canvas_w / float(canvas_h)
    if ratio > canvas_ratio:
        bg_h = canvas_h
        bg_w = max(1, round(bg_h * ratio))
    else:
        bg_w = canvas_w
        bg_h = max(1, round(bg_w / ratio))
    background = img.resize((bg_w, bg_h), Image.LANCZOS)
    left = (bg_w - canvas_w) // 2
    top = (bg_h - canvas_h) // 2
    background = background.crop((left, top, left + canvas_w, top + canvas_h))
    background = background.filter(ImageFilter.GaussianBlur(blur_radius))

    # Premier plan : l'image d'origine ENTIERE, non recadree, centree
    # verticalement par-dessus le fond floute.
    canvas = background.convert("RGB")
    paste_y = (canvas_h - h) // 2
    canvas.paste(img, (0, paste_y))
    return canvas


def resize_bg_bytes(raw_bytes, max_dimension=MAX_BG_DIMENSION, quality=BG_JPEG_QUALITY):
    """Pour une image de FOND (toujours affichee en JPEG opaque -- voir
    'data:image/jpeg;base64,' cote dice_web.py) : complete en portrait si
    besoin (voir _letterbox_to_portrait -- ne modifie pas les images deja
    verticales), redimensionne et reencode systematiquement en JPEG, sans
    transparence. En cas d'echec (format illisible, Pillow absent...),
    renvoie les bytes d'origine tels quels plutot que de planter."""
    if not _PIL_AVAILABLE or not raw_bytes:
        return raw_bytes
    try:
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        img = _letterbox_to_portrait(img)
        img = _resize_if_needed(img, max_dimension)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()
    except Exception:
        return raw_bytes


def resize_bg_b64(b64_str, max_dimension=MAX_BG_DIMENSION, quality=BG_JPEG_QUALITY):
    """Meme chose que resize_bg_bytes, mais prend et renvoie une chaine
    base64 -- pratique pour les images de fond deja integrees dans le
    code (bg_animorph_data.py / bg_poudlard_data.py)."""
    if not b64_str:
        return b64_str
    try:
        raw = base64.b64decode(b64_str)
    except Exception:
        return b64_str
    return base64.b64encode(resize_bg_bytes(raw, max_dimension, quality)).decode("ascii")


def resize_totem_bytes(raw_bytes, max_dimension=MAX_TOTEM_DIMENSION, quality=TOTEM_JPEG_QUALITY):
    """Pour une image de TOTEM : redimensionne, et conserve la
    transparence si la source en a une (reencodee en PNG dans ce cas),
    sinon reencode en JPEG. Renvoie un tuple (bytes, extension). En cas
    d'echec, renvoie (raw_bytes, None) -- l'appelant garde alors
    l'extension d'origine du fichier uploade."""
    if not _PIL_AVAILABLE or not raw_bytes:
        return raw_bytes, None
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        has_alpha = _has_alpha(img)
        img = _resize_if_needed(img, max_dimension)
        out = io.BytesIO()
        if has_alpha:
            img.convert("RGBA").save(out, format="PNG", optimize=True)
            return out.getvalue(), "png"
        img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue(), "jpg"
    except Exception:
        return raw_bytes, None
