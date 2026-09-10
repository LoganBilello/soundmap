import * as THREE from "three";
import Globe from "globe.gl";
import { Player } from "./audio.js";
import { DotLayer } from "./points.js";
import { renderBasemap, pickCountry } from "./basemap.js";

// Natural Earth ships some names we do not want on screen: a non-standard
// lowercase "eSwatini", and abbreviations that read as typos at label size.
const NAME_FIXES = {
  "eSwatini": "Eswatini",
  "Côte d'Ivoire": "Cote d'Ivoire",
  "Dem. Rep. Congo": "DR Congo",
  "Central African Rep.": "Central African Republic",
  "Dominican Rep.": "Dominican Republic",
  "Eq. Guinea": "Equatorial Guinea",
  "S. Sudan": "South Sudan",
  "Bosnia and Herz.": "Bosnia and Herzegovina",
  "Solomon Is.": "Solomon Islands",
  "N. Cyprus": "Northern Cyprus",
  "W. Sahara": "Western Sahara",
  "Falkland Is.": "Falkland Islands",
  "Fr. S. Antarctic Lands": "French Southern Territories",
};
const displayName = (n) => NAME_FIXES[n] ?? n;

// Natural Earth and Every Noise name the same country differently, which left
// real data stranded: both Congos have genres but rendered as "no data" and
// were unclickable. Only genuine pairs belong here -- North Korea, Syria and
// Cuba are absent from Every Noise for real, and must stay unmatched.
const COUNTRY_ALIASES = {
  "dem rep congo": "congo (kinshasa)",
  "democratic republic of the congo": "congo (kinshasa)",
  "congo": "congo (brazzaville)",
  "republic of the congo": "congo (brazzaville)",
  "trinidad and tobago": "trinidad & tobago",
};

const $ = (s) => document.querySelector(s);
const body = $("#body");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const norm = (s) => s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();

const state = { cities: [], genres: [], countries: {}, meta: {}, view: null, genre: null };

// Countries are baked into the globe texture, so the map IS the sphere surface
// and dots need only enough lift to win the depth test against it.
//
// This used to be 0.018 to clear an extruded polygon layer, and that height
// cost accuracy: a dot 1.8 units above a 100-unit sphere shows real parallax at
// oblique angles, drifting off its true spot near the limb. MapLibre draws its
// circles flat on the surface, which is why radio.garden's sit exactly right.
const DOT_ALTITUDE = 0.0015;

// ---------------------------------------------------------------- data

const [cities, genres, countries, meta, world] = await Promise.all(
  ["/data/cities.json", "/data/genres.json", "/data/countries.json",
   "/data/meta.json", "/countries-110m.geojson"].map((u) => fetch(u).then((r) => r.json()))
);
Object.assign(state, { cities, genres, countries, meta });

$("#tagline").textContent =
  `${meta.cities.toLocaleString()} cities · ${meta.genres.toLocaleString()} genres · ${meta.withOrigin.toLocaleString()} located origins`;

// EVERY genre is searchable, not only those a city listens to. Two thirds of
// the taxonomy has no city data -- suomisaundi, korean city pop, swedish drill
// -- yet all of them have a preview clip and many have a documented origin.
// Filtering them out of search made them unreachable for no reason.
const allGenres = genres.map((_, i) => i);

// Genres a city listens to, kept for the featured picks on the intro, where a
// genre with somewhere to fly to makes a better first click.
const live = genres.map((g, i) => (g.n > 0 ? i : -1)).filter((i) => i >= 0);

// ---------------------------------------------------------------- audio

const player = new Player((s) => {
  const np = $("#np");
  if (s.error) {
    np.classList.add("show");
    $("#np-name").textContent = s.error;
    $("#np-sub").textContent = "";
    setTimeout(() => np.classList.remove("show"), 3400);
  } else if (s.slug) {
    np.classList.add("show", "playing");
    $("#np-name").textContent = s.genre.name;
    $("#np-sub").textContent = s.source === "loading" ? "finding a clip…" : s.source;
  } else {
    np.classList.remove("show", "playing");
    $("#bar i").style.width = "0%";
  }
  syncPlaying();
});

player.audio.addEventListener("timeupdate", () => {
  const { currentTime: t, duration: d } = player.audio;
  if (d) $("#bar i").style.width = `${(t / d) * 100}%`;
});

// ---- volume -------------------------------------------------------------
// Remembered per browser. localStorage can throw outright in private mode or
// with site data blocked, so every access is guarded and falls back to 0.85.
const VOL_KEY = "soundmap.volume";
const volEl = $("#vol");
const muteEl = $("#vol-mute");
let lastAudible = 0.85;

function applyVolume(v, persist = true) {
  v = Math.min(1, Math.max(0, v));
  player.audio.volume = v;
  volEl.value = Math.round(v * 100);
  muteEl.classList.toggle("muted", v === 0);
  muteEl.setAttribute("aria-label", v === 0 ? "Unmute" : "Mute");
  if (v > 0) lastAudible = v;
  if (persist) { try { localStorage.setItem(VOL_KEY, String(v)); } catch {} }
}

let startVolume = 0.85;
try {
  const saved = parseFloat(localStorage.getItem(VOL_KEY));
  if (Number.isFinite(saved)) startVolume = saved;
} catch {}
applyVolume(startVolume, false);

// Global stop. The genre panel's own button vanishes as soon as you navigate
// back to a city or country, which left audio playing with nothing to stop it.
$("#np-stop").addEventListener("click", () => player.stop());

volEl.addEventListener("input", () => applyVolume(volEl.value / 100));
muteEl.addEventListener("click", () => applyVolume(player.audio.volume > 0 ? 0 : lastAudible));
// The slider lives inside the globe's pointer area; don't let arrow keys or
// drags there reach the globe controls or the space-to-play shortcut.
volEl.addEventListener("keydown", (e) => e.stopPropagation());
volEl.addEventListener("pointerdown", (e) => e.stopPropagation());

function syncPlaying() {
  const slug = player.slug;
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.slug === slug));
  const btn = document.querySelector("button.play");
  if (btn) {
    const on = btn.dataset.play === slug;
    btn.classList.toggle("stop", on);
    btn.textContent = on ? "■ Stop" : "▶ Play a clip";
  }
}

const playGenre = (i) => player.toggle(genres[i].slug, genres[i]);

// ---------------------------------------------------------------- globe

const globe = Globe()(document.getElementById("globe"))
  .backgroundColor("#04060c")
  .showAtmosphere(true)
  .atmosphereColor("#4a7fb5")
  .atmosphereAltitude(0.19)
  // No polygon layer. Countries are baked into the globe texture below; as
  // meshes they cost 1,641 draw calls and 1M triangles per frame.
  // Cities are NOT drawn with .pointsData — that builds one sphere mesh each.
  // They are a single THREE.Points cloud in a custom layer; see points.js.
  // Influence: where a sound began, arcing out to everywhere it reached.
  .arcsData([])
  .arcStartLat("sLat").arcStartLng("sLon").arcEndLat("eLat").arcEndLng("eLon")
  .arcColor((a) => [`${a.color}00`, `${a.color}dd`])
  .arcStroke(0.32)
  .arcAltitudeAutoScale(0.42)
  .arcDashLength(0.42).arcDashGap(0.55).arcDashAnimateTime(2400)
  // Country names, thinned out by zoom so the globe never looks cluttered.
  .labelsData([])
  .labelLat("lat").labelLng("lon").labelText("name")
  .labelColor((d) => (d.has ? "rgba(203,217,240,0.62)" : "rgba(132,148,180,0.30)"))
  .labelDotRadius(0)
  .labelAltitude(0.009)
  .labelResolution(2)
  .onLabelClick((d) => d.feature && onCountry(d.feature))
  // A pulsing ring marks the origin itself.
  .ringsData([])
  .ringLat("lat").ringLng("lon")
  .ringColor((r) => () => r.color)
  .ringMaxRadius(4.6).ringPropagationSpeed(1.5).ringRepeatPeriod(820);

// globe.gl measures its container once and can end up with a 0x0 canvas.
const resize = () => globe.width(window.innerWidth).height(window.innerHeight);
resize();
window.addEventListener("resize", resize);

// Countries rasterised once into the globe's own texture: one draw call for
// the entire world map, versus ~1,600 when they were extruded meshes.
{
  const canvas = renderBasemap(world.features, (f) => !!countryOf(f));
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = globe.renderer().capabilities.getMaxAnisotropy();
  const mat = globe.globeMaterial();
  mat.map = tex;
  mat.color.set("#ffffff");   // let the texture supply the colour
  mat.needsUpdate = true;
}
const ctl = globe.controls();
ctl.autoRotate = true;
ctl.autoRotateSpeed = 0.28;
ctl.enableDamping = true;
ctl.dampingFactor = 0.09;
ctl.addEventListener("start", () => (ctl.autoRotate = false));

// Natural Earth ships hand-placed label anchors and a LABELRANK that says how
// important each country is to show. Use both so labels thin out when zoomed
// away and fill in as you approach, instead of overlapping into mush.
const labelPts = world.features
  .filter((f) => f.properties.LABEL_X != null && f.properties.LABEL_Y != null)
  .map((f) => ({
    lat: f.properties.LABEL_Y,
    lon: f.properties.LABEL_X,
    name: displayName(f.properties.NAME || f.properties.ADMIN),
    rank: f.properties.LABELRANK ?? 9,
    has: !!countryOf(f),
    feature: f,
  }));

let lastRank = -1;
let lastLabelSize = -1;

// ISO code -> the basemap's hand-placed label point, so a Wikipedia origin
// known only to country level can still be shown somewhere sensible instead of
// being discarded or given a fabricated centroid.
const countryPoint = {};
for (const f of world.features) {
  const iso = f.properties.ISO_A2;
  if (iso && iso !== "-99" && f.properties.LABEL_X != null) {
    countryPoint[iso] = { lat: f.properties.LABEL_Y, lon: f.properties.LABEL_X };
  }
}

function syncLabels() {
  // pointOfView() is not meaningful until the globe has initialised, and a
  // NaN altitude silently propagates into labelSize, rendering nothing.
  const pov = globe.pointOfView();
  const alt = Number.isFinite(pov?.altitude) ? pov.altitude : 2.5;
  const maxRank = alt > 2.2 ? 2 : alt > 1.6 ? 3 : alt > 1.05 ? 4 : alt > 0.65 ? 5 : 7;
  // labelSize is in globe-radius units, so on-screen size is roughly
  // labelSize/altitude. Scaling it with altitude keeps labels legible at every
  // zoom instead of shrinking to invisible threads when you pull back.
  // GUARDED. onZoom fires on every camera movement, so an unguarded
  // labelsData() call here rebuilt all 36 label objects on every frame of
  // every drag. That was the lag: the dots were never the problem.
  if (maxRank !== lastRank) {
    lastRank = maxRank;
    globe.labelsData(labelPts.filter((d) => d.rank <= maxRank));
  }

  // Labels are background context. Kept small and dim so the city dots stay
  // the loudest thing on the globe; at 1.28x they collided badly (Belgium over
  // Germany, Portugal over Spain) and read as the primary layer.
  // Quantised so a continuous zoom touches this a few dozen times, not
  // thousands.
  const size = Math.round(Math.max(0.26, Math.min(1.75, alt * 0.68)) * 20) / 20;
  if (size !== lastLabelSize) {
    lastLabelSize = size;
    globe.labelSize(size);
  }
}
// Registered here but only invoked later, so the clustering state it touches
// is safely initialised by then. The first paint happens at the bottom of the
// file, once every function and binding below exists.
globe.onZoom(() => { syncLabels(); syncPoints(); });
syncLabels();
if (import.meta.env.DEV) window.__globe = globe;
// Dev-only handles so band-swap cost can be timed synchronously; whole-frame
// timing in this environment is too noisy to attribute anything to.
if (import.meta.env.DEV) {
  window.__perf = () => ({ band, paintColors, buildNodes, cellSizeFor, dots, bandCache });
}

// ------------------------------------------------------------- clustering
// Thousands of city dots overlap into an unreadable mass when zoomed out, and
// every one is an interactive mesh, which is what makes the globe drag badly.
// Bin cities into roughly equal-area cells sized by camera altitude: distant
// views draw a few hundred weighted clusters, close views resolve real cities.

function currentAlt() {
  const pov = globe.pointOfView();
  return Number.isFinite(pov?.altitude) ? pov.altitude : 2.5;
}

/** Cell width in degrees; 0 means "stop clustering, show every city". */
function cellSizeFor(alt) {
  if (alt < 0.40) return 0;
  if (alt < 0.70) return 1.1;
  if (alt < 1.10) return 2.2;
  if (alt < 1.70) return 3.8;
  if (alt < 2.40) return 6.0;
  return 9.0;
}

function buildNodes(cell) {
  if (!cell) {
    return state.cities.map((c) => ({ lat: c.lat, lon: c.lon, count: 1, rep: c, members: [c] }));
  }
  const bins = new Map();
  for (const c of state.cities) {
    // Widen longitude cells toward the poles, or Reykjavik and Helsinki end up
    // in cells a fraction the real width of one near the equator.
    const lonCell = cell / Math.max(0.2, Math.cos((c.lat * Math.PI) / 180));
    const key = `${Math.floor((c.lat + 90) / cell)}:${Math.floor((c.lon + 180) / lonCell)}`;
    let bin = bins.get(key);
    if (!bin) bins.set(key, (bin = []));
    bin.push(c);
  }

  const out = [];
  for (const members of bins.values()) {
    // The city listening to the most genres speaks for the cluster, and the
    // cluster is drawn AT that city, never at the average of its members.
    //
    // A centroid is not a real place, so it shifts whenever the membership
    // changes: cross a zoom band and every dot visibly slides. Anchoring to a
    // real city keeps every dot geographically fixed, and a splitting cluster
    // reads as new dots appearing around one that stayed put.
    const rep = members.reduce((a, b) => (b.g.length > a.g.length ? b : a));
    out.push({ lat: rep.lat, lon: rep.lon, count: members.length, rep, members });
  }
  return out;
}

// One THREE.Points cloud for every city, injected as a custom layer so
// globe.gl parents it to the globe and it stays aligned as the camera orbits.
const dots = new DotLayer(globe, { onClick: onNodeClick, onHover: showTip });
dots.setPixelRatio(globe.renderer().getPixelRatio());
dots.allocate(state.cities.length, globe.getGlobeRadius());
globe
  .customLayerData([{ id: "dots" }])
  .customThreeObject(() => dots.object)
  .customThreeObjectUpdate(() => {});

// Genre colours parsed to floats ONCE. THREE.Color.set() parses a CSS string
// on every call, and doing that for ~3,600 dots on each zoom band was a large
// share of the hitch.
const genreRGB = new Float32Array(genres.length * 3);
for (let i = 0; i < genres.length; i++) {
  const h = genres[i].color;
  genreRGB[i * 3] = parseInt(h.slice(1, 3), 16) / 255;
  genreRGB[i * 3 + 1] = parseInt(h.slice(3, 5), 16) / 255;
  genreRGB[i * 3 + 2] = parseInt(h.slice(5, 7), 16) / 255;
}
const DIM_R = 0.33, DIM_G = 0.385, DIM_B = 0.49;

// Every zoom band is computed once and kept. City positions never move, so
// zooming back to a band already visited costs a memcpy rather than rebuilding
// thousands of node objects and typed arrays.
const bandCache = new Map();

function band(cell) {
  const hit = bandCache.get(cell);
  if (hit) return hit;

  const nodes = buildNodes(cell);
  const pos = new Float32Array(nodes.length * 3);
  const siz = new Float32Array(nodes.length);
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    const c = globe.getCoords(n.lat, n.lon, DOT_ALTITUDE);
    pos[i * 3] = c.x; pos[i * 3 + 1] = c.y; pos[i * 3 + 2] = c.z;
    siz[i] = nodeSize(n);
  }

  const entry = { nodes, pos, siz, col: new Float32Array(nodes.length * 3) };
  bandCache.set(cell, entry);
  return entry;
}

function nodeHasGenre(n, gi) {
  for (const c of n.members) {
    for (let k = 0; k < c.g.length; k++) if (c.g[k][0] === gi) return true;
  }
  return false;
}

function paintColors(entry) {
  const sel = state.genre;
  const { nodes, col } = entry;
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    let k = -1;
    if (sel == null) k = n.rep.dom * 3;
    else if (nodeHasGenre(n, sel)) k = sel * 3;

    if (k >= 0) {
      col[i * 3] = genreRGB[k]; col[i * 3 + 1] = genreRGB[k + 1]; col[i * 3 + 2] = genreRGB[k + 2];
    } else {
      col[i * 3] = DIM_R; col[i * 3 + 1] = DIM_G; col[i * 3 + 2] = DIM_B;
    }
  }
  dots.setColors(col);
}

let lastCell = -1;
let currentBand = null;
function syncPoints(force = false) {
  const cell = cellSizeFor(currentAlt());
  if (!force && cell === lastCell) return;   // only swap when the band changes
  lastCell = cell;
  currentBand = band(cell);
  dots.setBand(currentBand.nodes, currentBand.pos, currentBand.siz);
  paintColors(currentBand);
}

// Sprite size in shader units, not world radius: the vertex shader multiplies
// this by uScale/distance to get a pixel size.
function nodeSize(n) {
  return n.count === 1
    ? 0.85 + Math.min(1.0, n.rep.g.length * 0.032)
    : Math.min(4.0, 1.35 + Math.sqrt(n.count) * 0.28);
}

function nodeLabel(n) {
  if (n.count === 1) {
    return `<b>${esc(n.rep.city)}</b><br><span class="d">${esc(n.rep.country)}</span>
      <span class="dd"> · ${n.rep.g.length} genres</span>`;
  }
  return `<b>${n.count} cities</b><br>
    <span class="d">${esc(n.rep.city)} and ${n.count - 1} more</span><br>
    <span class="dd">click to zoom in</span>`;
}

/** Tooltip follows the cursor; the dot layer has no DOM of its own. */
function showTip(n, e) {
  const tip = $("#tip");
  if (!n || !e) return void tip.classList.remove("show");
  tip.innerHTML = nodeLabel(n);
  tip.classList.add("show");
  const pad = 16;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = `${Math.min(e.clientX + pad, innerWidth - w - 8)}px`;
  tip.style.top = `${Math.min(e.clientY + pad, innerHeight - h - 8)}px`;
}

function onNodeClick(n, e) {
  if (!n) return clickCountryAt(e);
  if (n.count === 1) return showCity(n.rep);
  flyTo(n.lat, n.lon, Math.max(0.34, currentAlt() * 0.45));  // splits on re-cluster
}

/** No dot under the cursor, so test the globe surface for a country instead. */
function clickCountryAt(e) {
  if (!e) return;
  const rect = globe.renderer().domElement.getBoundingClientRect();
  const c = globe.toGlobeCoords(e.clientX - rect.left, e.clientY - rect.top);
  if (!c) return;                                   // clicked past the horizon
  const f = pickCountry(world.features, c.lat, c.lng);
  if (f) onCountry(f);
}
// Natural Earth abbreviates some names ("Dem. Rep. Congo") where Every Noise
// spells them out, so try every name field the basemap carries.
function countryOf(f) {
  const p = f.properties;
  for (const n of [p.NAME, p.NAME_LONG, p.ADMIN, p.BRK_NAME, p.NAME_CIAWF]) {
    if (!n) continue;
    const k = norm(n);
    const hit = state.countries[k] ?? state.countries[COUNTRY_ALIASES[k]];
    if (hit) return hit;
  }
  return null;
}
const repaint = () => { if (currentBand) paintColors(currentBand); };

function flyTo(lat, lon, altitude = 1.75) {
  ctl.autoRotate = false;
  globe.pointOfView({ lat, lng: lon, altitude }, 950);
}
function clearOverlays() {
  globe.ringsData([]).arcsData([]);
}

// ---------------------------------------------------------------- views

const chip = (i, w) => {
  const g = genres[i];
  return !g ? "" : `<button class="chip" data-genre="${i}" data-slug="${esc(g.slug)}">
      <span class="dot" style="background:${g.color}"></span>
      <span class="label">${esc(g.name)}</span>
      ${w != null ? `<span class="w">${w}</span>` : ""}</button>`;
};
const cityChip = (id, w, color) => {
  const c = state.cities[id];
  return !c ? "" : `<button class="chip" data-city="${id}">
      <span class="dot" style="background:${color ?? genres[c.dom].color}"></span>
      <span class="label">${esc(c.city)} <span class="cc">${esc(c.country)}</span></span>
      ${w != null ? `<span class="w">${w}</span>` : ""}</button>`;
};

// ---------------------------------------------------------- navigation
// Back used to always jump to the intro, so opening a genre from a city threw
// away the city you came from. Views are recorded on a stack instead, and back
// retraces the path you actually took.
const history = [];

function pushCurrent() {
  const v = state.view ?? { type: "intro" };
  const last = history[history.length - 1];
  const same = last && last.type === v.type && last.id === v.id && last.key === v.key;
  if (!same) history.push(v);
}

function renderView(v) {
  if (!v || v.type === "intro") return showIntro();
  if (v.type === "search") { $("#search").value = v.q; return search(v.q); }
  if (v.type === "city") return showCity(state.cities[v.id], false);
  if (v.type === "genre") return showGenre(v.id, false);
  if (v.type === "country") return showCountry(v.key, false);
  return showIntro();
}

function goBack() {
  renderView(history.pop() ?? { type: "intro" });
}

function showCity(c, push = true) {
  if (push) pushCurrent();
  state.view = { type: "city", id: c.id };
  state.genre = null;
  clearOverlays();
  repaint();
  flyTo(c.lat, c.lon);

  body.innerHTML = `
    ${history.length ? `<button class="back" data-back>&larr; back</button>` : ""}
    <div class="place">${esc(c.city)}</div>
    <div class="meta">${[c.region, c.country].filter(Boolean).map(esc).join(" · ")}${
      c.pop ? ` · pop ${c.pop.toLocaleString()}` : ""}</div>
    <div class="eyebrow">Listens to · ${c.g.length} genres</div>
    ${c.g.map(([i, w]) => chip(i, w)).join("")}`;
  syncPlaying();
}

/**
 * Best available origin, with its provenance.
 *
 * Declared, not a const arrow: showGenre() calls it and hoisting keeps the
 * ordering from mattering.
 *
 * Order is deliberate. Wikipedia is human-edited and cited, is often finer than
 * city level, carries a date, and does not inherit Spotify's market bias.
 * MusicBrainz is a statistical inference over whoever happens to be catalogued.
 * The genre's own name is a last resort. Each result says which it was, so a
 * reader can weigh "Durban, Wikipedia" against "London, inferred from 61
 * artists" rather than seeing them presented identically.
 */
function pickOrigin(g, named) {
  const w = g.wiki;
  if (w && w.level === "city" && w.lat != null) {
    return {
      lat: w.lat, lon: w.lon,
      label: [w.place, ccName(w.country)].filter(Boolean).join(", "),
      tag: "wikipedia", date: w.date, why: w.raw,
    };
  }
  if (w && w.level === "country" && countryPoint[w.country]) {
    const p = countryPoint[w.country];
    return {
      lat: p.lat, lon: p.lon, label: ccName(w.country) || w.country,
      tag: "wikipedia · country", date: w.date, why: w.raw,
    };
  }
  if (g.mb) {
    return {
      lat: g.mb.lat, lon: g.mb.lon,
      label: `${g.mb.place}, ${g.mb.country ?? g.mb.cc}`,
      tag: g.mb.confidence,
      why: `${g.mb.artists} of ${g.mb.pool.toLocaleString()} tagged artists began here`
         + ` · ${Math.round(g.mb.share * 100)}% of those placed · MusicBrainz`,
    };
  }
  if (named) {
    return {
      lat: named.lat, lon: named.lon, label: `${named.city}, ${named.country}`,
      tag: "inferred", why: "a city named in the genre’s own name",
    };
  }
  // Last resort, and the one that covers most of the tail: a nationality in
  // the name. No encyclopedia will ever hold an article for "dutch cabaret",
  // so the name is the only evidence that exists.
  if (g.nat && countryPoint[g.nat]) {
    const p = countryPoint[g.nat];
    return {
      lat: p.lat, lon: p.lon, label: ccName(g.nat) || g.nat,
      tag: "inferred · country", why: "a nationality in the genre’s own name",
    };
  }
  return null;
}

/** Country name from any city we already know in that country. */
function ccName(code) {
  if (!code) return null;
  const c = state.cities.find((x) => x.cc === code);
  return c ? c.country : code;
}

function showGenre(i, push = true) {
  if (push) pushCurrent();
  const g = genres[i];
  state.view = { type: "genre", id: i };
  state.genre = i;
  repaint();

  // Origin sources, strongest first. Wikipedia outranks MusicBrainz because it
  // is edited and cited rather than inferred from who happens to be catalogued.
  const named = g.origin != null ? state.cities[g.origin] : null;
  const origin = pickOrigin(g, named);

  // Influence arcs: origin -> every city that picked the sound up.
  if (origin) {
    globe.ringsData([{ lat: origin.lat, lon: origin.lon, color: g.color }]);
    globe.arcsData(
      g.top
        .map(([ci]) => state.cities[ci])
        .filter((c) => c && Math.hypot(c.lat - origin.lat, c.lon - origin.lon) > 0.6)
        .map((c) => ({ sLat: origin.lat, sLon: origin.lon, eLat: c.lat, eLon: c.lon, color: g.color }))
    );
  } else {
    clearOverlays();
  }
  // A genre with no cities has a centroid of 0,0, which is open ocean off
  // west Africa. Only move the camera when there is somewhere real to go.
  if (origin) flyTo(origin.lat, origin.lon, 2.15);
  else if (g.n > 0) flyTo(g.lat, g.lon, 2.15);

  body.innerHTML = `
    <button class="back" data-back>&larr; back</button>
    <div class="place" style="color:${g.color}">${esc(g.name)}</div>
    <div class="meta">${g.example ? esc(g.example) : "&nbsp;"}</div>
    <button class="play" data-play="${esc(g.slug)}" data-genre="${i}">▶ Play a clip</button>
    ${origin ? `<div class="origin">
        <div class="eyebrow" style="margin:0">Origin<span class="badge">${esc(origin.tag)}</span></div>
        <div class="origin-p" style="color:${g.color}">${esc(origin.label)}</div>
        ${origin.date ? `<div class="origin-d">${esc(origin.date)}</div>` : ""}
        <div class="origin-w">${esc(origin.why)}</div></div>` : ""}
    <div class="stat"><span>Listened to in</span><span>${
      g.n ? `${g.n.toLocaleString()} ${g.n === 1 ? "city" : "cities"}` : "no city data"}</span></div>
    ${g.n ? `<div class="stat"><span>Centre of gravity</span><span>${g.lat.toFixed(1)}, ${g.lon.toFixed(1)}</span></div>` : ""}
    <p class="hint" style="margin:15px 0 0">${
      origin
        ? "The ring marks where this sound <b>began</b>. The arcs trace it out to everywhere it is <b>listened to</b> now."
        : "Lit points are where this genre is <b>listened to</b>. No single origin dominates its artists, so none is claimed."
    }</p>
    ${g.top.length ? `<div class="eyebrow">Strongest in</div>` : ""}
    ${g.top.map(([id, w]) => cityChip(id, w, g.color)).join("")}`;
  syncPlaying();
}

function onCountry(f) {
  const c = countryOf(f);
  if (c) showCountry(norm(c.name));
}

function showCountry(key, push = true) {
  const c = state.countries[key];
  if (!c) return;
  if (push) pushCurrent();
  state.view = { type: "country", key };
  state.genre = null;
  clearOverlays();
  repaint();

  body.innerHTML = `
    <button class="back" data-back>&larr; back</button>
    <div class="place">${esc(c.name)}</div>
    <div class="meta">Ranked by how strongly the country leans to each sound</div>
    <div class="eyebrow">Signature genres</div>
    ${/* Already ranked strongest-first, and the leading band is genuinely
          tied (Brazil's top 29 all sit at max), so a column of repeated
          identical numbers would be noise. The order carries the ranking. */
      c.g.map(([i]) => chip(i)).join("")}`;
  syncPlaying();
}

function showIntro() {
  // The intro is home, so it is the bottom of the stack, not a step in it.
  history.length = 0;
  $("#search").value = "";
  state.view = null; state.genre = null;
  clearOverlays();
  repaint();
  ctl.autoRotate = true;

  body.innerHTML = `<p class="hint">
      Every point is a city, coloured by the sound its listeners lean toward most.<br><br>
      <b>Click a city</b> to see what it listens to. <b>Click a genre</b> to hear it and watch
      arcs trace from where it began to everywhere it travelled. <b>Click a country</b> for its
      signature sounds.<br><br>
      <kbd>/</kbd> search &nbsp; <kbd>space</kbd> play or stop &nbsp; <kbd>esc</kbd> back
    </p>
    <div class="eyebrow">Try one</div>
    ${pickFeatured().map((i) => chip(i)).join("")}`;
  syncPlaying();
}

/** A few genres with a located origin make the best first click. */
function pickFeatured() {
  const withOrigin = live.filter((i) => genres[i].mb && genres[i].n > 1);
  const pool = withOrigin.length >= 6 ? withOrigin : live;
  const out = new Set();
  while (out.size < Math.min(6, pool.length)) {
    out.add(pool[Math.floor(Math.random() * pool.length)]);
  }
  return [...out];
}

// ---------------------------------------------------------------- search

function rank(q, name) {
  const n = norm(name);
  if (n === q) return 0;
  if (n.startsWith(q)) return 1;
  if (new RegExp(`\\b${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(n)) return 2;
  return n.includes(q) ? 3 : -1;
}

function search(raw) {
  const q = norm(raw);
  if (!q) return showIntro();

  const cs = state.cities
    .map((c) => ({ c, r: rank(q, c.city) }))
    .filter((x) => x.r >= 0)
    .sort((a, b) => a.r - b.r || b.c.g.length - a.c.g.length)
    .slice(0, 7);

  const gs = allGenres
    .map((i) => ({ i, r: rank(q, genres[i].name) }))
    .filter((x) => x.r >= 0)
    // Match quality first, then genres with city data, so a well-known genre
    // still outranks an obscure one that merely shares a prefix.
    .sort((a, b) => a.r - b.r || genres[b.i].n - genres[a.i].n)
    .slice(0, 14);

  body.innerHTML =
    (cs.length ? `<div class="eyebrow">Cities</div>` : "") +
    cs.map(({ c }) => cityChip(c.id, null)).join("") +
    (gs.length ? `<div class="eyebrow">Genres</div>` : "") +
    gs.map(({ i }) => chip(i, genres[i].n || null)).join("") +
    (!cs.length && !gs.length ? `<p class="hint">Nothing matches “${esc(raw)}”.</p>` : "");

  // Recorded but never pushed: this runs on every keystroke, so stacking it
  // would fill the history with half-typed queries. Opening a result pushes
  // whatever the view was at that moment, which lands the search here.
  state.view = { type: "search", q: raw };
  syncPlaying();
}

$("#search").addEventListener("input", (e) => search(e.target.value));

// ---------------------------------------------------------------- events

body.addEventListener("click", (e) => {
  const play = e.target.closest("[data-play]");
  if (play) return void playGenre(+play.dataset.genre);

  const g = e.target.closest("[data-genre]");
  if (g) { const i = +g.dataset.genre; playGenre(i); return showGenre(i); }

  const c = e.target.closest("[data-city]");
  if (c) return showCity(state.cities[+c.dataset.city]);

  if (e.target.closest("[data-back]")) return goBack();
});

document.addEventListener("keydown", (e) => {
  const typing = e.target.tagName === "INPUT";
  if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); }
  else if (e.key === "Escape") {
    if (typing) { e.target.value = ""; e.target.blur(); showIntro(); }
    else goBack();
  } else if (e.key === " " && !typing) {
    e.preventDefault();
    if (player.slug) player.stop();
    else if (state.view?.type === "genre") playGenre(state.view.id);
  }
});

syncPoints(true);
showIntro();
$("#loading").remove();
