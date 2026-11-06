# PyTalk — téléchargement des signes de référence (LSF) depuis Wikimedia Commons
#
# Les vidéos viennent de la catégorie « Videos of French Sign Language » :
# dictionnaire Elix (CC BY-SA 4.0), vidéos de Laura Jauvert (CC BY 3.0),
# enregistrements Lingua Libre (CC0). Licence et auteur sont relevés pour
# chaque vidéo et affichés dans le mode Apprendre.
#
# Utilisation (depuis le dossier src) :
#   python references.py                 télécharge la liste de mots de départ
#   python references.py merci maison    télécharge ces mots
#   python references.py --liste [filtre]  affiche les mots disponibles
#   python references.py --tout          télécharge tout (~800 vidéos, long)
#   python references.py --credits       auteurs et licences des vidéos téléchargées
#   python references.py --rafraichir    remet à jour le catalogue

import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refs")
CATALOGUE = os.path.join(DOSSIER, "catalogue.json")
MOTS = os.path.join(DOSSIER, "mots.json")
API = "https://commons.wikimedia.org/w/api.php"
AGENT = "PyTalk/0.2 (LSF learning app; https://github.com/AnthonyMarcelin/pytalk-LSF)"
CATEGORIES = [
    "Category:Videos of French Sign Language",
    "Category:Files supported by Wikimedia France - Elix",
    "Category:Vidéos LSF par Laura Jauvert",
    "Category:Lingua Libre pronunciation-fsl",
]
MOTS_DE_DEPART = [
    "bonjour", "bonsoir", "au revoir", "merci", "oui", "non", "de rien", "pardon",
    "ca va", "bien", "comprendre", "apprendre", "sourd", "famille", "manger",
    "boire", "eau", "maison", "aider", "pourquoi",
]
VARIANTES_MAX = 3
ORDRE_SOURCES = {"Elix": 0, "Laura Jauvert": 1, "Lingua Libre": 2, "Commons": 3}
EXTENSIONS = (".webm", ".ogv", ".mp4")


def cle(mot):
    """« Écôle (lieu) » → « ecole » : clé sans accents ni précision."""
    mot = re.sub(r"\(.*?\)", "", mot)
    mot = unicodedata.normalize("NFD", mot).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9' ]", "", mot.lower()).strip()


def _requete(params, essais=5):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "maxlag": 5})
    for essai in range(essais):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(req, timeout=30) as rep:
                donnees = json.load(rep)
            if "error" in donnees and donnees["error"].get("code") == "maxlag":
                raise ValueError("serveur occupé")
            return donnees
        except (ValueError, OSError):
            time.sleep(2 ** essai)
    raise RuntimeError("Wikimedia Commons ne répond pas, réessaie plus tard.")


def _texte(html):
    texte = re.sub(r"<br\s*/?>", ", ", html or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", texte)).strip(" ,")


def analyser_titre(titre, categorie):
    """Déduit (mot affiché, source) du nom de fichier Commons."""
    nom = titre[len("File:"):].rsplit(".", 1)[0]
    m = re.match(r"LL-Q33302 \(fsl\)-([^-]+)-(.+)$", nom)
    if m:
        return m.group(2), "Laura Jauvert" if "Jauvert" in m.group(1) else "Lingua Libre"
    m = re.match(r"(.+?) [A-Z][A-Za-z]*(?: \d+)+ - Elix$", nom)
    if m:
        return m.group(1), "Elix"
    m = re.match(r"LSF Vocab (.+)$", nom)
    if m:
        return m.group(1), "Commons"
    if nom.startswith("LSF Exo"):
        return None, None
    if "Jauvert" in categorie:
        return nom, "Laura Jauvert"
    return nom, "Commons"


def construire_catalogue():
    """Liste toutes les vidéos LSF de Commons (mis en cache dans refs/catalogue.json)."""
    fichiers = {}
    for categorie in CATEGORIES:
        params = {
            "action": "query", "generator": "categorymembers", "gcmtitle": categorie,
            "gcmtype": "file", "gcmlimit": 50, "prop": "imageinfo",
            "iiprop": "url|extmetadata|size",
            "iiextmetadatafilter": "LicenseShortName|Artist",
        }
        while True:
            donnees = _requete(params)
            for page in donnees.get("query", {}).get("pages", {}).values():
                titre = page["title"]
                if not titre.lower().endswith(EXTENSIONS) or titre in fichiers:
                    continue
                mot, source = analyser_titre(titre, categorie)
                if not mot:
                    continue
                info = page["imageinfo"][0]
                meta = info.get("extmetadata", {})
                fichiers[titre] = {
                    "titre": titre, "mot": mot, "cle": cle(mot), "source": source,
                    "url": info["url"], "taille": info.get("size", 0),
                    "licence": _texte(meta.get("LicenseShortName", {}).get("value")),
                    "auteur": _texte(meta.get("Artist", {}).get("value")) or "auteur : voir la page Commons",
                    "page": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"), safe=":()'"),
                }
            if "continue" not in donnees:
                break
            params.update(donnees["continue"])
            time.sleep(0.5)
    os.makedirs(DOSSIER, exist_ok=True)
    with open(CATALOGUE, "w", encoding="utf-8") as f:
        json.dump(list(fichiers.values()), f, ensure_ascii=False, indent=1)
    return list(fichiers.values())


def catalogue(rafraichir=False):
    if not rafraichir and os.path.exists(CATALOGUE):
        with open(CATALOGUE, encoding="utf-8") as f:
            return json.load(f)
    print("Récupération du catalogue des vidéos LSF sur Wikimedia Commons…")
    return construire_catalogue()


def par_mot(entrees):
    groupes = {}
    for e in entrees:
        groupes.setdefault(e["cle"], []).append(e)
    for liste in groupes.values():
        liste.sort(key=lambda e: (ORDRE_SOURCES.get(e["source"], 9), e["titre"]))
    return groupes


def nom_affiche(variantes):
    """Préfère un nom accentué (Elix n'a pas d'accents dans ses noms de fichiers)."""
    noms = [re.sub(r"\s*\(.*?\)", "", v["mot"]).strip() for v in variantes]
    accentues = [n for n in noms if n != cle(n)]
    return (accentues or noms)[0].capitalize()


def charger_mots():
    if os.path.exists(MOTS):
        with open(MOTS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _sauver_mots(mots):
    with open(MOTS, "w", encoding="utf-8") as f:
        json.dump(mots, f, ensure_ascii=False, indent=1)


def extraire_points(chemin_video):
    """Passe la vidéo dans MediaPipe et renvoie la séquence brute (T, 44, 2)."""
    import cv2
    import numpy as np
    import mediapipe as mp
    from comparaison import points_image

    video = cv2.VideoCapture(chemin_video)
    fps = video.get(cv2.CAP_PROP_FPS) or 25
    seq = []
    with mp.solutions.holistic.Holistic(min_detection_confidence=0.5, min_tracking_confidence=0.5) as holistic:
        while True:
            ok, image = video.read()
            if not ok:
                break
            h, w = image.shape[:2]
            resultats = holistic.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            seq.append(points_image(resultats, w, h))
    video.release()
    return np.array(seq, dtype=np.float32), fps


def nom_fichier(titre):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", titre[len("File:"):])


def telecharger(cles_voulues, entrees=None):
    """Télécharge les vidéos des mots demandés et calcule leurs points."""
    import numpy as np

    entrees = entrees if entrees is not None else catalogue()
    groupes = par_mot(entrees)
    mots = charger_mots()
    os.makedirs(os.path.join(DOSSIER, "videos"), exist_ok=True)
    os.makedirs(os.path.join(DOSSIER, "points"), exist_ok=True)
    introuvables = []
    for k in cles_voulues:
        k = cle(k)
        if k not in groupes:
            introuvables.append(k)
            continue
        variantes = groupes[k][:VARIANTES_MAX]
        nom = nom_affiche(groupes[k])
        print(f"  {nom} ({len(variantes)} vidéo{'s' if len(variantes) > 1 else ''})")
        fiche = {"nom": nom, "variantes": []}
        for v in variantes:
            fichier = nom_fichier(v["titre"])
            video = os.path.join(DOSSIER, "videos", fichier)
            points = os.path.join(DOSSIER, "points", fichier.rsplit(".", 1)[0] + ".npz")
            if not os.path.exists(video):
                req = urllib.request.Request(v["url"], headers={"User-Agent": AGENT})
                with urllib.request.urlopen(req, timeout=60) as rep, open(video, "wb") as f:
                    f.write(rep.read())
                time.sleep(0.3)
            if not os.path.exists(points):
                seq, fps = extraire_points(video)
                np.savez_compressed(points, points=seq, fps=fps)
            fiche["variantes"].append({
                "video": os.path.relpath(video, DOSSIER), "points": os.path.relpath(points, DOSSIER),
                "source": v["source"], "auteur": v["auteur"], "licence": v["licence"], "page": v["page"],
            })
        mots[k] = fiche
        _sauver_mots(mots)
    if introuvables:
        print("Introuvables :", ", ".join(introuvables), "(essaie : python references.py --liste <début du mot>)")
    return mots


def main(args):
    if args and args[0] == "--liste":
        groupes = par_mot(catalogue(rafraichir="--rafraichir" in args))
        filtre = cle(args[1]) if len(args) > 1 and not args[1].startswith("--") else ""
        noms = sorted(nom_affiche(v) for k, v in groupes.items() if k.startswith(filtre))
        print(f"{len(noms)} mots disponibles :")
        print(", ".join(noms))
        return
    if args and args[0] == "--credits":
        for fiche in charger_mots().values():
            for v in fiche["variantes"]:
                print(f"{fiche['nom']} : {v['auteur']} — {v['licence']} — {v['page']}")
        return
    if args and args[0] == "--rafraichir":
        catalogue(rafraichir=True)
        return
    entrees = catalogue()
    if args and args[0] == "--tout":
        voulus = sorted(par_mot(entrees))
    else:
        voulus = args or MOTS_DE_DEPART
    print(f"Téléchargement de {len(voulus)} mot(s) dans {DOSSIER} :")
    telecharger(voulus, entrees)
    print("Terminé. Lance : python apprendre.py")


if __name__ == "__main__":
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    main(sys.argv[1:])
