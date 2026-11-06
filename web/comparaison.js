// PyTalk web — comparaison d'un geste avec un signe de référence.
// Traduction de src/comparaison.py (mêmes réglages, même principe) :
// normalisation par rapport aux épaules, alignement DTW, score et conseils.
//
// Une séquence est un tableau d'images ; chaque image est un tableau de
// 44 points [x, y] ou null : 0-1 épaules (gauche, droite de la personne),
// 2-22 main gauche, 23-43 main droite. x est en « unités de hauteur ».

// Calibré sur les vidéos de référence analysées avec les modèles web :
// même mot signé par deux personnes ≈ 0,33 à 0,89 (médiane 0,61),
// mots différents ≈ 0,45 à 1,03 (médiane 0,79). Imiter la vidéo affichée
// donne des coûts plus bas qu'entre deux signeurs différents.
export const REGLAGES = {
  poidsPosition: 1.0,
  poidsForme: 0.35,
  penaliteMainAbsente: 1.0,
  coutParfait: 0.15, // coût → 100
  coutNul: 0.85,     // coût → 0
  seuilRepos: 1.1,   // main plus basse que ça (en largeurs d'épaules) = au repos
};

const MAINS = { G: 2, D: 23 };

/** Points d'une image à partir des résultats MediaPipe Tasks (web). */
export function pointsImage(resMains, resCorps, largeur, hauteur) {
  const ratio = largeur / hauteur;
  const pts = new Array(44).fill(null);
  const pose = resCorps?.landmarks?.[0];
  if (pose) {
    pts[0] = [pose[11].x * ratio, pose[11].y];
    pts[1] = [pose[12].x * ratio, pose[12].y];
  }
  const mains = resMains?.landmarks ?? [];
  const cotes = resMains?.handednesses ?? resMains?.handedness ?? [];
  mains.forEach((main, i) => {
    // vérifié : sur une image non retournée, « Left » = main gauche de la personne
    const debut = cotes[i]?.[0]?.categoryName === "Left" ? 2 : 23;
    if (pts[debut]) return;
    main.forEach((p, j) => { pts[debut + j] = [p.x * ratio, p.y]; });
  });
  return pts;
}

function median(valeurs) {
  if (!valeurs.length) return NaN;
  const v = [...valeurs].sort((a, b) => a - b);
  const m = v.length >> 1;
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

function repereCorps(seq) {
  const centres = [], echelles = [];
  for (const img of seq) {
    const [g, d] = img;
    if (g && d) {
      const e = Math.hypot(g[0] - d[0], g[1] - d[1]);
      if (e > 1e-3) {
        centres.push([(g[0] + d[0]) / 2, (g[1] + d[1]) / 2]);
        echelles.push(e);
        continue;
      }
    }
    centres.push(null);
    echelles.push(null);
  }
  const ok = echelles.filter((e) => e !== null);
  const defaut = ok.length
    ? { c: [median(centres.filter(Boolean).map((c) => c[0])), median(centres.filter(Boolean).map((c) => c[1]))], e: median(ok) }
    : { c: [0.5, 0.5], e: 0.3 };
  return seq.map((_, i) => ({
    c: centres[i] ?? defaut.c,
    e: echelles[i] ?? defaut.e,
  }));
}

/** Descripteurs par main : présence, position (vs épaules) et forme (vs poignet). */
export function normaliser(seq) {
  const repere = repereCorps(seq);
  const mains = {};
  for (const [nom, debut] of Object.entries(MAINS)) {
    const presence = new Uint8Array(seq.length);
    const position = new Float32Array(seq.length * 2);
    const forme = new Float32Array(seq.length * 42);
    seq.forEach((img, t) => {
      const poignet = img[debut];
      if (!poignet) return;
      const { c, e } = repere[t];
      let sx = 0, sy = 0;
      for (let j = 0; j < 21; j++) { sx += img[debut + j][0]; sy += img[debut + j][1]; }
      const px = (sx / 21 - c[0]) / e, py = (sy / 21 - c[1]) / e;
      if (py >= REGLAGES.seuilRepos) return; // main au repos : ignorée
      presence[t] = 1;
      position[t * 2] = px;
      position[t * 2 + 1] = py;
      const m9 = img[debut + 9];
      const taille = Math.hypot(m9[0] - poignet[0], m9[1] - poignet[1]) || 1;
      for (let j = 0; j < 21; j++) {
        forme[t * 42 + j * 2] = (img[debut + j][0] - poignet[0]) / taille;
        forme[t * 42 + j * 2 + 1] = (img[debut + j][1] - poignet[1]) / taille;
      }
    });
    mains[nom] = { presence, position, forme, n: seq.length };
  }
  return mains;
}

/** Une main est-elle levée sur cette image ? (pour arrêter l'enregistrement) */
export function mainsActives(pts) {
  const m = normaliser([pts]);
  return Boolean(m.G.presence[0] || m.D.presence[0]);
}

function miroir(mains) {
  const retourne = ({ presence, position, forme, n }) => {
    const p = position.slice(), f = forme.slice();
    for (let i = 0; i < p.length; i += 2) p[i] = -p[i];
    for (let i = 0; i < f.length; i += 2) f[i] = -f[i];
    return { presence, position: p, forme: f, n };
  };
  return { G: retourne(mains.D), D: retourne(mains.G) };
}

/** Enlève les temps morts : garde de la 1re à la dernière image avec une main levée. */
export function rogner(seq) {
  const m = normaliser(seq);
  let debut = -1, fin = -1;
  for (let t = 0; t < seq.length; t++) {
    if (m.G.presence[t] || m.D.presence[t]) {
      if (debut < 0) debut = t;
      fin = t;
    }
  }
  return debut < 0 ? [] : seq.slice(debut, fin + 1);
}

function matriceCouts(a, b) {
  const n = a.G.n, m = b.G.n;
  const total = new Float32Array(n * m), actives = new Uint8Array(n * m);
  const R = REGLAGES;
  for (const nom of ["G", "D"]) {
    const A = a[nom], B = b[nom];
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < m; j++) {
        const pa = A.presence[i], pb = B.presence[j];
        if (!pa && !pb) continue;
        const k = i * m + j;
        actives[k]++;
        if (pa !== pb) { total[k] += R.penaliteMainAbsente; continue; }
        const dPos = Math.hypot(A.position[i * 2] - B.position[j * 2], A.position[i * 2 + 1] - B.position[j * 2 + 1]);
        let dForme = 0;
        for (let p = 0; p < 21; p++) {
          const ia = i * 42 + p * 2, ib = j * 42 + p * 2;
          dForme += Math.hypot(A.forme[ia] - B.forme[ib], A.forme[ia + 1] - B.forme[ib + 1]);
        }
        total[k] += R.poidsPosition * dPos + R.poidsForme * (dForme / 21);
      }
    }
  }
  for (let k = 0; k < total.length; k++) total[k] /= Math.max(actives[k], 1);
  return { couts: total, n, m };
}

function dtw({ couts, n, m }) {
  const W = m + 1;
  const acc = new Float64Array((n + 1) * W).fill(Infinity);
  acc[0] = 0;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      acc[i * W + j] = couts[(i - 1) * m + (j - 1)] +
        Math.min(acc[(i - 1) * W + j], acc[i * W + j - 1], acc[(i - 1) * W + j - 1]);
    }
  }
  const chemin = [];
  let i = n, j = m;
  while (i > 0 && j > 0) {
    chemin.push([i - 1, j - 1]);
    const diag = acc[(i - 1) * W + j - 1], haut = acc[(i - 1) * W + j], gauche = acc[i * W + j - 1];
    if (diag <= haut && diag <= gauche) { i--; j--; }
    else if (haut <= gauche) i--;
    else j--;
  }
  chemin.reverse();
  return { cout: acc[n * W + m] / chemin.length, chemin };
}

function conseils(ref, essai, chemin, estMiroir, dureeRef, dureeEssai) {
  const liste = [];
  const noms = estMiroir ? { D: "gauche", G: "droite" } : { D: "droite", G: "gauche" };
  for (const cote of ["D", "G"]) {
    const R = ref[cote], E = essai[cote];
    const main = `main ${noms[cote]}`;
    let nr = 0, ne = 0, refSeul = 0, essaiSeul = 0, deux = 0;
    let dx = 0, dy = 0, dForme = 0;
    for (const [i, j] of chemin) {
      const pr = R.presence[i], pe = E.presence[j];
      nr += pr; ne += pe;
      if (pr && !pe) refSeul++;
      if (pe && !pr) essaiSeul++;
      if (pr && pe) {
        deux++;
        dx += E.position[j * 2] - R.position[i * 2];
        dy += E.position[j * 2 + 1] - R.position[i * 2 + 1];
        let f = 0;
        for (let p = 0; p < 21; p++) {
          f += Math.hypot(E.forme[j * 42 + p * 2] - R.forme[i * 42 + p * 2],
                          E.forme[j * 42 + p * 2 + 1] - R.forme[i * 42 + p * 2 + 1]);
        }
        dForme += f / 21;
      }
    }
    const L = chemin.length;
    if (nr / L > 0.3 && refSeul / L > 0.4) {
      liste.push([3, `Utilise aussi ta ${main} (elle n'est pas assez visible).`]);
      continue;
    }
    if (ne / L > 0.3 && essaiSeul / L > 0.5 && nr / L < 0.2) {
      liste.push([2.5, `Ce signe se fait sans ta ${main}.`]);
      continue;
    }
    if (deux < 3) continue;
    dx /= deux; dy /= deux; dForme /= deux;
    const exterieur = cote === "D" ? -1 : 1;
    if (dy > 0.35) liste.push([dy, `Monte ta ${main} (elle est trop basse).`]);
    else if (dy < -0.35) liste.push([-dy, `Descends ta ${main} (elle est trop haute).`]);
    if (dx * exterieur > 0.35) liste.push([Math.abs(dx), `Rapproche ta ${main} du centre du corps.`]);
    else if (dx * exterieur < -0.35) liste.push([Math.abs(dx), `Écarte davantage ta ${main} du corps.`]);
    if (dForme > 0.45) liste.push([dForme, `Regarde la forme de ta ${main} : position des doigts différente.`]);
  }
  if (dureeRef > 0) {
    const rapport = dureeEssai / dureeRef;
    if (rapport > 1.9) liste.push([0.5 * rapport, "Fais le geste plus vite, plus fluide."]);
    else if (rapport < 0.45) liste.push([0.5 / rapport, "Prends ton temps : le geste est trop rapide."]);
  }
  return liste.sort((a, b) => b[0] - a[0]).map(([, m]) => m);
}

/**
 * Compare deux séquences (référence, essai) ; les durées sont en secondes.
 * Renvoie { score 0-100, conseils[], miroir, cout }.
 */
export function comparer(reference, essai, fpsRef = 25, fpsEssai = 25) {
  const refSeq = rogner(reference), essSeq = rogner(essai);
  if (essSeq.length < 3) {
    return { score: 0, conseils: ["Je n'ai pas vu tes mains : place-toi bien face à la caméra, mains et épaules visibles."], miroir: false, cout: Infinity };
  }
  if (refSeq.length < 3) {
    return { score: 0, conseils: ["La vidéo de référence est inutilisable."], miroir: false, cout: Infinity };
  }
  const ref = normaliser(refSeq), ess = normaliser(essSeq);
  let meilleur = null;
  for (const [estMiroir, version] of [[false, ess], [true, miroir(ess)]]) {
    const r = dtw(matriceCouts(ref, version));
    if (!meilleur || r.cout < meilleur.cout) meilleur = { ...r, estMiroir, version };
  }
  const R = REGLAGES;
  const score = Math.round(100 * Math.min(1, Math.max(0, (R.coutNul - meilleur.cout) / (R.coutNul - R.coutParfait))));
  return {
    score,
    conseils: conseils(ref, meilleur.version, meilleur.chemin, meilleur.estMiroir,
      refSeq.length / fpsRef, essSeq.length / fpsEssai).slice(0, 3),
    miroir: meilleur.estMiroir,
    cout: meilleur.cout,
  };
}
