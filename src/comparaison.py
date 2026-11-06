# PyTalk — comparaison d'un geste avec un signe de référence
#
# Principe :
#   1. Pour chaque image, on garde les épaules et les 21 points de chaque main
#      détectés par MediaPipe (fonction `points_image`).
#   2. On normalise : la position des mains est exprimée par rapport au centre
#      des épaules et en « largeurs d'épaules », la forme de la main par rapport
#      au poignet. Ainsi la taille de la personne et sa distance à la caméra
#      n'ont pas d'importance.
#   3. On aligne les deux mouvements avec la déformation temporelle dynamique
#      (DTW) : un geste plus lent ou plus rapide peut quand même correspondre.
#   4. Le coût moyen de l'alignement donne un score de 0 à 100, et les écarts
#      mesurés le long de l'alignement donnent des conseils.

import numpy as np

# Indices dans le tableau renvoyé par `points_image`
EPAULES = slice(0, 2)
MAIN_GAUCHE = slice(2, 23)   # main gauche de la personne filmée
MAIN_DROITE = slice(23, 44)  # main droite de la personne filmée
NB_POINTS = 44

# Réglages calibrés en comparant les vidéos de référence entre elles
# (même mot, deux personnes : 0,3 à 0,6 ; deux mots différents : souvent > 0,7,
# mais des signes proches comme « bonjour » et « merci » restent sous 0,4)
POIDS_POSITION = 1.0
POIDS_FORME = 0.35
PENALITE_MAIN_ABSENTE = 1.0
# Coût moyen → score : COUT_PARFAIT donne 100, COUT_NUL donne 0
COUT_PARFAIT = 0.12
COUT_NUL = 0.75
# Une main plus basse que ça (en largeurs d'épaules sous les épaules) est « au repos »
SEUIL_REPOS = 1.1


def points_image(results, largeur, hauteur):
    """Extrait épaules + mains d'un résultat MediaPipe Holistic.

    Renvoie un tableau (44, 2) en « unités de hauteur d'image » (x est multiplié
    par largeur/hauteur pour ne pas déformer les distances). NaN si absent.
    """
    pts = np.full((NB_POINTS, 2), np.nan, dtype=np.float32)
    ratio = largeur / hauteur
    if results.pose_landmarks:
        lm = results.pose_landmarks.landmark
        pts[0] = (lm[11].x * ratio, lm[11].y)  # épaule gauche
        pts[1] = (lm[12].x * ratio, lm[12].y)  # épaule droite
    for main, zone in ((results.left_hand_landmarks, MAIN_GAUCHE),
                       (results.right_hand_landmarks, MAIN_DROITE)):
        if main:
            pts[zone] = [(p.x * ratio, p.y) for p in main.landmark]
    return pts


def _repere_corps(seq):
    """Centre et échelle (largeur d'épaules) par image, complétés par la médiane."""
    epaules = seq[:, EPAULES]
    centre = epaules.mean(axis=1)
    echelle = np.linalg.norm(epaules[:, 0] - epaules[:, 1], axis=1)
    ok = ~np.isnan(echelle) & (echelle > 1e-3)
    if ok.any():
        centre[~ok] = np.median(centre[ok], axis=0)
        echelle[~ok] = np.median(echelle[ok])
    else:  # pas d'épaules du tout : repère de l'image
        centre[:] = (0.5, 0.5)
        echelle[:] = 0.3
    return centre, echelle


def normaliser(seq):
    """Transforme une séquence brute (T, 44, 2) en descripteurs par main.

    Renvoie un dict {"G": (présence, position, forme), "D": ...} avec
    présence (T,), position (T, 2) et forme (T, 21, 2).
    """
    seq = np.asarray(seq, dtype=np.float32)
    centre, echelle = _repere_corps(seq)
    mains = {}
    for nom, zone in (("G", MAIN_GAUCHE), ("D", MAIN_DROITE)):
        main = seq[:, zone]
        presence = ~np.isnan(main[:, 0, 0])
        position = (main.mean(axis=1) - centre) / echelle[:, None]
        poignet = main[:, :1]
        taille = np.linalg.norm(main[:, 9] - main[:, 0], axis=1)[:, None, None]
        forme = (main - poignet) / np.where(taille > 1e-4, taille, 1.0)
        # une main posée en bas (au repos) ne participe pas au signe
        presence &= np.nan_to_num(position[:, 1], nan=9) < SEUIL_REPOS
        mains[nom] = (presence, np.nan_to_num(position), np.nan_to_num(forme))
    return mains


def mains_actives(pts):
    """Vrai si au moins une main est levée (pas au repos) sur cette image."""
    mains = normaliser(np.asarray(pts, dtype=np.float32)[None])
    return bool(mains["G"][0][0] or mains["D"][0][0])


def miroir(mains):
    """Échange les mains et inverse la gauche/droite (pour les gauchers)."""
    def retourne(desc):
        presence, position, forme = desc
        position = position * np.array([-1, 1], dtype=np.float32)
        forme = forme * np.array([-1, 1], dtype=np.float32)
        return presence, position, forme
    return {"G": retourne(mains["D"]), "D": retourne(mains["G"])}


def rogner(seq):
    """Garde la partie où les mains sont levées (enlève les temps morts)."""
    seq = np.asarray(seq, dtype=np.float32)
    if len(seq) == 0:
        return seq
    mains = normaliser(seq)
    actif = np.zeros(len(seq), dtype=bool)
    for presence, _, _ in mains.values():
        actif |= presence
    if not actif.any():
        return seq[:0]
    idx = np.flatnonzero(actif)
    return seq[idx[0]:idx[-1] + 1]


def _matrice_couts(a, b):
    total = np.zeros((len(a["G"][0]), len(b["G"][0])), dtype=np.float32)
    actives = np.zeros_like(total)
    for nom in ("G", "D"):
        pa, posa, fa = a[nom]
        pb, posb, fb = b[nom]
        deux = pa[:, None] & pb[None, :]
        une = pa[:, None] ^ pb[None, :]
        d_pos = np.linalg.norm(posa[:, None] - posb[None, :], axis=-1)
        d_forme = np.linalg.norm(fa[:, None] - fb[None, :], axis=-1).mean(axis=-1)
        cout = np.where(deux, POIDS_POSITION * d_pos + POIDS_FORME * d_forme,
                        np.where(une, PENALITE_MAIN_ABSENTE, 0.0))
        total += cout
        actives += deux | une
    return total / np.maximum(actives, 1)


def _dtw(couts):
    n, m = couts.shape
    acc = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    acc[0, 0] = 0.0
    for i in range(1, n + 1):
        ligne = couts[i - 1]
        for j in range(1, m + 1):
            acc[i, j] = ligne[j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    # Remonte le chemin optimal
    chemin, i, j = [], n, m
    while i > 0 and j > 0:
        chemin.append((i - 1, j - 1))
        pas = np.argmin((acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1]))
        if pas == 0:
            i, j = i - 1, j - 1
        elif pas == 1:
            i -= 1
        else:
            j -= 1
    chemin.reverse()
    return acc[n, m] / len(chemin), chemin


def _conseils(ref, essai, chemin, miroir_utilise, duree_ref, duree_essai):
    """Liste de (gravité, message) triée, à partir des écarts sur le chemin."""
    conseils = []
    i_ref = np.array([c[0] for c in chemin])
    i_ess = np.array([c[1] for c in chemin])
    noms = {"D": "droite", "G": "gauche"}
    if miroir_utilise:  # la main « droite » comparée est en fait ta main gauche
        noms = {"D": "gauche", "G": "droite"}
    for cote in ("D", "G"):
        pr, posr, fr = ref[cote]
        pe, pose, fe = essai[cote]
        pr, pe = pr[i_ref], pe[i_ess]
        main = f"main {noms[cote]}"
        if pr.mean() > 0.3 and (pr & ~pe).mean() > 0.4:
            conseils.append((3.0, f"Utilise aussi ta {main} (elle n'est pas assez visible)."))
            continue
        if pe.mean() > 0.3 and (pe & ~pr).mean() > 0.5 and pr.mean() < 0.2:
            conseils.append((2.5, f"Ce signe se fait sans ta {main}."))
            continue
        deux = pr & pe
        if deux.sum() < 3:
            continue
        ecart = (pose[i_ess][deux] - posr[i_ref][deux]).mean(axis=0)
        exterieur = -1 if cote == "D" else 1  # côté extérieur du corps en x
        if ecart[1] > 0.35:
            conseils.append((ecart[1], f"Monte ta {main} (elle est trop basse)."))
        elif ecart[1] < -0.35:
            conseils.append((-ecart[1], f"Descends ta {main} (elle est trop haute)."))
        if ecart[0] * exterieur > 0.35:
            conseils.append((abs(ecart[0]), f"Rapproche ta {main} du centre du corps."))
        elif ecart[0] * exterieur < -0.35:
            conseils.append((abs(ecart[0]), f"Écarte davantage ta {main} du corps."))
        d_forme = np.linalg.norm(fe[i_ess][deux] - fr[i_ref][deux], axis=-1).mean()
        if d_forme > 0.45:
            conseils.append((d_forme, f"Regarde la forme de ta {main} : position des doigts différente."))
    if duree_ref > 0:
        rapport = duree_essai / duree_ref
        if rapport > 1.9:
            conseils.append((0.5 * rapport, "Fais le geste plus vite, plus fluide."))
        elif rapport < 0.45:
            conseils.append((0.5 / rapport, "Prends ton temps : le geste est trop rapide."))
    conseils.sort(reverse=True)
    return [m for _, m in conseils]


def comparer(reference, essai):
    """Compare deux séquences brutes (T, 44, 2).

    Renvoie un dict : score (0-100), conseils (liste de phrases), miroir (bool).
    Le geste est aussi comparé en miroir, pour les gauchers : on garde le meilleur.
    """
    ref_seq, ess_seq = rogner(reference), rogner(essai)
    if len(ess_seq) < 3:
        return {"score": 0, "conseils": ["Je n'ai pas vu tes mains : place-toi bien face à la caméra."], "miroir": False}
    if len(ref_seq) < 3:
        return {"score": 0, "conseils": ["La vidéo de référence est inutilisable."], "miroir": False}
    ref = normaliser(ref_seq)
    ess = normaliser(ess_seq)
    meilleur = None
    for est_miroir, version in ((False, ess), (True, miroir(ess))):
        cout, chemin = _dtw(_matrice_couts(ref, version))
        if meilleur is None or cout < meilleur[0]:
            meilleur = (cout, chemin, est_miroir, version)
    cout, chemin, est_miroir, version = meilleur
    score = int(round(100 * np.clip((COUT_NUL - cout) / (COUT_NUL - COUT_PARFAIT), 0, 1)))
    conseils = _conseils(ref, version, chemin, est_miroir, len(ref_seq), len(ess_seq))
    return {"score": score, "conseils": conseils[:3], "miroir": est_miroir, "cout": float(cout)}
