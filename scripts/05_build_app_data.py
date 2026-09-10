"""Join every source into the compact JSON the web app loads.

Degrades gracefully: the Wayback crawl already carries each genre's name and
colour, so the globe renders from crawl data alone. The manually-saved Every
Noise pages add preview audio, example artists and the country rankings.

Genres are emitted as an ARRAY and referenced by integer index everywhere else.
Storing the slug string in each city link cost ~40% of cities.json, because
~99,000 links each repeated an 11-character slug.

Inputs (first two required):
  data/out/city_genres.jsonl     Wayback crawl
  data/out/city_coords.json      GeoNames join
  data/out/genres.json           saved engenremap     (optional: audio)
  data/out/country_genres.json   saved countries.html (optional)
  data/out/genre_origins.jsonl   MusicBrainz          (optional: origins)

Outputs:
  public/data/cities.json  public/data/genres.json
  public/data/countries.json  public/data/meta.json
"""

import json
import math
import pathlib
import re
import sys
import unicodedata
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "out"
APP = ROOT / "public" / "data"

TOP_CITIES = 15          # cities listed under "Strongest in"
TOP_COUNTRY_GENRES = 30


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().replace("-", " ").replace(".", "").split())


def country_names() -> dict[str, str]:
    """ISO code -> full country name, so nothing in the UI reads as 'JM'."""
    p = ROOT / "data" / "cache" / "countryInfo.txt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            "https://download.geonames.org/export/dump/countryInfo.txt",
            headers={"User-Agent": "SoundMap/0.1 (personal research project)"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            p.write_bytes(r.read())

    out = {}
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) > 4 and f[0] and f[4]:
            out[f[0]] = f[4]
    return out


def load_jsonl(p: pathlib.Path) -> list[dict]:
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def spherical_centroid(points) -> tuple[float, float]:
    """Weighted mean direction on a sphere.

    Averaging raw lat/lon breaks across the antimeridian -- a genre split
    between Tokyo and Los Angeles would land in the Sahara.
    """
    x = y = z = 0.0
    for lat, lon, w in points:
        la, lo = math.radians(lat), math.radians(lon)
        x += w * math.cos(la) * math.cos(lo)
        y += w * math.cos(la) * math.sin(lo)
        z += w * math.sin(la)
    if x == y == z == 0:
        return 0.0, 0.0
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def load_origins() -> dict:
    """MusicBrainz begin-areas, filtered to the ones that actually dominate."""
    p = OUT / "genre_origins.jsonl"
    if not p.exists():
        return {}
    out = {}
    for row in load_jsonl(p):
        ors = row["origins"]
        if not ors:
            continue
        top = ors[0]
        runner_up = ors[1]["n"] if len(ors) > 1 else 0
        located = sum(o["n"] for o in ors)
        share = top["n"] / max(1, located)

        # COVERAGE is the signal that matters, not share. MusicBrainz skews
        # heavily UK/US, so for any huge tag London wins the located subset no
        # matter where the sound actually came from: `rock` gives London a 52%
        # share off 100 artists sampled from 43,811 -- 0.2% coverage, which
        # supports nothing. `detroit techno` has 47 tagged artists total and we
        # see all 47, so its 78% Detroit really is the population.
        total = row.get("total") or 0
        sampled = row.get("sampled") or 0
        coverage = (sampled / total) if total else 0.0

        if "lat" not in top or top["n"] < 3 or top["n"] <= runner_up:
            continue
        if coverage < 0.20 or share < 0.30:
            continue

        out[row["slug"]] = {
            "place": top["area"],
            "cc": top["country"],
            "lat": top["lat"],
            "lon": top["lon"],
            "artists": top["n"],
            "share": round(share, 2),
            "coverage": round(coverage, 3),
            "pool": total,
            "confidence": "strong" if (coverage >= 0.5 and share >= 0.5) else "likely",
        }
    return out


# Nationality words that identify a country unambiguously. Genre names carry
# these constantly ("dutch pop", "korean city pop", "swedish drill") and no
# encyclopedia will ever hold an article for them, so the name is the only
# evidence there is. Regional and continental words are deliberately absent:
# "latin", "afro", "nordic", "balkan" and "arabic" name no single country.
DEMONYMS = {
    "afghan": "AF", "albanian": "AL", "algerian": "DZ", "andean": None,
    "argentine": "AR", "argentinian": "AR", "armenian": "AM", "australian": "AU",
    "austrian": "AT", "azerbaijani": "AZ", "bangladeshi": "BD", "belarusian": "BY",
    "belgian": "BE", "bolivian": "BO", "bosnian": "BA", "brazilian": "BR",
    "british": "GB", "bulgarian": "BG", "cambodian": "KH", "cameroonian": "CM",
    "canadian": "CA", "chilean": "CL", "chinese": "CN", "colombian": "CO",
    "congolese": "CD", "costa rican": "CR", "croatian": "HR", "cuban": "CU",
    "cypriot": "CY", "czech": "CZ", "danish": "DK", "dominican": "DO",
    "dutch": "NL", "ecuadorian": "EC", "egyptian": "EG", "english": "GB",
    "estonian": "EE", "ethiopian": "ET", "filipino": "PH", "finnish": "FI",
    "french": "FR", "georgian": "GE", "german": "DE", "ghanaian": "GH",
    "greek": "GR", "guatemalan": "GT", "haitian": "HT", "hawaiian": "US",
    "honduran": "HN", "hungarian": "HU", "icelandic": "IS", "indian": "IN",
    "indonesian": "ID", "iranian": "IR", "iraqi": "IQ", "irish": "IE",
    "israeli": "IL", "italian": "IT", "italo": "IT", "jamaican": "JM",
    "japanese": "JP", "kazakh": "KZ", "kenyan": "KE", "korean": "KR",
    "latvian": "LV", "lebanese": "LB", "lithuanian": "LT", "malaysian": "MY",
    "maltese": "MT", "mexican": "MX", "moldovan": "MD", "mongolian": "MN",
    "moroccan": "MA", "nepali": "NP", "nicaraguan": "NI", "nigerian": "NG",
    "norwegian": "NO", "pakistani": "PK", "palestinian": "PS", "panamanian": "PA",
    "paraguayan": "PY", "peruvian": "PE", "polish": "PL", "portuguese": "PT",
    "puerto rican": "PR", "romanian": "RO", "russian": "RU", "salvadoran": "SV",
    "saudi": "SA", "scottish": "GB", "senegalese": "SN", "serbian": "RS",
    "singaporean": "SG", "slovak": "SK", "slovenian": "SI", "somali": "SO",
    "spanish": "ES", "sudanese": "SD", "swedish": "SE", "swiss": "CH",
    "taiwanese": "TW", "tanzanian": "TZ", "thai": "TH", "tunisian": "TN",
    "turkish": "TR", "ugandan": "UG", "ukrainian": "UA", "uruguayan": "UY",
    "uzbek": "UZ", "venezuelan": "VE", "vietnamese": "VN", "welsh": "GB",
    "zambian": "ZM", "zimbabwean": "ZW",
    # Endonyms that appear in genre names as often as the English forms.
    "suomi": "FI", "nederpop": "NL", "nederhop": "NL", "deutsch": "DE",
    "norsk": "NO", "svensk": "SE", "dansk": "DK", "turkce": "TR",
    "brasileiro": "BR", "brasileira": "BR", "mexicano": "MX", "mexicana": "MX",
    "espanol": "ES", "espanola": "ES", "argentino": "AR", "argentina": "AR",
    "peruano": "PE", "peruana": "PE", "chileno": "CL", "chilena": "CL",
    "colombiano": "CO", "colombiana": "CO", "cubano": "CU", "cubana": "CU",
    "venezolano": "VE", "boliviana": "BO", "portugues": "PT", "tuga": "PT",
}
DEMONYMS = {k: v for k, v in DEMONYMS.items() if v}

# City names that are also ordinary English words, or so short that a chance
# substring match is likelier than a real reference.
CITY_NAME_STOPWORDS = {
    "nice", "bath", "reading", "mobile", "split", "hollywood", "sale", "bury",
    "deal", "march", "wells", "boston", "york", "orange", "phoenix", "eureka",
    "surprise", "hope", "liberty", "union", "industry", "commerce", "avon",
    "kent", "essex", "surrey", "richmond", "victoria", "santa", "san", "santo",
    "saint", "lake", "valley", "springs", "north", "south", "east", "west",
    # Ordinary Spanish and Portuguese words that also open city names. "nuevo"
    # put a Mexican regional genre in Nuevo Laredo; "grande" put a Michoacan
    # harp style in Campo Grande, Brazil.
    "nuevo", "nueva", "grande", "grande", "novo", "nova", "porto", "puerto",
    "ciudad", "cidade", "villa", "vila", "campo", "monte", "playa", "punta",
    "salto", "rio", "sierra", "costa", "isla", "mar", "sol", "cruz", "verde",
    "central", "capital", "territory", "district", "county", "province",
}

# US states and similar first-level regions. A genre named for a state is not
# a claim about its largest city: "kentucky roots" is not Louisville music.
# These resolve to the country instead of inventing a city.
REGION_COUNTRY = {
    "alabama": "US", "alaska": "US", "arizona": "US", "arkansas": "US",
    "california": "US", "colorado": "US", "connecticut": "US", "delaware": "US",
    "florida": "US", "georgia": "US", "hawaii": "US", "idaho": "US",
    "illinois": "US", "indiana": "US", "iowa": "US", "kansas": "US",
    "kentucky": "US", "louisiana": "US", "maine": "US", "maryland": "US",
    "massachusetts": "US", "michigan": "US", "minnesota": "US", "mississippi": "US",
    "missouri": "US", "montana": "US", "nebraska": "US", "nevada": "US",
    "ohio": "US", "oklahoma": "US", "oregon": "US", "pennsylvania": "US",
    "tennessee": "US", "texas": "US", "utah": "US", "vermont": "US",
    "virginia": "US", "washington": "US", "wisconsin": "US", "wyoming": "US",
    "ontario": "CA", "quebec": "CA", "alberta": "CA", "manitoba": "CA",
    "saskatchewan": "CA", "yukon": "CA", "queensland": "AU", "tasmania": "AU",
    "bavaria": "DE", "catalonia": "ES", "andalusia": "ES", "tuscany": "IT",
    "sicily": "IT", "flanders": "BE", "wallonia": "BE",
}


def load_wikipedia() -> dict:
    """Human-curated origins, the most precise source available.

    Ranked above MusicBrainz because it is edited and cited rather than
    inferred, is often finer than city level, carries a date, and does not
    inherit Spotify's market bias -- so it covers African and Asian genres the
    city data barely sees. Its limit is coverage, not accuracy: only ~a quarter
    of genres have the field at all.
    """
    p = OUT / "wikipedia_origins.jsonl"
    if not p.exists():
        return {}
    out = {}
    for row in load_jsonl(p):
        if not row.get("raw"):
            continue
        rec = {
            "raw": row["raw"],
            "date": row.get("date"),
            "title": row.get("title"),
            "level": row.get("level"),
            "country": row.get("country"),
        }
        if row.get("level") == "city":
            rec.update(place=row.get("place"), lat=row.get("lat"), lon=row.get("lon"))
        out[row["slug"]] = rec
    return out


def build_city_lookup(cities: list[dict]) -> dict:
    """Distinctive city word -> id of the most populous city using it.

    Matching previously ran only over cities that already listen to a genre,
    so a genre with no listening data could never match its own city -- which
    is exactly the case for most of the taxonomy. This indexes every city.
    """
    idx = {}
    for c in cities:
        # City keys carry their region ("Louisville Kentucky", "Canberra
        # Australian Capital Territory"), so indexing every word let a genre
        # named for a STATE match one city inside it, and let "australian"
        # match Canberra. Only the city's own words are indexed.
        region_words = set(re.findall(r"[a-z]{3,}", (c.get("region") or "").lower()))
        for word in re.findall(r"[a-z]{5,}", c["city"].lower()):
            if word in CITY_NAME_STOPWORDS or word in region_words:
                continue
            cur = idx.get(word)
            if cur is None or c["pop"] > cities[cur]["pop"]:
                idx[word] = c["id"]
    return idx


def name_city(genre_name: str, lookup: dict) -> int | None:
    for word in re.findall(r"[a-z]{5,}", genre_name.lower()):
        if word in lookup:
            return lookup[word]
    return None


def name_country(genre_name: str) -> str | None:
    """Whole-word nationality match only.

    Prefix matching would read "indiana" as India and "romania" as Romanian.
    A missed match costs one genre; a wrong one puts a sound on the wrong
    continent.
    """
    for token in re.findall(r"[a-z]+", genre_name.lower()):
        code = DEMONYMS.get(token) or REGION_COUNTRY.get(token)
        if code:
            return code
    return None


def main() -> int:
    jsonl, coords_p = OUT / "city_genres.jsonl", OUT / "city_coords.json"
    if not jsonl.exists() or not coords_p.exists():
        print("run 01-03 first", file=sys.stderr)
        return 1

    coords = json.loads(coords_p.read_text(encoding="utf-8"))
    rows = load_jsonl(jsonl)
    enrich = json.loads((OUT / "genres.json").read_text(encoding="utf-8")) if (OUT / "genres.json").exists() else {}
    raw_countries = json.loads((OUT / "country_genres.json").read_text(encoding="utf-8")) if (OUT / "country_genres.json").exists() else {}
    origins = load_origins()
    wiki = load_wikipedia()
    ccname = country_names()
    for o in origins.values():
        o["country"] = ccname.get(o["cc"], o["cc"])

    # ---- pass 1: assign a stable integer id to every genre we will ship.
    # A genre earns a slot if a city listens to it or a country ranks it.
    order, meta = [], {}
    for r in rows:
        for g in r["genres"]:
            if g["slug"] not in meta:
                meta[g["slug"]] = {"name": g["name"], "color": g["color"]}
                order.append(g["slug"])
    for c in raw_countries.values():
        for g in c["genres"]:
            if g["slug"] not in meta:
                e = enrich.get(g["slug"])
                if not e:
                    continue                    # unknown name/colour: skip
                meta[g["slug"]] = {"name": e["name"], "color": e["color"]}
                order.append(g["slug"])

    # EVERY genre in the taxonomy ships, including ones no city listens to and
    # no country ranks. They previously fell out here -- 314 of them, among
    # them classical, power metal and symphonic metal -- yet each still has a
    # name, a colour, a preview clip, and often a documented origin. Dropping
    # them made them unsearchable and unplayable for no gain.
    for slug, e in enrich.items():
        if slug not in meta:
            meta[slug] = {"name": e["name"], "color": e["color"]}
            order.append(slug)
    gid = {slug: i for i, slug in enumerate(order)}

    # ---- pass 2: cities, with genres referenced by integer id.
    cities, hits = [], {}
    for r in rows:
        c = coords.get(r["root"])
        if not c or not r["genres"]:
            continue
        cid = len(cities)
        pairs = sorted(
            ([gid[g["slug"]], round(g["weight"], 1)] for g in r["genres"]),
            key=lambda p: -p[1],
        )
        for i, w in pairs:
            hits.setdefault(i, []).append((cid, w))
        cities.append({
            "id": cid, "city": c["city"], "cc": c["country"],
            "country": ccname.get(c["country"], c["country"]),
            "region": c["region"],
            "lat": c["lat"], "lon": c["lon"], "pop": c["pop"],
            "dom": pairs[0][0],                 # dominant genre, for point colour
            "g": pairs,
        })

    # ---- pass 3: genre records, parallel to `order`.
    city_lookup = build_city_lookup(cities)
    genres = []
    for i, slug in enumerate(order):
        m, e = meta[slug], enrich.get(slug, {})
        h = hits.get(i, [])
        lat, lon = spherical_centroid(
            [(cities[c]["lat"], cities[c]["lon"], w) for c, w in h]
        ) if h else (0.0, 0.0)

        # A genre whose name contains one of its own cities is very likely
        # from there. Weaker than MusicBrainz, but it covers genres MusicBrainz
        # has no tag for.
        # Nationality and region are checked FIRST. They are less precise than
        # a city but far safer: "australian blues" is Australian music, not
        # Canberra music, and "ontario indie" means the province, not the town
        # of Ontario, California.
        nat = name_country(m["name"])
        named = None if nat else name_city(m["name"], city_lookup)

        genres.append({
            "slug": slug, "name": m["name"], "color": m["color"],
            "lat": round(lat, 3), "lon": round(lon, 3), "n": len(h),
            "top": [[c, round(w, 1)] for c, w in sorted(h, key=lambda t: -t[1])[:TOP_CITIES]],
            # Origin sources, strongest first. The frontend picks the best
            # available and shows which one it came from, so a cited
            # encyclopedia entry never reads the same as a statistical guess.
            "wiki": wiki.get(slug),   # human-curated, may be city or country level
            "mb": origins.get(slug),  # inferred from MusicBrainz artist areas
            "origin": named,          # a city named in the genre's own name
            "nat": nat,               # weakest: a nationality in the name
            "preview": e.get("preview_url"),
            "example": e.get("example"),
        })

    # ---- countries, keyed by normalised name so the GeoJSON can match them.
    countries = {}
    for c in raw_countries.values():
        gs = [[gid[g["slug"]], g["weight"]] for g in c["genres"] if g["slug"] in gid]
        if gs:
            countries[norm(c["name"])] = {
                "name": c["name"],
                "g": gs[:TOP_COUNTRY_GENRES],
            }

    APP.mkdir(parents=True, exist_ok=True)
    for name, obj in (("cities", cities), ("genres", genres), ("countries", countries)):
        (APP / f"{name}.json").write_text(
            json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
        )

    meta_out = {
        "cities": len(cities), "genres": len(genres),
        "links": sum(len(c["g"]) for c in cities),
        "withAudio": sum(1 for g in genres if g["preview"]),
        "withOrigin": sum(1 for g in genres
                          if g["wiki"] or g["mb"] or g["origin"] is not None or g["nat"]),
        "withNameCity": sum(1 for g in genres if g["origin"] is not None),
        "withNameCountry": sum(1 for g in genres if g["nat"]),
        "withWiki": sum(1 for g in genres if g["wiki"]),
        "withWikiCity": sum(1 for g in genres if (g["wiki"] or {}).get("level") == "city"),
        "withMbOrigin": sum(1 for g in genres if g["mb"]),
        "countries": len(countries),
        "enriched": bool(enrich),
    }
    (APP / "meta.json").write_text(json.dumps(meta_out, indent=1), encoding="utf-8")

    kb = lambda n: (APP / f"{n}.json").stat().st_size / 1024
    for k, v in meta_out.items():
        print(f"  {k:<13}: {v:,}" if isinstance(v, int) else f"  {k:<13}: {v}")
    print(f"  size         : cities {kb('cities'):.0f} KB · genres {kb('genres'):.0f} KB · countries {kb('countries'):.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
