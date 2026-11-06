// PyTalk LSF — application web (PWA)
import { FilesetResolver, HandLandmarker, PoseLandmarker } from "./lib/mediapipe/vision_bundle.mjs";
import { comparer, mainsActives, pointsImage } from "./comparaison.js";
import { classerLettres, decrireLettre, decrireMain, scoreDepuisCout } from "./alphabet.js";

const $ = (id) => document.getElementById(id);
const CLE_PROGRES = "pytalk-lsf-progres";
const COMPTE_A_REBOURS = 3;
const FIN_SANS_MAINS = 600;   // ms sans main levée qui terminent l'essai
const DUREE_MIN = 1000;       // ms
const DUREE_MAX = 6000;       // ms (allongée pour les signes longs)
const SEUIL_REUSSI = 70;
const CLE_ONGLET = "pytalk-lsf-onglet";
const FENETRE_LETTRE = 400;   // ms de lissage du score en direct
const TENUE_LETTRE = 700;     // ms à tenir la lettre pour la valider

// Mode test : ?test=data/videos/xxx.mp4 remplace la caméra par une vidéo
const params = new URLSearchParams(location.search);
const VIDEO_TEST = params.get("test");

const etat = {
  mots: [],
  lettres: [],
  categorie: lireOnglet(),
  type: "mot",         // mot | lettre (exercice ouvert)
  suivi: [],           // lettre : [{ t, cout, plusProche }] des dernières images
  debutTenue: 0,
  meilleurTenue: 0,
  progres: chargerProgres(),
  detecteurs: null,
  mot: null,           // mot ouvert
  refs: [],            // [{ points, fps, duree, variante }]
  variante: 0,
  defi: false,
  aideVue: false,
  phase: "pret",       // pret | compte | enregistrer | direct (lettre) | resultat
  essai: [],
  debutEnregistrement: 0,
  derniereMain: 0,
  mainsVues: false,
  flux: null,
  boucleActive: false,
  derniereImage: -1,
  enregistreur: null,
  morceaux: [],
  verrouEcran: null,
  minuteurCompte: null,
};

// ---------------------------------------------------------------- progression

function chargerProgres() {
  try {
    return JSON.parse(localStorage.getItem(CLE_PROGRES)) || {};
  } catch {
    return {};
  }
}

function lireOnglet() {
  try {
    return localStorage.getItem(CLE_ONGLET) === "alphabet" ? "alphabet" : "mots";
  } catch {
    return "mots";
  }
}

function sauverProgres() {
  try {
    localStorage.setItem(CLE_PROGRES, JSON.stringify(etat.progres));
  } catch {
    /* stockage indisponible (navigation privée) : tant pis */
  }
}

function couleurScore(score) {
  return score >= SEUIL_REUSSI ? "vert" : score >= 45 ? "orange" : "rouge";
}

// ---------------------------------------------------------------- écrans

function afficher(id) {
  document.querySelectorAll(".ecran").forEach((e) => e.classList.toggle("actif", e.id === id));
  window.scrollTo(0, 0);
}

const elements = () => (etat.categorie === "alphabet" ? etat.lettres : etat.mots);

function rendreAccueil() {
  const alphabet = etat.categorie === "alphabet";
  for (const onglet of document.querySelectorAll(".onglet")) {
    onglet.setAttribute("aria-selected", String(onglet.dataset.categorie === etat.categorie));
  }
  $("lancer-defi").textContent = alphabet ? "🎲 Défi : une lettre au hasard" : "🎲 Défi : un signe au hasard";
  $("nom-progres").textContent = alphabet ? "lettres réussies" : "signes réussis";
  const liste = $("liste-mots");
  liste.classList.toggle("lettres", alphabet);
  liste.setAttribute("aria-label", alphabet ? "Lettres" : "Signes");
  liste.replaceChildren();
  for (const mot of elements()) {
    const fiche = etat.progres[mot.cleProgres];
    const li = document.createElement("li");
    const bouton = document.createElement("button");
    bouton.className = "carte-mot" + (fiche?.meilleur >= SEUIL_REUSSI ? " reussi" : "");
    const nom = document.createElement("span");
    nom.className = "nom";
    nom.textContent = mot.nom;
    const info = document.createElement("span");
    info.className = "etat";
    info.textContent = alphabet ? (!fiche ? "–" : fiche.meilleur >= SEUIL_REUSSI ? "✓" : fiche.meilleur)
      : !fiche ? "Pas encore essayé"
      : fiche.meilleur >= SEUIL_REUSSI ? `✓ Réussi · ${fiche.meilleur}`
      : `Meilleur : ${fiche.meilleur}`;
    bouton.append(nom, info);
    if (alphabet) bouton.setAttribute("aria-label", `Lettre ${mot.nom}${fiche ? `, meilleur score ${fiche.meilleur}` : ""}`);
    bouton.addEventListener("click", () => ouvrir(mot, false));
    li.append(bouton);
    liste.append(li);
  }
  const tous = elements();
  const reussis = tous.filter((m) => etat.progres[m.cleProgres]?.meilleur >= SEUIL_REUSSI).length;
  $("nb-reussis").textContent = reussis;
  $("nb-mots").textContent = tous.length;
  $("barre-progres").style.width = `${tous.length ? (100 * reussis) / tous.length : 0}%`;
}

function rendreCredits() {
  const liste = $("liste-credits");
  liste.replaceChildren();
  for (const mot of etat.mots) {
    for (const v of mot.variantes) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = v.page;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = mot.nom;
      li.append(a, ` — ${v.auteur} — ${v.licence}`);
      liste.append(li);
    }
  }
}

// ---------------------------------------------------------------- détection

async function initialiserDetection() {
  const fichiers = await FilesetResolver.forVisionTasks("lib/mediapipe/wasm");
  const creer = async (delegate) => Promise.all([
    HandLandmarker.createFromOptions(fichiers, {
      baseOptions: { modelAssetPath: "models/hand_landmarker.task", delegate },
      runningMode: "VIDEO", numHands: 2,
      minHandDetectionConfidence: 0.5, minTrackingConfidence: 0.5,
    }),
    PoseLandmarker.createFromOptions(fichiers, {
      baseOptions: { modelAssetPath: "models/pose_landmarker_lite.task", delegate },
      runningMode: "VIDEO", numPoses: 1,
    }),
  ]);
  let mains, corps;
  try {
    [mains, corps] = await creer("GPU");
  } catch (erreur) {
    console.warn("[PyTalk] GPU indisponible, passage au CPU", erreur);
    [mains, corps] = await creer("CPU");
  }
  etat.detecteurs = { mains, corps };
}

async function demarrerCamera() {
  const video = $("camera");
  if (VIDEO_TEST) {
    video.src = VIDEO_TEST;
    video.loop = true;
    await video.play();
    return true;
  }
  if (etat.flux) return true;
  try {
    etat.flux = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
    });
  } catch (erreur) {
    console.warn("[PyTalk] caméra refusée", erreur);
    messageCamera("La caméra n'est pas accessible.\nAutorise-la quand le téléphone le demande, ou dans Réglages › Safari › Caméra.");
    return false;
  }
  video.srcObject = etat.flux;
  await video.play().catch(() => {});
  return true;
}

function arreterCamera() {
  etat.boucleActive = false;
  etat.flux?.getTracks().forEach((t) => t.stop());
  etat.flux = null;
  const video = $("camera");
  video.pause();
  video.srcObject = null;
  if (VIDEO_TEST) video.removeAttribute("src");
}

function messageCamera(texte) {
  const m = $("message-camera");
  m.textContent = texte;
  m.hidden = !texte;
}

function boucle() {
  if (!etat.boucleActive) return;
  const video = $("camera");
  if (etat.detecteurs && video.readyState >= 2 && video.currentTime !== etat.derniereImage) {
    etat.derniereImage = video.currentTime;
    const maintenant = performance.now();
    const resMains = etat.detecteurs.mains.detectForVideo(video, maintenant);
    // Les lettres n'ont besoin que des mains : on économise la détection du corps.
    const resCorps = etat.type === "lettre" ? null : etat.detecteurs.corps.detectForVideo(video, maintenant);
    const pts = pointsImage(resMains, resCorps, video.videoWidth, video.videoHeight);
    if (etat.phase !== "resultat") dessinerCalque(pts, video.videoWidth, video.videoHeight);
    if (etat.phase === "enregistrer") enregistrer(pts, maintenant);
    if (etat.phase === "direct") suivreLettre(resMains, video.videoWidth / video.videoHeight, maintenant);
  }
  requestAnimationFrame(boucle);
}

const LIAISONS = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11],
  [11, 12], [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];

function dessinerCalque(pts, largeur, hauteur) {
  const calque = $("calque");
  if (calque.width !== largeur) calque.width = largeur;
  if (calque.height !== hauteur) calque.height = hauteur;
  const ctx = calque.getContext("2d");
  ctx.clearRect(0, 0, largeur, hauteur);
  // Les épaules servent de repère pour la comparaison : on les montre.
  if (pts[0] && pts[1]) {
    const [a, b] = [pts[0], pts[1]].map(([x, y]) => [x * hauteur, y * hauteur]);
    ctx.strokeStyle = ctx.fillStyle = "#50dc78";
    ctx.lineWidth = Math.max(3, largeur / 120);
    ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
    for (const [x, y] of [a, b]) {
      ctx.beginPath(); ctx.arc(x, y, Math.max(5, largeur / 70), 0, Math.PI * 2); ctx.fill();
    }
  }
  for (const [debut, couleur] of [[2, "#f542e6"], [23, "#4296f5"]]) {
    if (!pts[debut]) continue;
    const p = (i) => [pts[debut + i][0] * hauteur, pts[debut + i][1] * hauteur];
    ctx.strokeStyle = couleur;
    ctx.lineWidth = Math.max(2, largeur / 200);
    ctx.beginPath();
    for (const [a, b] of LIAISONS) {
      ctx.moveTo(...p(a));
      ctx.lineTo(...p(b));
    }
    ctx.stroke();
    ctx.fillStyle = "#fff";
    for (let i = 0; i < 21; i++) {
      const [x, y] = p(i);
      ctx.beginPath();
      ctx.arc(x, y, Math.max(2.5, largeur / 160), 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

// ---------------------------------------------------------------- exercice

async function chargerReferences(mot) {
  return Promise.all(mot.variantes.map(async (variante) => {
    const r = await fetch(variante.points);
    const donnees = await r.json();
    return { points: donnees.points, fps: donnees.fps || 25, duree: donnees.points.length / (donnees.fps || 25), variante };
  }));
}

function ouvrir(element, defi) {
  if (element.cleProgres.startsWith("lettre-")) ouvrirLettre(element, defi);
  else ouvrirMot(element, defi);
}

async function ouvrirMot(mot, defi) {
  etat.type = "mot";
  etat.mot = mot;
  etat.defi = defi;
  etat.aideVue = false;
  etat.variante = 0;
  etat.refs = [];
  $("titre-mot").textContent = defi ? `Défi : « ${mot.nom} »` : mot.nom;
  majBadge();
  afficher("exercice");
  if (history.state?.ecran !== "exercice") history.pushState({ ecran: "exercice" }, "");
  passer("pret");
  majModele();
  $("action").disabled = true;
  $("action").hidden = false;
  const [refs, cameraOk] = await Promise.all([chargerReferences(mot), demarrerCamera()]);
  if (etat.mot !== mot) return; // l'utilisateur est reparti entre-temps
  etat.refs = refs;
  if (cameraOk) {
    messageCamera("");
    if (!etat.boucleActive) {
      etat.boucleActive = true;
      requestAnimationFrame(boucle);
    }
  }
  verrouillerEcran();
  majBoutonAction();
}

async function ouvrirLettre(lettre, defi) {
  etat.type = "lettre";
  etat.mot = lettre;
  etat.defi = defi;
  etat.aideVue = false;
  etat.refs = [];
  $("titre-mot").textContent = defi ? `Défi : lettre ${lettre.nom}` : `Lettre ${lettre.nom}`;
  majBadge();
  afficher("exercice");
  if (history.state?.ecran !== "exercice") history.pushState({ ecran: "exercice" }, "");
  passer("direct");
  majModele();
  const cameraOk = await demarrerCamera();
  if (etat.mot !== lettre) return;
  if (cameraOk) {
    messageCamera("");
    if (!etat.boucleActive) {
      etat.boucleActive = true;
      requestAnimationFrame(boucle);
    }
  }
  verrouillerEcran();
  majBoutonAction();
}

// Suivi en direct : on compare la main à toutes les lettres à chaque image.
function suivreLettre(resMains, ratio, maintenant) {
  const lettre = etat.mot;
  let meilleur = null;
  (resMains.landmarks ?? []).forEach((plan, i) => {
    const monde = resMains.worldLandmarks?.[i];
    if (!monde) return;
    const main = decrireMain(monde.map((p) => [p.x, p.y, p.z]), plan.map((p) => [p.x * ratio, p.y]));
    const classement = classerLettres(main, etat.lettres);
    const cout = classement.find((c) => c.cle === lettre.cle).cout;
    if (!meilleur || cout < meilleur.cout) meilleur = { t: maintenant, cout, plusProche: classement[0] };
  });
  etat.suivi = etat.suivi.filter((s) => maintenant - s.t < FENETRE_LETTRE);
  if (!meilleur) {
    etat.suivi = [];
    etat.debutTenue = 0;
    majJauge(0, "Montre ta main à la caméra.");
    return;
  }
  etat.suivi.push(meilleur);
  const couts = etat.suivi.map((s) => s.cout).sort((a, b) => a - b);
  const score = scoreDepuisCout(couts[couts.length >> 1]);
  // Une autre lettre ressemble nettement plus au geste ?
  const autre = meilleur.plusProche.cle !== lettre.cle && meilleur.plusProche.cout < meilleur.cout - 0.03
    ? etat.lettres.find((l) => l.cle === meilleur.plusProche.cle) : null;
  if (score >= SEUIL_REUSSI && !autre) {
    if (!etat.debutTenue) {
      etat.debutTenue = maintenant;
      etat.meilleurTenue = 0;
    }
    etat.meilleurTenue = Math.max(etat.meilleurTenue, score);
    if (maintenant - etat.debutTenue >= TENUE_LETTRE) {
      lettreReussie(etat.meilleurTenue);
      return;
    }
    majJauge(score, "Parfait, garde la position…");
    return;
  }
  etat.debutTenue = 0;
  majJauge(score, autre ? `On dirait plutôt un ${autre.nom}. Regarde bien la photo.`
    : score >= 45 ? "Presque ! Ajuste la position de tes doigts."
    : "Forme la lettre comme sur la photo, paume face à la caméra.");
}

function majJauge(score, texte) {
  const jauge = $("jauge-remplie");
  jauge.style.width = `${score}%`;
  jauge.className = `jauge-remplie ${couleurScore(score)}`;
  $("consigne").textContent = texte;
}

function lettreReussie(score) {
  console.log("[PyTalk] lettre réussie", etat.mot.cle, score);
  noterProgres(score);
  passer("resultat");
  majModele();
  majBadge();
  afficherResultat({ score, conseils: [], miroir: false });
}

function noterProgres(score) {
  const fiche = etat.progres[etat.mot.cleProgres] ?? { meilleur: 0, essais: 0 };
  fiche.essais += 1;
  fiche.meilleur = Math.max(fiche.meilleur, score);
  fiche.dernier = score;
  etat.progres[etat.mot.cleProgres] = fiche;
  sauverProgres();
}

function majBadge() {
  const fiche = etat.progres[etat.mot.cleProgres];
  const badge = $("badge-meilleur");
  badge.hidden = !fiche;
  if (fiche) {
    badge.textContent = `★ ${fiche.meilleur}`;
    badge.className = `badge ${couleurScore(fiche.meilleur)}`;
  }
}

function majModele() {
  const mot = etat.mot;
  const video = $("modele");
  const photo = $("photo-lettre");
  const cache = etat.defi && !etat.aideVue && etat.phase !== "resultat";
  const lettre = etat.type === "lettre";
  $("cache-defi").hidden = !cache;
  $("cache-defi").querySelector("p").textContent = lettre ? `Te souviens-tu de la lettre ${mot.nom} ?` : "Te souviens-tu de ce signe ?";
  $("voir-modele").textContent = lettre ? "Voir la photo" : "Voir la vidéo";
  document.querySelector(".zone-modele").classList.toggle("photo", lettre);
  video.hidden = lettre;
  photo.hidden = !lettre;
  if (lettre) {
    video.pause();
    $("autre-version").hidden = true;
    $("credit").textContent = cache ? "" : `Photo : ${mot.auteur} — ${mot.licence} — Wikimedia Commons`;
    if (!photo.src.endsWith(mot.image)) {
      photo.src = mot.image;
      photo.alt = `Main formant la lettre ${mot.nom} en LSF`;
    }
    photo.style.visibility = cache ? "hidden" : "visible";
    return;
  }
  const variante = mot.variantes[etat.variante];
  $("credit").textContent = cache ? "" : `Vidéo : ${variante.auteur} — ${variante.licence} — Wikimedia Commons`;
  $("autre-version").hidden = cache || mot.variantes.length < 2;
  $("autre-version").textContent = `Version ${etat.variante + 1}/${mot.variantes.length} ↻`;
  if (!video.src.endsWith(variante.video)) video.src = variante.video;
  if (cache) video.pause();
  else video.play().catch(() => {});
}

function majBoutonAction() {
  const pret = Boolean(etat.detecteurs && etat.refs.length && (etat.flux || VIDEO_TEST));
  const action = $("action");
  const consigne = $("consigne");
  action.hidden = etat.phase === "direct";
  if (etat.phase === "direct") {
    if (!etat.detecteurs) consigne.textContent = "Préparation de la détection…";
    else if (!etat.suivi.length) {
      consigne.textContent = etat.mot.mouvement
        ? `Forme la lettre avec ta main. ${etat.mot.mouvement}`
        : "Forme la lettre avec ta main, face à la caméra.";
    }
  } else if (etat.phase === "pret") {
    action.textContent = "Essayer";
    action.disabled = !pret;
    consigne.textContent = !etat.detecteurs ? "Préparation de la détection…"
      : etat.defi && !etat.aideVue ? "Te souviens-tu de ce signe ? Essaie !"
      : "Regarde le modèle, puis essaie.";
  } else if (etat.phase === "compte") {
    action.textContent = "Annuler";
    action.disabled = false;
    consigne.textContent = "Mets-toi face à la caméra, mains baissées…";
  } else if (etat.phase === "enregistrer") {
    action.textContent = "Terminer";
    action.disabled = false;
    consigne.textContent = "Fais le signe, puis baisse les mains.";
  }
}

function passer(phase) {
  etat.phase = phase;
  clearInterval(etat.minuteurCompte);
  $("compte").hidden = phase !== "compte";
  $("enregistrement").hidden = phase !== "enregistrer";
  $("panneau").hidden = phase === "resultat";
  $("jauge").hidden = phase !== "direct";
  if (phase === "direct") {
    etat.suivi = [];
    etat.debutTenue = 0;
    $("jauge-remplie").style.width = "0";
  }
  $("resultat").hidden = phase !== "resultat";
  const resultat = phase === "resultat";
  $("camera").hidden = resultat && !$("replay").hidden;
  if (!resultat) {
    $("replay").hidden = true;
    $("camera").hidden = false;
  }
  if (phase === "compte") {
    let reste = COMPTE_A_REBOURS;
    $("compte").textContent = reste;
    etat.minuteurCompte = setInterval(() => {
      reste -= 1;
      if (reste <= 0) passer("enregistrer");
      else $("compte").textContent = reste;
    }, 1000);
  }
  if (phase === "enregistrer") {
    etat.essai = [];
    etat.debutEnregistrement = performance.now();
    etat.derniereMain = 0;
    etat.mainsVues = false;
    demarrerReplay();
  }
  majBoutonAction();
}

function enregistrer(pts, maintenant) {
  etat.essai.push(pts);
  if (mainsActives(pts)) {
    etat.mainsVues = true;
    etat.derniereMain = maintenant;
  }
  const ecoule = maintenant - etat.debutEnregistrement;
  const dureeMax = Math.max(DUREE_MAX, 2500 * Math.max(...etat.refs.map((r) => r.duree)));
  if ((etat.mainsVues && maintenant - etat.derniereMain > FIN_SANS_MAINS && ecoule > DUREE_MIN) || ecoule > dureeMax) {
    noter();
  }
}

function noter() {
  const duree = (performance.now() - etat.debutEnregistrement) / 1000;
  const fpsEssai = etat.essai.length / Math.max(duree, 0.1);
  const resultats = etat.refs.map((r) => comparer(r.points, etat.essai, r.fps, fpsEssai));
  let meilleur = 0;
  resultats.forEach((r, i) => { if (r.score > resultats[meilleur].score) meilleur = i; });
  const resultat = resultats[meilleur];
  console.log("[PyTalk] résultat", etat.mot.cle, JSON.stringify({ ...resultat, images: etat.essai.length, fps: Math.round(fpsEssai) }));
  noterProgres(resultat.score);

  etat.variante = meilleur;
  arreterReplay();
  passer("resultat");
  majModele();
  majBadge();
  afficherResultat(resultat);
}

function afficherResultat(r) {
  const score = $("score");
  score.textContent = r.score;
  score.className = `score ${couleurScore(r.score)}`;
  $("appreciation").textContent = etat.type === "lettre" ? (r.score >= 85 ? "Excellent !" : "Lettre réussie !")
    : r.score >= 85 ? "Excellent !"
    : r.score >= SEUIL_REUSSI ? "Bien joué !"
    : r.score >= 45 ? "Presque, continue !"
    : "Pas encore, réessaie.";
  const liste = $("conseils");
  liste.replaceChildren();
  for (const c of r.score < 90 ? r.conseils : []) {
    const li = document.createElement("li");
    li.textContent = c;
    liste.append(li);
  }
  $("note-miroir").hidden = !r.miroir;
  $("calque").getContext("2d").clearRect(0, 0, $("calque").width, $("calque").height);
  $("resultat").scrollIntoView({ behavior: "smooth", block: "end" });
}

// ----- rejouer l'essai (si le navigateur sait enregistrer la vidéo)

function demarrerReplay() {
  etat.morceaux = [];
  etat.enregistreur = null;
  if (!etat.flux || typeof MediaRecorder === "undefined") return;
  const type = ["video/mp4", "video/webm"].find((t) => MediaRecorder.isTypeSupported?.(t));
  try {
    etat.enregistreur = new MediaRecorder(etat.flux, type ? { mimeType: type } : undefined);
    etat.enregistreur.ondataavailable = (e) => e.data.size && etat.morceaux.push(e.data);
    etat.enregistreur.start();
  } catch {
    etat.enregistreur = null;
  }
}

function arreterReplay() {
  const enregistreur = etat.enregistreur;
  if (!enregistreur || enregistreur.state === "inactive") return;
  enregistreur.onstop = () => {
    if (etat.phase !== "resultat" || !etat.morceaux.length) return;
    const replay = $("replay");
    if (replay.src) URL.revokeObjectURL(replay.src);
    replay.src = URL.createObjectURL(new Blob(etat.morceaux, { type: enregistreur.mimeType }));
    replay.hidden = false;
    $("camera").hidden = true;
    replay.play().catch(() => {});
  };
  enregistreur.stop();
}

function quitterExercice() {
  clearInterval(etat.minuteurCompte);
  if (etat.enregistreur?.state === "recording") etat.enregistreur.stop();
  etat.phase = "pret";
  etat.mot = null;
  etat.type = "mot";
  arreterCamera();
  $("modele").pause();
  $("replay").pause();
  libererEcran();
  rendreAccueil();
  afficher("accueil");
}

function auHasard(liste, sauf) {
  const candidats = liste.length > 1 ? liste.filter((m) => m !== sauf) : liste;
  const poids = candidats.map((m) => {
    const meilleur = etat.progres[m.cleProgres]?.meilleur;
    return meilleur === undefined ? 100 : 110 - meilleur;
  });
  let tirage = Math.random() * poids.reduce((a, b) => a + b, 0);
  for (let i = 0; i < candidats.length; i++) {
    tirage -= poids[i];
    if (tirage <= 0) return candidats[i];
  }
  return candidats[candidats.length - 1];
}

// ----- garder l'écran allumé pendant l'exercice

async function verrouillerEcran() {
  try {
    etat.verrouEcran = await navigator.wakeLock?.request("screen");
  } catch {
    /* non disponible */
  }
}

function libererEcran() {
  etat.verrouEcran?.release().catch(() => {});
  etat.verrouEcran = null;
}

// ---------------------------------------------------------------- événements

$("action").addEventListener("click", () => {
  if (etat.phase === "pret") passer("compte");
  else if (etat.phase === "compte") passer("pret");
  else if (etat.phase === "enregistrer") noter();
});
$("reessayer").addEventListener("click", () => {
  passer(etat.type === "lettre" ? "direct" : "pret");
  majModele();
  $("zone-camera").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("suivant").addEventListener("click", () => {
  const liste = etat.type === "lettre" ? etat.lettres : etat.mots;
  const i = liste.indexOf(etat.mot);
  ouvrir(etat.defi ? auHasard(liste, etat.mot) : liste[(i + 1) % liste.length], etat.defi);
});
$("voir-modele").addEventListener("click", () => {
  etat.aideVue = true;
  majModele();
  majBoutonAction();
});
$("autre-version").addEventListener("click", () => {
  etat.variante = (etat.variante + 1) % etat.mot.variantes.length;
  majModele();
});
$("retour").addEventListener("click", () => history.back());
window.addEventListener("popstate", () => {
  if ($("exercice").classList.contains("actif")) quitterExercice();
  else if ($("apropos").classList.contains("actif")) afficher("accueil");
});
$("lancer-defi").addEventListener("click", () => elements().length && ouvrir(auHasard(elements()), true));
for (const onglet of document.querySelectorAll(".onglet")) {
  onglet.addEventListener("click", () => {
    etat.categorie = onglet.dataset.categorie;
    try {
      localStorage.setItem(CLE_ONGLET, etat.categorie);
    } catch {
      /* stockage indisponible */
    }
    rendreAccueil();
  });
}
$("ouvrir-apropos").addEventListener("click", () => {
  history.pushState({ ecran: "apropos" }, "");
  afficher("apropos");
});
$("fermer-apropos").addEventListener("click", () => history.back());

let confirmationEffacer = false;
$("effacer-progres").addEventListener("click", (e) => {
  if (!confirmationEffacer) {
    confirmationEffacer = true;
    e.target.textContent = "Appuie encore pour confirmer";
    setTimeout(() => {
      confirmationEffacer = false;
      e.target.textContent = "Effacer mes scores";
    }, 4000);
    return;
  }
  etat.progres = {};
  sauverProgres();
  rendreAccueil();
  e.target.textContent = "Scores effacés";
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && etat.mot) verrouillerEcran();
});

// ---------------------------------------------------------------- démarrage

async function demarrer() {
  if ("serviceWorker" in navigator && !VIDEO_TEST) {
    navigator.serviceWorker.register("sw.js").catch((e) => console.warn("[PyTalk] service worker", e));
  }
  const [mots, lettres] = await Promise.all([
    fetch("data/mots.json").then((r) => r.json()),
    fetch("data/alphabet.json").then((r) => r.json()).catch(() => []),
  ]);
  etat.mots = mots.sort((a, b) => a.nom.localeCompare(b.nom, "fr")).map((m) => ({ ...m, cleProgres: m.cle }));
  etat.lettres = lettres.map((l) => ({ ...l, cleProgres: `lettre-${l.cle}`, descripteurs: decrireLettre(l) }));
  rendreAccueil();
  rendreCredits();
  const statut = $("etat-chargement");
  try {
    await initialiserDetection();
    statut.hidden = true;
    console.log("[PyTalk] détection prête");
  } catch (erreur) {
    console.error("[PyTalk] échec du chargement de MediaPipe", erreur);
    statut.textContent = "La détection n'a pas pu démarrer. Vérifie ta connexion et relance l'app.";
  }
  if (etat.mot) majBoutonAction();
}

demarrer();
