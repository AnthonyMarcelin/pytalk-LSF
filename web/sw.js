// PyTalk LSF — service worker : l'app fonctionne hors ligne une fois ouverte.
// Change VERSION à chaque mise à jour pour que les téléphones la récupèrent.
const VERSION = "pytalk-lsf-v3";

const COQUILLE = [
  "./",
  "index.html",
  "style.css",
  "app.js",
  "comparaison.js",
  "alphabet.js",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/apple-touch-icon.png",
  "lib/mediapipe/vision_bundle.mjs",
  "lib/mediapipe/wasm/vision_wasm_internal.js",
  "lib/mediapipe/wasm/vision_wasm_internal.wasm",
  "models/hand_landmarker.task",
  "models/pose_landmarker_lite.task",
  "data/mots.json",
  "data/alphabet.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(COQUILLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((c) => c !== VERSION).map((c) => caches.delete(c))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const requete = event.request;
  if (requete.method !== "GET" || new URL(requete.url).origin !== location.origin) return;
  // Les vidéos sont lues par morceaux (Range) : on laisse le navigateur gérer,
  // mais on garde une copie complète pour le hors ligne.
  if (requete.headers.has("range")) {
    event.respondWith(lireVideo(requete));
    return;
  }
  event.respondWith(
    caches.match(requete, { ignoreSearch: true }).then((enCache) => enCache || fetch(requete).then((reponse) => {
      if (reponse.ok) {
        const copie = reponse.clone();
        caches.open(VERSION).then((cache) => cache.put(requete, copie));
      }
      return reponse;
    })),
  );
});

// Safari exige des réponses 206 (partielles) pour les vidéos.
async function lireVideo(requete) {
  const cache = await caches.open(VERSION);
  const url = requete.url;
  let complete = await cache.match(url);
  if (!complete) {
    try {
      const reponse = await fetch(url);
      if (!reponse.ok) return fetch(requete);
      await cache.put(url, reponse.clone());
      complete = reponse;
    } catch {
      return fetch(requete);
    }
  }
  const donnees = await complete.arrayBuffer();
  const [, debutTexte, finTexte] = /bytes=(\d*)-(\d*)/.exec(requete.headers.get("range")) || [];
  const debut = Number(debutTexte) || 0;
  const fin = finTexte ? Math.min(Number(finTexte), donnees.byteLength - 1) : donnees.byteLength - 1;
  return new Response(donnees.slice(debut, fin + 1), {
    status: 206,
    headers: {
      "Content-Type": complete.headers.get("Content-Type") || "video/mp4",
      "Content-Range": `bytes ${debut}-${fin}/${donnees.byteLength}`,
      "Content-Length": String(fin - debut + 1),
      "Accept-Ranges": "bytes",
    },
  });
}
