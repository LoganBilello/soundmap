/**
 * Genre preview playback.
 *
 * Every Noise's own clips are Spotify preview URLs harvested before Spotify
 * closed preview access to new API apps. They still resolve today, but they are
 * not guaranteed to, so every failure falls back to the iTunes Search API,
 * which is public, documented and sends `Access-Control-Allow-Origin: *`.
 *
 * Resolution order for a genre:
 *   1. its Spotify preview_url          (needs the saved engenremap.html)
 *   2. iTunes, searched by example artist + track  (same source)
 *   3. iTunes, searched by the genre name itself   (always available)
 */

const ITUNES = "https://itunes.apple.com/search";
const resolved = new Map();      // slug -> url | null

/** 'e.g. Kenny Larkin "Tedra"' -> 'Kenny Larkin Tedra' */
function exampleToQuery(example) {
  if (!example) return null;
  const m = example.replace(/^e\.g\.\s*/i, "").match(/^(.*?)\s*"(.*)"\s*$/);
  return m ? `${m[1]} ${m[2]}`.trim() : example.replace(/^e\.g\.\s*/i, "").trim();
}

async function itunes(term) {
  const url = `${ITUNES}?term=${encodeURIComponent(term)}&entity=song&limit=1`;
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    const j = await r.json();
    return j.results?.[0]?.previewUrl ?? null;
  } catch {
    return null;
  }
}

/** Does this URL actually return audio? Spotify 404s are silent otherwise. */
function probe(url) {
  return new Promise((done) => {
    const a = new Audio();
    a.preload = "metadata";
    const ok = () => { cleanup(); done(true); };
    const no = () => { cleanup(); done(false); };
    const cleanup = () => {
      a.removeEventListener("loadedmetadata", ok);
      a.removeEventListener("error", no);
      a.src = "";
    };
    a.addEventListener("loadedmetadata", ok, { once: true });
    a.addEventListener("error", no, { once: true });
    a.src = url;
    setTimeout(no, 6000);
  });
}

async function resolve(genre, slug) {
  if (resolved.has(slug)) return resolved.get(slug);

  let url = null;
  if (genre.preview && (await probe(genre.preview))) {
    url = genre.preview;
  } else {
    const q = exampleToQuery(genre.example);
    if (q) url = await itunes(q);
    if (!url) url = await itunes(genre.name);
  }
  resolved.set(slug, url);
  return url;
}

export class Player {
  /** @param {(state:{slug:string|null, genre?:object, source?:string, error?:string})=>void} onChange */
  constructor(onChange) {
    this.onChange = onChange;
    this.audio = new Audio();
    this.audio.volume = 0.85;
    this.slug = null;
    this.audio.addEventListener("ended", () => this.stop());
  }

  async toggle(slug, genre) {
    if (this.slug === slug) { this.stop(); return; }
    await this.play(slug, genre);
  }

  async play(slug, genre) {
    this.slug = slug;
    this.onChange({ slug, genre, source: "loading" });

    const url = await resolve(genre, slug);
    if (this.slug !== slug) return;              // superseded while resolving

    if (!url) {
      this.slug = null;
      this.onChange({ slug: null, error: `No preview found for "${genre.name}"` });
      return;
    }

    this.audio.src = url;
    try {
      await this.audio.play();
      this.onChange({
        slug, genre,
        source: url === genre.preview ? "Spotify preview" : "iTunes preview",
      });
    } catch {
      this.slug = null;
      this.onChange({ slug: null, error: "Playback blocked — click again" });
    }
  }

  stop() {
    this.audio.pause();
    this.audio.currentTime = 0;
    this.slug = null;
    this.onChange({ slug: null });
  }
}
