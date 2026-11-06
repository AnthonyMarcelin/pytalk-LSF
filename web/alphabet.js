// PyTalk web — reconnaissance des lettres de l'alphabet (dactylologie LSF).
//
// Une lettre est une forme de main tenue immobile. On décrit la main par :
//   - le pliage de chaque articulation des doigts (15 angles, en 3D) ;
//   - les distances entre le bout du pouce et les autres bouts de doigts (O, D, F…) ;
//   - l'écart entre les bouts de doigts voisins (U / V…) ;
//   - le croisement index / majeur (R) ;
//   - la direction de la main dans l'image (M / N horizontales, P / Q vers le bas).
// Tout sauf la direction est indépendant de la taille, de la distance à la
// caméra et de la main utilisée ; la direction est comparée aussi en miroir.
//
// Chaque lettre a plusieurs descripteurs de référence (la même photo détectée
// à plusieurs tailles) : on garde le plus proche. Calibré sur les 26 photos :
// deux lettres différentes sont à un coût d'au moins 0,36 (R / U), la plupart
// au-delà de 0,55 ; la même photo redétectée reste le plus souvent sous 0,25.

export const REGLAGES_LETTRES = {
  coutParfait: 0.15, // coût → 100
  coutNul: 0.85,     // coût → 0 (score 70 ≈ coût 0,36 ; la lettre la plus proche doit aussi être la bonne)
};

const DOIGTS = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16], [17, 18, 19, 20]];

const moins = (a, b) => a.map((v, i) => v - b[i]);
const scalaire = (a, b) => a.reduce((s, v, i) => s + v * b[i], 0);
const norme = (a) => Math.sqrt(scalaire(a, a));
const distance = (a, b) => norme(moins(a, b));

function angle(u, v) {
  const c = scalaire(u, v) / (norme(u) * norme(v) || 1);
  return Math.acos(Math.min(1, Math.max(-1, c)));
}

/**
 * Descripteur d'une main.
 * monde : 21 points [x, y, z] (worldLandmarks de MediaPipe, en mètres).
 * plan  : 21 points [x, y] dans l'image, x en « unités de hauteur ».
 */
export function decrireMain(monde, plan) {
  const pli = [];
  for (const doigt of DOIGTS) {
    const chaine = [0, ...doigt];
    for (let k = 1; k < 4; k++) {
      pli.push(angle(moins(monde[chaine[k]], monde[chaine[k - 1]]), moins(monde[chaine[k + 1]], monde[chaine[k]])) / Math.PI);
    }
  }
  const taille = distance(monde[9], monde[0]) || 1;
  const contacts = [8, 12, 16, 20].map((t) => Math.min(distance(monde[4], monde[t]) / taille, 1.5));
  const ecarts = [[8, 12], [12, 16], [16, 20]].map(([a, b]) => Math.min(distance(monde[a], monde[b]) / taille, 1.5));
  const axe = moins(monde[5], monde[9]);
  const croise = Math.min(2.5, Math.max(-1, scalaire(moins(monde[8], monde[12]), axe) / (scalaire(axe, axe) || 1)));
  const v = moins(plan[9], plan[0]);
  const n = norme(v) || 1;
  return { pli, contacts, ecarts, croise, direction: [v[0] / n, v[1] / n] };
}

const ecartMoyen = (a, b) => a.reduce((s, v, i) => s + Math.abs(v - b[i]), 0) / a.length;

function cout(a, b, miroir) {
  const db = miroir ? [-b.direction[0], b.direction[1]] : b.direction;
  return ecartMoyen(a.pli, b.pli) * 4
    + ecartMoyen(a.contacts, b.contacts) * 0.6
    + ecartMoyen(a.ecarts, b.ecarts) * 0.6
    + distance(a.direction, db) * 0.5
    + Math.abs(a.croise - b.croise) * 0.3;
}

/** Coût entre deux mains (le plus petit des deux sens : droitier / gaucher). */
export function coutMains(a, b) {
  return Math.min(cout(a, b, false), cout(a, b, true));
}

export function scoreDepuisCout(c) {
  const { coutParfait, coutNul } = REGLAGES_LETTRES;
  const s = 100 * (coutNul - c) / (coutNul - coutParfait);
  return Math.round(Math.min(100, Math.max(0, s)));
}

/** Descripteurs de référence d'une lettre de data/alphabet.json. */
export function decrireLettre(lettre) {
  return lettre.variantes.map((v) => decrireMain(v.monde, v.plan));
}

/**
 * Compare une main à toutes les lettres.
 * lettres : [{ cle, descripteurs }]. Renvoie les lettres triées par coût.
 */
export function classerLettres(main, lettres) {
  return lettres
    .map((l) => ({ cle: l.cle, cout: Math.min(...l.descripteurs.map((d) => coutMains(main, d))) }))
    .sort((a, b) => a.cout - b.cout);
}
