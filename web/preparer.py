# PyTalk web — prépare les mots pour la PWA
#
# Pour chaque mot téléchargé par `src/references.py` (dossier src/refs) :
#   - convertit la vidéo en MP4 H.264 (lisible par Safari / iPhone) ;
#   - calcule les points des mains et des épaules avec les MÊMES modèles
#     MediaPipe que l'application web (models/*.task), pour que les scores
#     du téléphone soient comparables ;
#   - écrit data/mots.json, data/points/*.json et data/videos/*.mp4.
#
# Utilisation (depuis la racine du projet, environnement virtuel activé) :
#   pip install imageio-ffmpeg
#   python src/references.py chat chien     # (facultatif) ajouter des mots
#   python web/preparer.py

import json
import math
import os
import subprocess
import sys

import cv2
import imageio_ffmpeg
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(RACINE, "web")
REFS = os.path.join(RACINE, "src", "refs")
SORTIE = os.path.join(WEB, "data")
MODELES = os.path.join(WEB, "models")
NB_POINTS = 44


def creer_detecteurs():
    mains = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=os.path.join(MODELES, "hand_landmarker.task")),
        running_mode=vision.RunningMode.VIDEO, num_hands=2,
        min_hand_detection_confidence=0.5, min_tracking_confidence=0.5))
    corps = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=os.path.join(MODELES, "pose_landmarker_lite.task")),
        running_mode=vision.RunningMode.VIDEO, num_poses=1))
    return mains, corps


def points_image(res_mains, res_corps, largeur, hauteur):
    """Même format que web/comparaison.js : 44 points [x, y] ou None.

    0-1 : épaules (gauche, droite de la personne), 2-22 : main gauche,
    23-43 : main droite. x est en « unités de hauteur » (x * largeur / hauteur).
    Vérifié sur une vidéo Elix (comparaison avec la main au repos, côté épaule
    gauche) : sur une image non retournée, « Left » est bien la main gauche.
    """
    ratio = largeur / hauteur
    pts = [None] * NB_POINTS
    if res_corps.pose_landmarks:
        lm = res_corps.pose_landmarks[0]
        pts[0] = [lm[11].x * ratio, lm[11].y]
        pts[1] = [lm[12].x * ratio, lm[12].y]
    for main, cote in zip(res_mains.hand_landmarks, res_mains.handedness):
        debut = 2 if cote[0].category_name == "Left" else 23
        if pts[debut] is not None:  # deux mains du même côté : on garde la 1re
            continue
        for i, p in enumerate(main):
            pts[debut + i] = [p.x * ratio, p.y]
    return pts


def arrondir(pts):
    return [None if p is None else [round(p[0], 4), round(p[1], 4)] for p in pts]


def convertir(source, destination):
    """MP4 H.264, sans son, 640 px de large max, démarrage rapide."""
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", source,
        "-an", "-vf", "scale='min(640,iw)':-2", "-c:v", "libx264", "-preset", "slow",
        "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", destination,
    ], check=True)


def analyser(video):
    """Points de chaque image de la vidéo, avec les modèles du web."""
    mains, corps = creer_detecteurs()
    capture = cv2.VideoCapture(video)
    fps = capture.get(cv2.CAP_PROP_FPS) or 25
    sequence, n = [], 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        h, w = image.shape[:2]
        image_mp = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        t = int(n * 1000 / fps)
        sequence.append(arrondir(points_image(mains.detect_for_video(image_mp, t),
                                              corps.detect_for_video(image_mp, t), w, h)))
        n += 1
    capture.release()
    mains.close()
    corps.close()
    return sequence, fps


def main():
    chemin_mots = os.path.join(REFS, "mots.json")
    if not os.path.exists(chemin_mots):
        sys.exit("Aucun mot téléchargé : lance d'abord  python src/references.py")
    with open(chemin_mots, encoding="utf-8") as f:
        mots = json.load(f)
    os.makedirs(os.path.join(SORTIE, "videos"), exist_ok=True)
    os.makedirs(os.path.join(SORTIE, "points"), exist_ok=True)
    liste = []
    for cle in sorted(mots, key=lambda k: mots[k]["nom"].lower()):
        fiche = mots[cle]
        print(f"  {fiche['nom']}")
        variantes = []
        for v in fiche["variantes"]:
            base = os.path.splitext(os.path.basename(v["video"]))[0]
            mp4 = os.path.join(SORTIE, "videos", base + ".mp4")
            pts = os.path.join(SORTIE, "points", base + ".json")
            if not os.path.exists(mp4):
                convertir(os.path.join(REFS, v["video"]), mp4)
            if not os.path.exists(pts):
                sequence, fps = analyser(mp4)
                with open(pts, "w") as f:
                    json.dump({"fps": fps, "points": sequence}, f, separators=(",", ":"))
            variantes.append({
                "video": f"data/videos/{base}.mp4", "points": f"data/points/{base}.json",
                "auteur": v["auteur"], "licence": v["licence"], "page": v["page"], "source": v["source"],
            })
        liste.append({"cle": cle, "nom": fiche["nom"], "variantes": variantes})
    with open(os.path.join(SORTIE, "mots.json"), "w", encoding="utf-8") as f:
        json.dump(liste, f, ensure_ascii=False, indent=1)
    print(f"{len(liste)} mots prêts dans web/data. Pense à changer VERSION dans web/sw.js.")


if __name__ == "__main__":
    main()
