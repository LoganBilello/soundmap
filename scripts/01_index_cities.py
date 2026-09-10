"""Build the list of archived Every Noise city pages worth fetching.

Reads the Wayback CDX index and picks ONE best snapshot per city. Each city was
archived under many `scope=XX` variants, but `scope` only filters the comparison
list -- the rooted city's own genre list is identical across them. So we collapse
~264k archived URLs down to one request per city.

Output: data/out/city_snapshots.json
"""

import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "out"

CDX = (
    "http://web.archive.org/cdx/search/cdx"
    "?url=everynoise.com/everyplace.cgi*"
    "&output=json&fl=original,timestamp,statuscode&collapse=urlkey"
)

UA = "SoundMap/0.1 (personal research project; archival retrieval)"

# City keys look like "Detroit Michigan US" / "Bristol GB" -- name then a
# 2-letter uppercase country code. Anything without that tail is a partial key.
CITY_RE = re.compile(r"^(.+?)\s+([A-Z]{2})$")


def fetch_cdx() -> list:
    cached = CACHE / "cdx_full.json"
    if cached.exists():
        print(f"using cached CDX ({cached.stat().st_size:,} bytes)")
        return json.loads(cached.read_text(encoding="utf-8"))

    print("fetching CDX index (one request, ~25 MB)...")
    req = urllib.request.Request(CDX, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        rows = json.loads(r.read().decode("utf-8", "replace"))
    cached.write_text(json.dumps(rows), encoding="utf-8")
    print(f"cached {len(rows):,} rows")
    return rows


def smart_unquote(v: str) -> str:
    """Percent-decode to bytes, then guess the charset.

    These URLs were archived over ~15 years and are not consistently encoded:
    'Medellin' appears both as UTF-8 (%C3%AD) and Latin-1 (%ED). Decoding
    everything as UTF-8 turns the Latin-1 ones into U+FFFD, which silently
    corrupts ~18 city names and makes them unmatchable against GeoNames.
    """
    raw = urllib.parse.unquote_to_bytes(v.replace("+", " "))
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def root_of(original: str) -> str | None:
    """Pull the decoded `root=` city key out of an archived URL."""
    # Archived URLs contain both `&` and `&amp;` separators.
    q = urllib.parse.urlparse(original).query.replace("&amp;", "&")
    for part in q.split("&"):
        key, _, val = part.partition("=")
        if key.lower() == "root" and val:
            return re.sub(r"\s+", " ", smart_unquote(val)).strip()
    return None


def main() -> int:
    rows = fetch_cdx()
    header, *data = rows

    best: dict[str, dict] = {}
    skipped_no_cc = 0

    for original, timestamp, status in data:
        if status != "200":
            continue
        root = root_of(original)
        if not root:
            continue
        m = CITY_RE.match(root)
        if not m:
            skipped_no_cc += 1
            continue

        city, cc = m.group(1), m.group(2)
        # Prefer the most RECENT capture. The per-city genre block ("these
        # people particularly like") did not exist on the site until ~2018 --
        # a 2017 capture is a complete, valid page with zero genres in it.
        # `scope` deliberately plays no part here: it filters the comparison
        # list only, never the rooted city's own genres, so preferring
        # scope=all would buy nothing while costing recency.
        mangled = "�" in root
        score = (not mangled, timestamp)

        prev = best.get(root)
        if prev is None or score > prev["_score"]:
            best[root] = {
                "_score": score,
                "city": city,
                "country": cc,
                "root": root,
                "timestamp": timestamp,
                "original": original.replace("&amp;", "&"),
                "wayback": f"https://web.archive.org/web/{timestamp}id_/{original.replace('&amp;', '&')}",
            }

    for v in best.values():
        del v["_score"]

    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "city_snapshots.json"
    dest.write_text(
        json.dumps(sorted(best.values(), key=lambda d: d["root"]), indent=1, ensure_ascii=False),
        encoding="utf-8",
    )

    countries = {v["country"] for v in best.values()}
    print(f"\n  archived URLs scanned : {len(data):,}")
    print(f"  skipped (no country)  : {skipped_no_cc:,}")
    print(f"  distinct cities       : {len(best):,}")
    print(f"  countries represented : {len(countries):,}")
    print(f"  -> {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
