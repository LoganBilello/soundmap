"""Derive where each genre CAME FROM, using MusicBrainz artist geography.

The Wayback crawl says where a genre is *listened to*. That is an affinity
signal, not an origin: Detroit techno is played everywhere. MusicBrainz records
each artist's `begin-area`, the place they started, usually at city level. The
modal begin-area across a genre's artists is a far better origin estimate.

MusicBrainz core data is CC0 and the API permits automated use at roughly one
request per second with an identifying User-Agent. That is what this does. The
run is resumable and appends one line per genre.

Output: data/out/genre_origins.jsonl
"""

import collections
import importlib.util
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Windows consoles default to cp1252 and cannot encode accented genre names.
# A progress line must never be able to kill a multi-hour crawl.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
CACHE = ROOT / "data" / "cache"
DEST = OUT / "genre_origins.jsonl"

# MusicBrainz asks for a contactable User-Agent. Anonymous clients are limited
# to ~1 request/second; exceeding it returns 503 rather than 429.
#
# The address comes from the environment so it never enters git history:
#     PowerShell   $env:SOUNDMAP_CONTACT = "you@example.com"
#     bash         export SOUNDMAP_CONTACT="you@example.com"
CONTACT = os.environ.get("SOUNDMAP_CONTACT", "").strip()
UA = (
    f"SoundMap/0.1 (+https://github.com/soundmap; {CONTACT})"
    if CONTACT
    else "SoundMap/0.1 (+https://github.com/soundmap)"
)
API = "https://musicbrainz.org/ws/2/artist/"
DELAY = 1.1
MAX_RETRIES = 6
# MusicBrainz's shared search cluster stalls at random, for ~30s, independently
# of how much data you ask for: the same query can take 0.5s or 33s. Waiting out
# every stall averages ~10s per genre. A short timeout plus an immediate retry
# usually lands on a healthy node instead, so treat a stall as "try again now"
# rather than as a rate limit.
TIMEOUT = 9
STALL_WAIT = 0.8
# Stalls are query-specific, not node-specific: a slow search term re-runs just
# as slowly on retry (`hyperpop` cost 34s across three attempts before
# answering). So allow one retry, not six, and move on rather than paying the
# same expensive search repeatedly.
MAX_STALLS = 2
# 100 is the API maximum and costs no extra requests. A larger sample is what
# separates a real origin from noise: `detroit techno` has only ~47 tagged
# artists in total, so 100 covers all of them, while `pop` has thousands and
# will correctly scatter instead of handing a plurality to one city.
LIMIT = 100

# begin-area types that represent a real place of origin.
PLACE_TYPES = {"City", "Town", "Municipality", "Village", "District", "Borough"}


def load_geonames():
    """Reuse the GeoNames index from 03 (its filename cannot be imported)."""
    spec = importlib.util.spec_from_file_location(
        "geo", ROOT / "scripts" / "03_geocode.py"
    )
    geo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geo)
    return geo, geo.load_cities(geo.load_admin1())


def load_country_codes(geo) -> dict[str, str]:
    """'United States' -> 'US'"""
    raw = geo.download("countryInfo.txt").decode("utf-8", "replace")
    out = {}
    for line in raw.splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) > 4 and f[0] and f[4]:
            out[geo.norm(f[4])] = f[0]
    return out


def genre_list() -> list[dict]:
    """Every genre, most-listened-to first.

    This run takes hours, so order matters: a genre appearing in 300 cities is
    one a visitor will actually click, while one appearing in none may never be
    seen. Sorting by city count means the map becomes useful long before the
    crawl finishes, and stopping early costs only the obscure tail.
    """
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

    return [
        {"slug": s, "name": n}
        for s, n in sorted(seen.items(), key=lambda kv: (-counts.get(kv[0], 0), kv[0]))
    ]


def query(name: str) -> tuple[int, list[dict]] | None:
    """Return (total artists carrying this tag, the sampled artists)."""
    q = urllib.parse.quote(f'tag:"{name}"')
    url = f"{API}?query={q}&fmt=json&limit={LIMIT}"
    stalls = 0
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
                return int(d.get("count") or 0), d.get("artists", [])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0, []
            # 503 here means the rate limiter, so genuinely back off.
            wait = (2 ** attempt) * 2 + 1
            print(f"    HTTP {e.code}; waiting {wait}s", flush=True)
            time.sleep(wait)
        except Exception as e:
            reason = getattr(e, "reason", None)
            stalled = isinstance(e, TimeoutError) or isinstance(reason, TimeoutError)
            if stalled:
                stalls += 1
                if stalls >= MAX_STALLS:
                    return None          # genuinely slow term; skip it
                time.sleep(STALL_WAIT)
            else:
                print(f"    {type(e).__name__}; retrying", flush=True)
                time.sleep((2 ** attempt) * 2 + 1)
    return None


def origins_from(artists: list[dict], ccmap, geo, index) -> list[dict]:
    """Rank the places a genre's artists started out in."""
    tally: dict[tuple, dict] = {}
    for a in artists:
        area = a.get("begin-area") or {}
        if area.get("type") not in PLACE_TYPES:
            continue
        name = (area.get("name") or "").strip()
        if not name:
            continue
        country_name = ((a.get("area") or {}).get("name") or "").strip()
        cc = ccmap.get(geo.norm(country_name), "")
        key = (geo.norm(name), cc)
        rec = tally.setdefault(
            key, {"area": name, "country": cc, "n": 0, "score": 0}
        )
        rec["n"] += 1
        rec["score"] += int(a.get("score") or 0)

    ranked = sorted(tally.values(), key=lambda r: (-r["n"], -r["score"]))[:5]
    for r in ranked:
        hit = geo.match(r["area"], r["area"], r["country"], index) if r["country"] else None
        if hit:
            r["lat"] = round(hit["lat"], 4)
            r["lon"] = round(hit["lon"], 4)
    return ranked


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


def main() -> int:
    genres = genre_list()
    if not genres:
        print("no genres yet; run 02 or 04 first", file=sys.stderr)
        return 1

    geo, index = load_geonames()
    ccmap = load_country_codes(geo)

    done = done_slugs()
    todo = [g for g in genres if g["slug"] not in done]
    print(f"{len(genres):,} genres | {len(done):,} done | {len(todo):,} to query")
    print(f"ETA ~{len(todo) * (DELAY + 0.5) / 60:.0f} min\n", flush=True)

    found = 0
    with DEST.open("a", encoding="utf-8") as out:
        for n, g in enumerate(todo, 1):
            res = query(g["name"])
            total, artists = res if res else (0, [])
            origins = origins_from(artists, ccmap, geo, index) if artists else []
            if origins:
                found += 1

            out.write(
                json.dumps(
                    {
                        "slug": g["slug"],
                        "name": g["name"],
                        # `total` is every artist carrying the tag; `sampled` is
                        # how many we actually looked at. When total >> sampled
                        # the sample cannot support an origin claim.
                        "total": total,
                        "sampled": len(artists),
                        "origins": origins,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out.flush()

            if n % 20 == 0 or n == len(todo):
                top = origins[0]["area"] if origins else "-"
                print(
                    f"  [{n:>5,}/{len(todo):,} {100*n/len(todo):5.1f}%] "
                    f"{g['name'][:30]:<30} -> {top[:20]:<20} (hits {found})",
                    flush=True,
                )
            time.sleep(DELAY)

    print(f"\ndone. {found:,} genres with a located origin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
