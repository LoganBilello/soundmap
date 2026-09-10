"""Fetch each archived city page from the Wayback Machine and keep only its genres.

Snapshots are ~1.7 MB each; 5,512 of them would be ~9 GB of HTML we don't need.
So each page is parsed as it arrives and only the extracted genre list is kept,
appended to a JSONL file. The run is resumable: rows already in the JSONL are
skipped, so it can be stopped and restarted freely.

Politeness: one request at a time, ~1 req/s, exponential backoff on 429/5xx.
The origin server (everynoise.com) is never contacted.

Output: data/out/city_genres.jsonl
"""

import gzip
import json
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.request

# Windows consoles default to cp1252, which cannot encode names like "Łódź".
# Without this, printing a progress line raises UnicodeEncodeError and kills a
# multi-hour crawl over nothing but a status message.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
SNAPSHOTS = OUT / "city_snapshots.json"
DEST = OUT / "city_genres.jsonl"

UA = "SoundMap/0.1 (personal research project; archival retrieval)"
DELAY = 1.0          # seconds between requests
MAX_RETRIES = 4
TIMEOUT = 90

# The rooted city's own genre block follows this label. City table rows above it
# contain only `?root=` links, so this marker cleanly separates the two.
MARKER = "these people particularly like"
GENRE_RE = re.compile(
    r'font-size:\s*([\d.]+)%[^>]*>\s*<a href="engenremap-([a-z0-9]+)\.html"'
    r'[^>]*style="color:\s*(#[0-9A-Fa-f]{6})"[^>]*>([^<]+)</a>'
)


def parse_genres(html: str) -> list[dict]:
    i = html.find(MARKER)
    if i == -1:
        return []
    out, seen = [], set()
    for weight, slug, color, name in GENRE_RE.findall(html[i:]):
        if slug in seen:
            continue
        seen.add(slug)
        out.append(
            {"slug": slug, "name": name.strip(), "color": color, "weight": float(weight)}
        )
    return out


def fetch(url: str) -> str | None:
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return None                       # genuinely absent; don't retry
            wait = (2 ** attempt) * 3 + random.uniform(0, 2)
            print(f"    HTTP {e.code}, backing off {wait:.0f}s", flush=True)
            time.sleep(wait)
        except Exception as e:                     # timeouts, connection resets
            wait = (2 ** attempt) * 3 + random.uniform(0, 2)
            print(f"    {type(e).__name__}, retry in {wait:.0f}s", flush=True)
            time.sleep(wait)
    return None


def already_done() -> set[str]:
    if not DEST.exists():
        return set()
    done = set()
    with DEST.open(encoding="utf-8") as f:
        for line in f:
            try:
                done.add(json.loads(line)["root"])
            except Exception:
                continue
    return done


def main() -> int:
    if not SNAPSHOTS.exists():
        print("run 01_index_cities.py first", file=sys.stderr)
        return 1

    cities = json.loads(SNAPSHOTS.read_text(encoding="utf-8"))
    done = already_done()
    todo = [c for c in cities if c["root"] not in done]

    print(f"{len(cities):,} cities indexed | {len(done):,} already fetched | {len(todo):,} to go")
    if todo:
        print(f"ETA ~{len(todo) * (DELAY + 0.6) / 60:.0f} min\n", flush=True)

    empty = 0
    with DEST.open("a", encoding="utf-8") as out:
        for n, c in enumerate(todo, 1):
            html = fetch(c["wayback"])
            genres = parse_genres(html) if html else []
            if not genres:
                empty += 1

            out.write(
                json.dumps(
                    {
                        "root": c["root"],
                        "city": c["city"],
                        "country": c["country"],
                        "timestamp": c["timestamp"],
                        "genres": genres,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            out.flush()

            if n % 25 == 0 or n == len(todo):
                pct = 100 * n / len(todo)
                print(
                    f"  [{n:>5,}/{len(todo):,}  {pct:5.1f}%]  {c['root'][:38]:<38} "
                    f"{len(genres):>3} genres  (empty so far: {empty})",
                    flush=True,
                )
            time.sleep(DELAY)

    print(f"\ndone. {len(todo) - empty:,} cities with genres, {empty:,} empty.")
    print(f"-> {DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
