/**
 * Country basemap as a single texture.
 *
 * globe.gl's polygon layer builds one extruded mesh per country: 177 countries
 * became 667 meshes plus 289 line segments, costing ~1,641 draw calls and over
 * a million triangles EVERY FRAME, regardless of zoom. That was the remaining
 * lag, and no amount of dot optimisation could touch it.
 *
 * MapLibre (what radio.garden uses) rasterises its land instead. This does the
 * same: the GeoJSON is drawn once into an equirectangular canvas that becomes
 * the globe's texture — one draw call, no triangles, no per-frame cost.
 *
 * Country picking moves to the CPU, where a point-in-polygon test over 177
 * features costs microseconds and only runs on click.
 */

/** Longitude/latitude to pixel in an equirectangular image. */
const px = (lon, w) => ((lon + 180) / 360) * w;
const py = (lat, h) => ((90 - lat) / 180) * h;

function ringPath(ctx, ring, w, h, lonShift) {
  for (let i = 0; i < ring.length; i++) {
    const x = px(ring[i][0] + lonShift, w);
    const y = py(ring[i][1], h);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.closePath();
}

function polygonsOf(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return [geometry.coordinates];
  if (geometry.type === "MultiPolygon") return geometry.coordinates;
  return [];
}

/**
 * @param {Array} features   GeoJSON country features
 * @param {(f:object)=>boolean} hasData  true if the country has genre data
 */
export function renderBasemap(features, hasData, { width = 4096, height = 2048 } = {}) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");

  ctx.fillStyle = "#050a14";                 // ocean
  ctx.fillRect(0, 0, width, height);

  ctx.lineJoin = "round";
  ctx.lineWidth = Math.max(1, width / 2048);

  // Draw the world three times, offset by a full turn each way. Countries that
  // straddle the antimeridian (Russia, Fiji) would otherwise smear a band right
  // across the map; the off-canvas copies supply their missing halves instead.
  for (const lonShift of [-360, 0, 360]) {
    for (const f of features) {
      const land = hasData(f) ? "#1b273e" : "#141c2d";
      for (const poly of polygonsOf(f.geometry)) {
        ctx.beginPath();
        for (const ring of poly) ringPath(ctx, ring, width, height, lonShift);
        ctx.fillStyle = land;
        ctx.fill("evenodd");                 // evenodd punches out lakes/holes
        ctx.strokeStyle = "rgba(125,158,200,0.34)";
        ctx.stroke();
      }
    }
  }
  return canvas;
}

/** Ray-casting point-in-polygon against one ring. */
function inRing(lon, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** The country containing this coordinate, or null. Outer ring wins, holes exclude. */
export function pickCountry(features, lat, lon) {
  for (const f of features) {
    for (const poly of polygonsOf(f.geometry)) {
      if (!poly.length || !inRing(lon, lat, poly[0])) continue;
      let inHole = false;
      for (let h = 1; h < poly.length; h++) {
        if (inRing(lon, lat, poly[h])) { inHole = true; break; }
      }
      if (!inHole) return f;
    }
  }
  return null;
}
