"""Genre origins from Wikipedia infoboxes -- the most precise source we have.

Music genre articles carry a `cultural_origins` field that is human-curated and
usually names a place and a decade: "Mid-1980s, Detroit, Michigan, United
States" or "Late 1950s, South Zone of Rio de Janeiro, Brazil". That beats every
other source available here:

  * finer than city level in places ("South Zone of Rio de Janeiro")
  * carries a DATE, which nothing else does
  * records genuine disagreement ("disputed in Gauteng") instead of guessing
  * independent of Spotify's market footprint, so it covers African and Asian
    genres that the Every Noise city data barely sees

Coverage is the limit, not precision. Measured over the live genre set, ~41% of
genres have an article at all and ~22-28% have the field. Every Noise's
taxonomy is mostly hyper-specific inventions ("deep australian indie") that no
encyclopedia documents. The quarter it does cover is the notable quarter.

Deliberately LOWER PRIORITY than the city crawl and the MusicBrainz pass: this
process drops its own scheduling priority and paces itself, so it never
competes with them.

Wikipedia text is CC BY-SA. Only short factual field values are extracted, and
the page title is recorded so the UI can attribute each claim.

Output: data/out/wikipedia_origins.jsonl
"""

import importlib.util
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import certifi
except ImportError:
    print("certifi is required: python -m pip install certifi", file=sys.stderr)
    raise

# Windows consoles are cp1252 and cannot encode many place names.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
DEST = OUT / "wikipedia_origins.jsonl"

API = "https://en.wikipedia.org/w/api.php"
UA = "SoundMap/0.1 (https://github.com/LoganBilello/soundmap)"
BATCH = 50          # the API maximum for multi-page queries
DELAY = 1.2
TIMEOUT = 45

# Python's OpenSSL here trusts a stale bundle that rejects Wikipedia's chain
# while accepting MusicBrainz and archive.org. certifi ships a current one.
CTX = ssl.create_default_context(cafile=certifi.where())

CULTURAL = re.compile(
    r"\|\s*cultural_origins\s*=\s*(.{0,260}?)(?=\n\s*\|\s*\w+\s*=|\n\}\})", re.S | re.I
)
# A capture that still contains "<word> =" ran past the end of the field into
# the next one, which produced nonsense like an instruments list being read as
# a place. Reject those outright rather than trying to salvage them.
SPILLED = re.compile(r"\b\w+\s*=")

# Regions, continents and other non-city geography. Without this, "Latin
# America and Iberian Peninsula" resolves to a village called América in
# Argentina, and a state name matches a same-named hamlet.
NOT_A_CITY = {
    "america", "latin america", "north america", "south america", "central america",
    "europe", "eastern europe", "western europe", "asia", "africa", "west africa",
    "east africa", "north africa", "southern africa", "oceania", "caribbean",
    "the caribbean", "middle east", "scandinavia", "balkans", "iberian peninsula",
    "united states", "u s", "us", "uk", "united kingdom", "great britain",
    "california", "southern california", "northern california", "texas", "florida",
    "new england", "midwest", "west coast", "east coast", "south", "north", "west",
    "east", "worldwide", "global", "various", "united states of america",
}

# GeoNames alternate names include IATA airport codes, so a stray three-letter
# fragment like "Mid" matches Merida. Require a longer, more distinctive token.
MIN_NAME_LEN = 4
# Genre origins are notable places. A hamlet match is almost always spurious.
MIN_POP = 15000
# "Mid-1980s", "Late 1950s", "Early 2000s", "1983", "1870s"
DATE = re.compile(r"\b((?:early|mid|late)[\s-]*)?((?:1[6-9]|20)\d{2})s?\b", re.I)
IS_MUSIC_INFOBOX = re.compile(r"\{\{\s*Infobox\s+music\s+genre", re.I)


def lower_priority():
    """Yield to the city and MusicBrainz crawls rather than compete with them."""
    try:
        import ctypes
        BELOW_NORMAL = 0x00004000
        h = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.kernel32.SetPriorityClass(h, BELOW_NORMAL)
        return True
    except Exception:
        try:
            import os
            os.nice(10)          # POSIX fallback
            return True
        except Exception:
            return False


def strip_markup(s: str) -> str:
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"\{\{\s*hlist\s*\|?", "", s, flags=re.I)
    s = re.sub(r"\{\{\s*(flatlist|plainlist|ubl|unbulleted list)\s*\|?", "", s, flags=re.I)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[([^\]|]*\|)?([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("|", ", ").replace("*", " ")
    return " ".join(s.split()).strip(" ,;")


def parse_date(text: str) -> str | None:
    m = DATE.search(text)
    if not m:
        return None
    qualifier = (m.group(1) or "").strip().rstrip("-").title()
    return f"{qualifier} {m.group(2)}s".strip() if qualifier else m.group(2)


def place_candidates(text: str) -> list[str]:
    """Comma-separated parts, most specific first, with the date removed."""
    body = DATE.sub("", text)
    parts = [p.strip(" ,;.–-") for p in re.split(r",|/|;| and ", body)]
    return [p for p in parts if 2 < len(p) < 60 and not p.lower().startswith("disputed")]


def main() -> int:
    spec = importlib.util.spec_from_file_location("geo", ROOT / "scripts" / "03_geocode.py")
    geo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geo)

    admin1 = geo.load_admin1()
    index = geo.load_cities(admin1)

    # country name -> ISO code, for resolving the trailing part of the field
    ccmap = {}
    raw = geo.download("countryInfo.txt").decode("utf-8", "replace")
    for line in raw.splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) > 4 and f[0] and f[4]:
            ccmap[geo.norm(f[4])] = f[0]
    # geo.norm() deletes dots outright, so "U.S." arrives as "us" and "U.K." as
    # "uk". Missing those silently cost every "…, Chicago, U.S." entry.
    for extra, code in {"us": "US", "usa": "US", "united states of america": "US",
                        "uk": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
                        "northern ireland": "GB", "great britain": "GB",
                        "british west africa": "GH", "soviet union": "RU",
                        "west germany": "DE", "east germany": "DE"}.items():
        ccmap[extra] = code

    # Name-only lookup, best-populated wins. Needed because plenty of entries
    # name a city and no country at all ("Early 2000s, London").
    anyname = {}
    for (name, _cc), recs in index.items():
        best = max(recs, key=lambda r: r["pop"])
        cur = anyname.get(name)
        if cur is None or best["pop"] > cur["pop"]:
            anyname[name] = best

    genres = genre_list()
    done = done_slugs()
    todo = [g for g in genres if g["slug"] not in done]

    nice = lower_priority()
    print(f"{len(genres):,} genres | {len(done):,} done | {len(todo):,} to look up")
    print(f"priority lowered: {nice} | {(len(todo) + BATCH - 1)//BATCH} requests\n", flush=True)

    found = placed = 0
    with DEST.open("a", encoding="utf-8") as out:
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            pages = fetch(chunk)
            if pages is None:
                print("  giving up on this batch", flush=True)
                pages = {}

            for g in chunk:
                text = pages.get(g["name"])
                rec = {"slug": g["slug"], "name": g["name"], "title": None,
                       "raw": None, "date": None, "place": None}

                if text and IS_MUSIC_INFOBOX.search(text):
                    rec["title"] = g["name"]
                    m = CULTURAL.search(text)
                    if m:
                        field = strip_markup(m.group(1))
                        if field and not SPILLED.search(field):
                            found += 1
                            rec["raw"] = field
                            rec["date"] = parse_date(field)
                            hit = locate(place_candidates(field), ccmap, geo, index, anyname)
                            if hit:
                                if hit["level"] == "city":
                                    placed += 1
                                rec.update(hit)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

            n = min(i + BATCH, len(todo))
            print(f"  [{n:>5,}/{len(todo):,} {100*n/len(todo):5.1f}%]  "
                  f"with origins {found}  located {placed}", flush=True)
            time.sleep(DELAY)

    print(f"\ndone. {found:,} genres carry a cultural origin, {placed:,} geocoded.")
    print(f"-> {DEST.relative_to(ROOT)}")
    return 0


def locate(candidates, ccmap, geo, index, anyname) -> dict | None:
    """Resolve the most specific candidate GeoNames recognises.

    Degrades rather than discarding: a city is best, a country alone is still
    worth recording, and "Early 2000s, London" names no country at all so the
    city has to be found without one.
    """
    country = None
    for c in reversed(candidates):
        code = ccmap.get(geo.norm(c))
        if code:
            country = code
            break

    for cand in candidates:
        if geo.norm(cand) in NOT_A_CITY:
            continue
        words = cand.split()
        # Drop leading words so "South Zone of Rio de Janeiro" still finds
        # "Rio de Janeiro".
        for start in range(len(words)):
            name = " ".join(words[start:])
            key = geo.norm(name)
            if len(name) < MIN_NAME_LEN or key in NOT_A_CITY or ccmap.get(key):
                continue                      # too short, a region, or the country
            hit = geo.match(name, name, country, index) if country else anyname.get(key)
            if hit and hit["pop"] >= MIN_POP:
                return {"level": "city", "place": hit["name"],
                        "country": hit["country"], "region": hit["region"],
                        "lat": round(hit["lat"], 4), "lon": round(hit["lon"], 4)}

    # No city resolved, but the country is still a real answer worth keeping.
    # No coordinates: the frontend already has country label points, and
    # inventing a centroid would imply precision we do not have.
    return {"level": "country", "country": country} if country else None


def fetch(chunk) -> dict | None:
    params = urllib.parse.urlencode({
        "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
        "format": "json", "redirects": 1, "titles": "|".join(g["name"] for g in chunk),
    })
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{API}?{params}", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                d = json.load(r)
            q = d.get("query", {})
            # Map the returned titles back to what we asked for.
            back = {}
            for x in q.get("normalized", []):
                back[x["to"]] = x["from"]
            for x in q.get("redirects", []):
                back[x["to"]] = back.get(x["from"], x["from"])

            out = {}
            for p in q.get("pages", {}).values():
                asked = back.get(p["title"], p["title"])
                if "missing" in p:
                    out[asked] = None
                    continue
                try:
                    out[asked] = p["revisions"][0]["slots"]["main"]["*"]
                except Exception:
                    out[asked] = None
            return out
        except Exception as e:
            wait = (2 ** attempt) * 2 + 1
            print(f"    {type(e).__name__}; waiting {wait}s", flush=True)
            time.sleep(wait)
    return None


def genre_list() -> list[dict]:
    """Most-listened genres first, matching 06 so the useful ones land early."""
    seen, counts = {}, {}
    jl = OUT / "city_genres.jsonl"
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                for g in json.loads(line)["genres"]:
                    seen.setdefault(g["slug"], g["name"])
                    counts[g["slug"]] = counts.get(g["slug"], 0) + 1
            except Exception:
                continue

    p = OUT / "genres.json"
    if p.exists():
        for s, v in json.loads(p.read_text(encoding="utf-8")).items():
            seen.setdefault(s, v["name"])

    return [{"slug": s, "name": n}
            for s, n in sorted(seen.items(), key=lambda kv: (-counts.get(kv[0], 0), kv[0]))]


def done_slugs() -> set[str]:
    if not DEST.exists():
        return set()
    out = set()
    for line in DEST.read_text(encoding="utf-8").splitlines():
        try:
            out.add(json.loads(line)["slug"])
        except Exception:
            continue
    return out


if __name__ == "__main__":
    sys.exit(main())
