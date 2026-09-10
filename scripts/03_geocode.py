"""Attach latitude/longitude to every archived city, offline.

Every Noise city keys look like "Detroit Michigan US" / "Bristol GB" -- name,
optional admin region, then a country code. GeoNames is matched on name +
country, with the admin region used to break ties (there are ~40 Springfields)
and population as the final tiebreaker.

Sources (downloaded once, cached):
  cities5000.zip        ~55k cities with population >= 5000   (CC BY 4.0)
  admin1CodesASCII.txt  admin1 code -> region name

Output: data/out/city_coords.json
"""

import io
import json
import pathlib
import sys
import unicodedata
import urllib.request
import zipfile

# This prints unmatched city names, which are frequently non-Latin-1.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "out"

GEONAMES = "https://download.geonames.org/export/dump/"
UA = "SoundMap/0.1 (personal research project)"


def norm(s: str) -> str:
    """Casefold and strip accents so 'Ávila' matches 'Avila'."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("-", " ").replace(".", "").split())


def download(name: str) -> bytes:
    dest = CACHE / name
    if dest.exists():
        return dest.read_bytes()
    print(f"downloading {name} ...")
    req = urllib.request.Request(GEONAMES + name, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    dest.write_bytes(data)
    print(f"  cached {len(data):,} bytes")
    return data


def load_admin1() -> dict[str, str]:
    """'US.MI' -> 'Michigan'"""
    raw = download("admin1CodesASCII.txt").decode("utf-8", "replace")
    out = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


def load_cities(admin1: dict[str, str]) -> dict[tuple[str, str], list[dict]]:
    """(normalized name, country code) -> candidate cities."""
    blob = download("cities5000.zip")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read("cities5000.txt").decode("utf-8", "replace")

    index: dict[tuple[str, str], list[dict]] = {}
    rows = 0
    for line in raw.splitlines():
        f = line.split("\t")
        if len(f) < 15:
            continue
        rows += 1
        name, ascii_name, alts = f[1], f[2], f[3]
        lat, lon, cc, a1 = float(f[4]), float(f[5]), f[8], f[10]
        pop = int(f[14] or 0)
        region = admin1.get(f"{cc}.{a1}", "")

        rec = {
            "name": name,
            "lat": lat,
            "lon": lon,
            "country": cc,
            "region": region,
            "pop": pop,
        }
        # Index every alternate name, not a prefix of them. GeoNames lists
        # Mexico City's primary name as "Mexico City" and Gothenburg's as
        # "Gothenburg"; the local forms ("Ciudad de Mexico", "Goteborg") sit
        # deep in the alternates list, so any cap drops real cities.
        names = {name, ascii_name} | set(alts.split(",") if alts else [])
        for n in names:
            n = norm(n)
            if n:
                index.setdefault((n, cc), []).append(rec)
    print(f"  indexed {rows:,} GeoNames cities")
    return index


def match(root: str, city: str, cc: str, index) -> dict | None:
    """Resolve one Every Noise city key against the GeoNames index."""
    tokens = city.split()

    # Try longest-prefix-as-name first: "Broken Arrow Oklahoma" -> try
    # "Broken Arrow Oklahoma", then "Broken Arrow", then "Broken".
    for cut in range(len(tokens), 0, -1):
        cand_name = " ".join(tokens[:cut])
        rest = norm(" ".join(tokens[cut:]))
        hits = index.get((norm(cand_name), cc))
        if not hits:
            continue
        if rest:
            exact = [h for h in hits if norm(h["region"]) == rest]
            if exact:
                return max(exact, key=lambda h: h["pop"])
        return max(hits, key=lambda h: h["pop"])
    return None


def main() -> int:
    snaps = OUT / "city_snapshots.json"
    if not snaps.exists():
        print("run 01_index_cities.py first", file=sys.stderr)
        return 1

    admin1 = load_admin1()
    index = load_cities(admin1)
    cities = json.loads(snaps.read_text(encoding="utf-8"))

    resolved, missed = {}, []
    for c in cities:
        hit = match(c["root"], c["city"], c["country"], index)
        if hit:
            resolved[c["root"]] = {
                "city": c["city"],
                "country": c["country"],
                "lat": round(hit["lat"], 4),
                "lon": round(hit["lon"], 4),
                "region": hit["region"],
                "pop": hit["pop"],
            }
        else:
            missed.append(c["root"])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "city_coords.json").write_text(
        json.dumps(resolved, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    (OUT / "city_coords_missing.txt").write_text(
        "\n".join(missed), encoding="utf-8"
    )

    pct = 100 * len(resolved) / max(1, len(cities))
    print(f"\n  cities in index : {len(cities):,}")
    print(f"  geocoded        : {len(resolved):,}  ({pct:.1f}%)")
    print(f"  unmatched       : {len(missed):,}")
    if missed:
        print("  examples        : " + ", ".join(missed[:5]))
    print(f"  -> {(OUT / 'city_coords.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
