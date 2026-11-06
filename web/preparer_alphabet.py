# PyTalk web — prépare l'alphabet (dactylologie LSF) pour la PWA
#
# Télécharge les 26 photos « LSF Lettre A…Z » de Wikimedia Commons
# (par Clairette, CC BY-SA), calcule les points de la main avec le MÊME
# modèle MediaPipe que l'application web, et écrit :
#   data/alphabet.json et data/alphabet/*.jpg
#
# Utilisation (depuis la racine du projet, environnement virtuel activé) :
#   python web/preparer_alphabet.py

import io
import json
import os
import re
import string
import time
import urllib.parse
import urllib.request

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision
from PIL import Image

WEB = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(WEB, "data")
AGENT = "PyTalk/0.2 (LSF learning app; https://github.com/AnthonyMarcelin/pytalk-LSF)"
API = "https://commons.wikimedia.org/w/api.php"

# Lettres qui demandent un mouvement : l'app ne juge que la forme de la main.
MOUVEMENTS = {
    "J": "Puis fais le mouvement montré par les flèches.",
    "P": "Main vers le bas, puis fais le mouvement montré par la flèche.",
    "Q": "Main pointée vers le bas.",
    "Z": "Puis trace un Z dans l'air avec l'index.",
}


def telecharger(url, essais=5):
    for essai in range(essais):
        try:
            requete = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                return reponse.read()
        except Exception:
            if essai == essais - 1:
                raise
            time.sleep(2 * (essai + 1))


def infos_commons():
    titres = "|".join(f"File:LSF Lettre{c}.jpg" for c in string.ascii_uppercase)
    url = API + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "imageinfo",
        "iiprop": "url|extmetadata", "titles": titres, "maxlag": 5,
    })
    pages = json.loads(telecharger(url))["query"]["pages"].values()
    infos = {}
    for page in pages:
        lettre = re.search(r"Lettre(.)\.jpg$", page["title"]).group(1)
        info = page["imageinfo"][0]
        infos[lettre] = {
            "url": info["url"].split("?")[0],
            "licence": info["extmetadata"].get("LicenseShortName", {}).get("value", "CC BY-SA 3.0"),
            "page": "https://commons.wikimedia.org/wiki/"
                    + urllib.parse.quote(page["title"].replace(" ", "_"), safe=":()'"),
        }
    return infos


def detections(detecteur, image):
    """Détecte la main à plusieurs tailles et marges.

    Les photos sont petites (200 × 150) : selon l'agrandissement, MediaPipe
    place certains doigts (pouce, doigts repliés) un peu différemment. On garde
    toutes les détections : l'app compare la main de l'utilisateur à la plus
    proche, ce qui la rend aussi tolérante que la détection elle-même.
    """
    variantes = []
    for marge in (0, 0.15, 0.3, 0.5):
        for agrandissement in (2, 3, 4, 5):
            m = int(max(image.size) * marge)
            fond = Image.new("RGB", (image.width + 2 * m, image.height + 2 * m), (223, 227, 230))
            fond.paste(image, (m, m))
            grande = fond.resize((fond.width * agrandissement, fond.height * agrandissement), Image.LANCZOS)
            res = detecteur.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(grande)))
            if not res.hand_landmarks:
                continue
            ratio = grande.width / grande.height
            variantes.append({
                "monde": [[round(p.x, 5), round(p.y, 5), round(p.z, 5)] for p in res.hand_world_landmarks[0]],
                "plan": [[round(p.x * ratio, 4), round(p.y, 4)] for p in res.hand_landmarks[0]],
            })
    return variantes


def main():
    os.makedirs(os.path.join(SORTIE, "alphabet"), exist_ok=True)
    detecteur = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=os.path.join(WEB, "models", "hand_landmarker.task")),
        running_mode=vision.RunningMode.IMAGE, num_hands=1, min_hand_detection_confidence=0.1))
    infos = infos_commons()
    lettres = []
    for lettre in string.ascii_uppercase:
        chemin = os.path.join(SORTIE, "alphabet", f"{lettre}.jpg")
        if not os.path.exists(chemin):
            with open(chemin, "wb") as f:
                f.write(telecharger(infos[lettre]["url"]))
            time.sleep(1)
        variantes = detections(detecteur, Image.open(chemin).convert("RGB"))
        if not variantes:
            print(f"  {lettre} : aucune main détectée, lettre ignorée")
            continue
        lettres.append({
            "cle": lettre.lower(),
            "nom": lettre,
            "image": f"data/alphabet/{lettre}.jpg",
            "variantes": variantes,
            "mouvement": MOUVEMENTS.get(lettre, ""),
            "auteur": "Clairette",
            "licence": infos[lettre]["licence"],
            "page": infos[lettre]["page"],
        })
        print(f"  {lettre} : {len(variantes)} détections")
    with open(os.path.join(SORTIE, "alphabet.json"), "w", encoding="utf-8") as f:
        json.dump(lettres, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(lettres)} lettres écrites dans data/alphabet.json")


if __name__ == "__main__":
    main()
