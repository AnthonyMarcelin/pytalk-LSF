# PyTalk — mode Apprendre : regarde un signe LSF, imite-le, obtiens un score
#
# Lancement (depuis le dossier src) : python apprendre.py
# Caméra : variable d'environnement PYTALK_CAMERA (0 par défaut).

import os
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import json
import random
import sys
import time

import cv2
import numpy as np
import mediapipe as mp
from PIL import Image, ImageDraw, ImageFont

import references
from comparaison import comparer, mains_actives, points_image

DOSSIER = os.path.dirname(os.path.abspath(__file__))
PROGRES = os.path.join(DOSSIER, "progres.json")
FENETRE = "PyTalk - Apprendre la LSF"

LARGEUR, HAUTEUR = 1280, 720
PANNEAU_L, PANNEAU_H = 620, 465
X_GAUCHE, X_DROITE, Y_PANNEAUX = 13, 647, 78

FOND = (53, 44, 43)        # couleurs en BGR, reprises de l'accueil PyTalk
TEXTE = (247, 249, 233)
DISCRET = (215, 204, 189)
ACCENT = (88, 69, 64)
VERT = (110, 200, 90)
ORANGE = (40, 150, 245)
ROUGE = (70, 70, 230)

COMPTE_A_REBOURS = 3.0
FIN_SANS_MAINS = 0.6       # secondes sans mains qui terminent l'essai
DUREE_MAX = 6.0

# Codes renvoyés par cv2.waitKeyEx pour les flèches (macOS, Windows, Linux)
HAUT = {63232, 2490368, 65362}
BAS = {63233, 2621440, 65364}


# ---------- texte avec accents (OpenCV ne sait pas les dessiner) ----------

_polices = {}


def police(taille):
    if taille not in _polices:
        for nom in ("/System/Library/Fonts/Supplemental/Arial.ttf", "arial.ttf",
                    "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/TTF/DejaVuSans.ttf"):
            try:
                _polices[taille] = ImageFont.truetype(nom, taille)
                break
            except OSError:
                continue
        else:
            _polices[taille] = ImageFont.load_default(size=taille)
    return _polices[taille]


def ecrire(image, textes):
    """textes : liste de (texte, (x, y), taille, couleur BGR[, "centre"])."""
    if not textes:
        return image
    pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    dessin = ImageDraw.Draw(pil)
    for t in textes:
        texte, (x, y), taille, couleur = t[:4]
        if len(t) > 4 and t[4] == "centre":
            x -= dessin.textlength(texte, font=police(taille)) / 2
        dessin.text((x, y), texte, font=police(taille), fill=couleur[::-1])
    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def coller(canvas, image, x, y):
    """Colle une image dans un panneau en gardant ses proportions."""
    h, w = image.shape[:2]
    echelle = min(PANNEAU_L / w, PANNEAU_H / h)
    image = cv2.resize(image, (int(w * echelle), int(h * echelle)))
    h, w = image.shape[:2]
    cv2.rectangle(canvas, (x, y), (x + PANNEAU_L, y + PANNEAU_H), (0, 0, 0), -1)
    ox, oy = x + (PANNEAU_L - w) // 2, y + (PANNEAU_H - h) // 2
    canvas[oy:oy + h, ox:ox + w] = image


# ---------- données ----------

def charger_progres():
    try:
        with open(PROGRES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def sauver_progres(progres):
    with open(PROGRES, "w", encoding="utf-8") as f:
        json.dump(progres, f, ensure_ascii=False, indent=1)


class Reference:
    """Une vidéo de référence : images, points MediaPipe et crédits."""

    def __init__(self, variante):
        self.credit = f"{variante['auteur']} — {variante['licence']} — Wikimedia Commons"
        donnees = np.load(os.path.join(references.DOSSIER, variante["points"]))
        self.points = donnees["points"]
        self.fps = float(donnees["fps"]) or 25.0
        video = cv2.VideoCapture(os.path.join(references.DOSSIER, variante["video"]))
        self.images = []
        while True:
            ok, image = video.read()
            if not ok:
                break
            self.images.append(image)
        video.release()
        self.duree = max(len(self.images), 1) / self.fps

    def image(self, t):
        """Image de la vidéo au temps t (en boucle), avec les mains dessinées."""
        if not self.images:
            return np.zeros((480, 640, 3), np.uint8)
        i = int(t * self.fps) % len(self.images)
        image = self.images[i].copy()
        if i < len(self.points):
            dessiner_mains(image, self.points[i])
        return image


def dessiner_mains(image, pts):
    h, w = image.shape[:2]
    for zone, couleur in ((slice(2, 23), (230, 66, 245)), (slice(23, 44), (66, 117, 245))):
        main = pts[zone]
        if np.isnan(main[0, 0]):
            continue
        for x, y in main:
            cv2.circle(image, (int(x * h), int(y * h)), 3, couleur, -1)


def dessiner_resultats(image, resultats):
    d, s = mp.solutions.drawing_utils, mp.solutions.drawing_styles
    for main in (resultats.left_hand_landmarks, resultats.right_hand_landmarks):
        if main:
            d.draw_landmarks(image, main, mp.solutions.holistic.HAND_CONNECTIONS,
                             s.get_default_hand_landmarks_style(), s.get_default_hand_connections_style())


# ---------- application ----------

class Appli:
    def __init__(self, camera):
        self.mots = references.charger_mots()
        self.cles = sorted(self.mots, key=lambda k: references.cle(self.mots[k]["nom"]))
        self.progres = charger_progres()
        self.camera = camera
        self.holistic = mp.solutions.holistic.Holistic(min_detection_confidence=0.5, min_tracking_confidence=0.5)
        self.choix = 0
        self.etat = "menu"
        self.defi = False          # mode défi : le mot sans la vidéo
        self.aide_vue = False
        self.refs = []
        self.debut = time.time()
        self.essai, self.images_essai = [], []
        self.resultat = None
        self.derniere_main = 0.0

    # --- transitions ---
    def ouvrir(self, cle, defi=False):
        self.cle = cle
        self.defi, self.aide_vue = defi, False
        self.refs = [Reference(v) for v in self.mots[cle]["variantes"]]
        self.variante = 0
        self.passer("regarder")

    def passer(self, etat):
        self.etat = etat
        self.debut = time.time()
        if etat == "enregistrer":
            self.essai, self.images_essai = [], []
            self.derniere_main = 0.0
            self.mains_vues = False

    def mot_au_hasard(self):
        """Mode défi : plus de chances de tomber sur les mots mal réussis."""
        poids = []
        for k in self.cles:
            meilleur = self.progres.get(k, {}).get("meilleur")
            poids.append(100 if meilleur is None else 110 - meilleur)
        return random.choices(self.cles, weights=poids)[0]

    def noter(self):
        essai = np.array(self.essai, dtype=np.float32)
        # comparé à chaque variante disponible : on garde la meilleure
        resultats = [comparer(r.points, essai) for r in self.refs]
        meilleur = max(range(len(resultats)), key=lambda i: resultats[i]["score"])
        self.resultat = resultats[meilleur]
        self.variante = meilleur
        fiche = self.progres.setdefault(self.cle, {"meilleur": 0, "essais": 0})
        fiche["essais"] += 1
        fiche["meilleur"] = max(fiche["meilleur"], self.resultat["score"])
        fiche["dernier"] = self.resultat["score"]
        sauver_progres(self.progres)
        self.passer("resultat")

    # --- boucle ---
    def lancer(self):
        cv2.namedWindow(FENETRE, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(FENETRE, LARGEUR, HAUTEUR)
        while True:
            ok, image = self.camera.read()
            if not ok:
                image = None
                resultats = None
            else:
                h, w = image.shape[:2]
                resultats = self.holistic.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
                if self.etat == "enregistrer":
                    self.enregistrer(image, resultats, w, h)
                dessiner_resultats(image, resultats)
                image = cv2.flip(image, 1)  # effet miroir, plus naturel pour imiter
            canvas = self.dessiner(image)
            cv2.imshow(FENETRE, canvas)
            touche = cv2.waitKeyEx(1)
            if touche != -1 and not self.touche(touche):
                break
            if cv2.getWindowProperty(FENETRE, cv2.WND_PROP_VISIBLE) < 1:
                break
        cv2.destroyAllWindows()

    def enregistrer(self, image, resultats, w, h):
        maintenant = time.time()
        pts = points_image(resultats, w, h)
        if mains_actives(pts):
            self.mains_vues = True
            self.derniere_main = maintenant
        self.essai.append(pts)
        self.images_essai.append(cv2.resize(image, (w * 360 // h, 360)))
        ecoule = maintenant - self.debut
        if (self.mains_vues and maintenant - self.derniere_main > FIN_SANS_MAINS and ecoule > 1.0) \
                or ecoule > max(DUREE_MAX, 2.5 * self.refs[0].duree):
            self.noter()

    def touche(self, code):
        """Renvoie False pour quitter."""
        c = chr(code).lower() if code < 128 else ""
        if self.etat == "menu":
            if c in ("q",) or code == 27:
                return False
            if code in HAUT or c == "p":
                self.choix = (self.choix - 1) % max(len(self.cles), 1)
            elif code in BAS or c == "s":
                self.choix = (self.choix + 1) % max(len(self.cles), 1)
            elif c in (" ", "\r", "\n") and self.cles:
                self.ouvrir(self.cles[self.choix])
            elif c == "d" and self.cles:
                self.ouvrir(self.mot_au_hasard(), defi=True)
            return True
        if code == 27 or c == "m":
            self.passer("menu")
        elif c == "q":
            return False
        elif self.etat == "regarder":
            if c == " ":
                self.passer("compte")
            elif c == "v" and self.defi:
                self.aide_vue = True
            elif c == "a" and len(self.refs) > 1:
                self.variante = (self.variante + 1) % len(self.refs)
        elif self.etat == "compte" and c == " ":
            self.passer("regarder")
        elif self.etat == "enregistrer" and c == " ":
            self.noter()
        elif self.etat == "resultat":
            if c in (" ", "r"):
                self.passer("compte")
            elif c == "n":
                if self.defi:
                    self.ouvrir(self.mot_au_hasard(), defi=True)
                else:
                    self.choix = (self.cles.index(self.cle) + 1) % len(self.cles)
                    self.ouvrir(self.cles[self.choix])
            elif c == "v":
                self.aide_vue = True
                self.passer("regarder")
        return True

    # --- affichage ---
    def dessiner(self, camera):
        canvas = np.full((HAUTEUR, LARGEUR, 3), FOND, np.uint8)
        textes = []
        t = time.time() - self.debut
        if self.etat == "menu":
            self.dessiner_menu(canvas, textes)
        else:
            nom = self.mots[self.cle]["nom"]
            titre = f"Défi : signe « {nom} »" if self.defi else f"« {nom} »"
            textes.append((titre, (LARGEUR // 2, 18), 38, TEXTE, "centre"))
            self.dessiner_reference(canvas, textes, t)
            if self.etat == "resultat":
                self.dessiner_resultat(canvas, textes, t)
            else:
                self.dessiner_camera(canvas, textes, camera, t)
        return ecrire(canvas, textes)

    def dessiner_menu(self, canvas, textes):
        textes.append(("Apprendre la langue des signes (LSF)", (LARGEUR // 2, 18), 38, TEXTE, "centre"))
        cv2.rectangle(canvas, (X_GAUCHE, Y_PANNEAUX), (X_GAUCHE + PANNEAU_L, Y_PANNEAUX + PANNEAU_H), ACCENT, -1)
        if not self.cles:
            textes.append(("Aucun signe téléchargé.", (X_GAUCHE + 20, Y_PANNEAUX + 20), 26, TEXTE))
            textes.append(("Lance d'abord : python references.py", (X_GAUCHE + 20, Y_PANNEAUX + 60), 22, DISCRET))
        par_page = 13
        debut = max(0, min(self.choix - par_page // 2, len(self.cles) - par_page))
        for ligne, k in enumerate(self.cles[debut:debut + par_page]):
            i = debut + ligne
            y = Y_PANNEAUX + 12 + ligne * 34
            if i == self.choix:
                cv2.rectangle(canvas, (X_GAUCHE + 6, y - 3), (X_GAUCHE + PANNEAU_L - 6, y + 31), DISCRET, -1)
            couleur = FOND if i == self.choix else TEXTE
            textes.append((self.mots[k]["nom"], (X_GAUCHE + 20, y), 24, couleur))
            fiche = self.progres.get(k)
            if fiche:
                m = fiche["meilleur"]
                etiquette = f"meilleur {m}  ·  {fiche['essais']} essai{'s' if fiche['essais'] > 1 else ''}"
                textes.append((etiquette, (X_GAUCHE + 330, y + 4), 18, couleur if i == self.choix else couleur_score(m)))
        connus = sum(1 for k in self.cles if self.progres.get(k, {}).get("meilleur", 0) >= 70)
        textes.append((f"{connus} / {len(self.cles)} signes réussis (score ≥ 70)", (X_DROITE, Y_PANNEAUX + 20), 26, TEXTE))
        aide = ["↑ ↓   choisir un mot", "Espace   apprendre ce mot",
                "D   défi : un mot au hasard, sans la vidéo", "Q   quitter", "",
                "Ajouter des mots :", "python references.py --liste",
                "python references.py chat chien"]
        for n, ligne in enumerate(aide):
            textes.append((ligne, (X_DROITE, Y_PANNEAUX + 80 + n * 34), 22, DISCRET))
        self.pied(textes, "Vidéos : Wikimedia Commons (Elix, Laura Jauvert, Lingua Libre), licences libres.")

    def dessiner_reference(self, canvas, textes, t):
        cache = self.defi and not self.aide_vue
        if cache:
            cv2.rectangle(canvas, (X_GAUCHE, Y_PANNEAUX), (X_GAUCHE + PANNEAU_L, Y_PANNEAUX + PANNEAU_H), ACCENT, -1)
            textes.append(("?", (X_GAUCHE + PANNEAU_L // 2, Y_PANNEAUX + 150), 120, DISCRET, "centre"))
            textes.append(("V : voir la vidéo (aide)", (X_GAUCHE + PANNEAU_L // 2, Y_PANNEAUX + 330), 22, DISCRET, "centre"))
            return
        ref = self.refs[self.variante]
        coller(canvas, ref.image(t), X_GAUCHE, Y_PANNEAUX)
        legende = "Modèle"
        if len(self.refs) > 1:
            legende += f"  (version {self.variante + 1}/{len(self.refs)})"
        textes.append((legende, (X_GAUCHE + 10, Y_PANNEAUX + 8), 20, TEXTE))

    def dessiner_camera(self, canvas, textes, camera, t):
        if camera is None:
            cv2.rectangle(canvas, (X_DROITE, Y_PANNEAUX), (X_DROITE + PANNEAU_L, Y_PANNEAUX + PANNEAU_H), (0, 0, 0), -1)
            textes.append(("Caméra indisponible", (X_DROITE + PANNEAU_L // 2, Y_PANNEAUX + 200), 28, TEXTE, "centre"))
            textes.append(("Essaie PYTALK_CAMERA=1", (X_DROITE + PANNEAU_L // 2, Y_PANNEAUX + 240), 20, DISCRET, "centre"))
        else:
            coller(canvas, camera, X_DROITE, Y_PANNEAUX)
        textes.append(("Toi", (X_DROITE + 10, Y_PANNEAUX + 8), 20, TEXTE))
        if self.etat == "regarder":
            consigne = "Regarde le modèle, puis appuie sur Espace pour essayer."
            if self.defi and not self.aide_vue:
                consigne = "Te souviens-tu de ce signe ? Espace pour essayer."
            touches = "Espace essayer   ·   M menu"
            if len(self.refs) > 1 and not (self.defi and not self.aide_vue):
                touches += "   ·   A autre version"
            self.pied(textes, consigne, touches)
        elif self.etat == "compte":
            reste = COMPTE_A_REBOURS - t
            if reste <= 0:
                self.passer("enregistrer")
            else:
                textes.append((str(int(reste) + 1), (X_DROITE + PANNEAU_L // 2, Y_PANNEAUX + 120), 160, ORANGE, "centre"))
            self.pied(textes, "Prépare-toi, mains baissées…", "Espace annuler")
        elif self.etat == "enregistrer":
            cv2.circle(canvas, (X_DROITE + PANNEAU_L - 30, Y_PANNEAUX + 25), 12, ROUGE, -1)
            textes.append(("Signe !", (X_DROITE + PANNEAU_L - 140, Y_PANNEAUX + 10), 26, TEXTE))
            self.pied(textes, "Fais le signe, puis baisse les mains pour terminer.", "Espace terminer")

    def dessiner_resultat(self, canvas, textes, t):
        if self.images_essai:
            i = int(t * 25) % len(self.images_essai)
            coller(canvas, cv2.flip(self.images_essai[i], 1), X_DROITE, Y_PANNEAUX)
        textes.append(("Ton essai", (X_DROITE + 10, Y_PANNEAUX + 8), 20, TEXTE))
        score = self.resultat["score"]
        y = Y_PANNEAUX + PANNEAU_H + 12
        textes.append((f"{score}/100", (X_GAUCHE + 10, y), 48, couleur_score(score)))
        if score >= 85:
            appreciation = "Excellent !"
        elif score >= 70:
            appreciation = "Bien joué !"
        elif score >= 45:
            appreciation = "Presque, continue !"
        else:
            appreciation = "Pas encore, réessaie."
        textes.append((appreciation, (X_GAUCHE + 10, y + 58), 22, TEXTE))
        conseils = self.resultat["conseils"] if score < 90 else []
        for n, conseil in enumerate(conseils[:3]):
            textes.append(("• " + conseil, (X_GAUCHE + 230, y + n * 30), 21, TEXTE))
        if self.resultat.get("miroir"):
            textes.append(("(comparé en miroir : tu as signé avec l'autre main, c'est accepté)",
                           (X_GAUCHE + 230, y + 92), 16, DISCRET))
        textes.append(("Espace réessayer   ·   V revoir le modèle   ·   N mot suivant   ·   M menu",
                       (LARGEUR // 2, HAUTEUR - 58), 20, DISCRET, "centre"))
        textes.append(("Vidéo : " + self.refs[self.variante].credit, (LARGEUR // 2, HAUTEUR - 28), 15, DISCRET, "centre"))

    def pied(self, textes, ligne1, ligne2=None):
        y = Y_PANNEAUX + PANNEAU_H + 18
        textes.append((ligne1, (LARGEUR // 2, y), 24, TEXTE, "centre"))
        if ligne2:
            textes.append((ligne2, (LARGEUR // 2, y + 40), 20, DISCRET, "centre"))
        if self.etat != "menu" and self.refs and not (self.defi and not self.aide_vue):
            textes.append(("Vidéo : " + self.refs[self.variante].credit, (LARGEUR // 2, HAUTEUR - 28), 15, DISCRET, "centre"))


def couleur_score(score):
    return VERT if score >= 70 else ORANGE if score >= 45 else ROUGE


def main():
    if not references.charger_mots():
        print("Aucun signe téléchargé : récupération des mots de départ (une seule fois, ~2 min)…")
        references.telecharger(references.MOTS_DE_DEPART)
    camera = cv2.VideoCapture(int(os.environ.get("PYTALK_CAMERA", "0")))
    if not camera.isOpened():
        print("Impossible d'ouvrir la caméra. Essaie PYTALK_CAMERA=1 (ou 2…).")
        sys.exit(1)
    try:
        Appli(camera).lancer()
    finally:
        camera.release()


if __name__ == "__main__":
    main()
