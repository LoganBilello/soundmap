"""Parse the manually-saved Every Noise pages into structured JSON.

This script NEVER fetches anything. everynoise.com/robots.txt disallows
automated access, so the two source pages are saved by hand from a browser
into data/raw/ and only read from disk here.

Inputs (see data/raw/PUT_SAVED_PAGES_HERE.md):
  data/raw/engenremap.html   genre taxonomy, colors, acoustic layout, previews
  data/raw/countries.html    country -> weighted genre ranking

Outputs:
  data/out/genres.json           slug -> genre record
  data/out/country_genres.json   country -> [{slug, weight}]
"""

import html as htmllib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "out"

# --- engenremap.html -------------------------------------------------------
# <div id=item1 preview_url="https://p.scdn.co/..." class="genre scanme" scan=true
#      style="color: #ad8907; top: 4997px; left: 783px; font-size: 160%"
#      ... onclick="playx(&quot;<spotify id>&quot;, &quot;<name>&quot;, this);"
#      title="e.g. <artist> &quot;<track>&quot;">pop<a class=navlink
#      href="engenremap-pop.html" ...
RE_PREVIEW = re.compile(r'preview_url="([^"]*)"')
RE_STYLE = re.compile(
    r'style="color:\s*(#[0-9A-Fa-f]{6});\s*top:\s*([\d.]+)px;\s*'
    r'left:\s*([\d.]+)px;\s*font-size:\s*([\d.]+)%"'
)
RE_PLAYX = re.compile(r'onclick="playx\(&quot;([A-Za-z0-9]+)&quot;')
RE_TITLE = re.compile(r'title="([^"]*)"')
RE_SLUG = re.compile(r'href="engenremap-([a-z0-9_-]+)\.html"')
RE_NAME = re.compile(r'>([^<>]+)<a class=navlink')


def parse_genres(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = text.split("<div id=item")[1:]
    genres, skipped = {}, 0

    for chunk in chunks:
        head = chunk[:900]                      # everything we need is up front
        style = RE_STYLE.search(head)
        slug = RE_SLUG.search(head)
        name = RE_NAME.search(head)
        if not (style and slug and name):
            skipped += 1
            continue

        color, top, left, size = style.groups()
        preview = RE_PREVIEW.search(head)
        track = RE_PLAYX.search(head)
        title = RE_TITLE.search(head)

        genres[slug.group(1)] = {
            "slug": slug.group(1),
            "name": htmllib.unescape(name.group(1)).strip(),
            "color": color,
            # Every Noise's acoustic layout: x ~ organic->mechanical,
            # y ~ denser/atmospheric -> spikier/bouncier. Kept for the
            # "sonic neighbours" view; unrelated to geography.
            "x": float(left),
            "y": float(top),
            "prominence": float(size),
            "spotify_track": track.group(1) if track else None,
            "preview_url": preview.group(1) if preview and preview.group(1) else None,
            "example": htmllib.unescape(title.group(1)).strip() if title else None,
        }

    return {"genres": genres, "skipped": skipped}


# --- countries.html --------------------------------------------------------
# <div class="country" id=afghanistan>FLAG <a href="spotify:playlist:ID">Afghanistan</a>
#   <span class=count>3</span></div>
# <a href="...engenremap-<slug>.html" ... style="font-size: 99%; color: #9F7853">name</a><br>
RE_COUNTRY_HEAD = re.compile(
    r'id=([a-z0-9_-]+)>\s*(?:<[^>]+>)?\s*(?:&#\w+;|[^\s<]*)?\s*'
    r'<a href="spotify:(?:user:[^:]+:)?playlist:([A-Za-z0-9]+)">([^<]+)</a>'
)
RE_COUNTRY_GENRE = re.compile(
    r'href="[^"]*engenremap-([a-z0-9_-]+)\.html"[^>]*font-size:\s*([\d.]+)%'
)


def parse_countries(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = text.split('<div class="country"')[1:]
    out = {}

    for block in blocks:
        head = RE_COUNTRY_HEAD.search(block[:400])
        if not head:
            continue
        cid, playlist, label = head.groups()
        genres = [
            {"slug": s, "weight": float(w)}
            for s, w in RE_COUNTRY_GENRE.findall(block)
        ]
        if genres:
            out[cid] = {
                "id": cid,
                "name": htmllib.unescape(label).strip(),
                "playlist": playlist,
                "genres": genres,
            }
    return out


def find_saved(*candidates: str) -> pathlib.Path | None:
    """Locate a saved page by any of the names a browser might give it.

    Ctrl+S names the file after the page TITLE, not the URL, so
    engenremap.html arrives as "Every Noise at Once.html". Accept both rather
    than making anyone rename downloads.
    """
    for name in candidates:
        p = RAW / name
        if p.exists():
            return p
    return None


def main() -> int:
    genre_src = find_saved("engenremap.html", "Every Noise at Once.html")
    country_src = find_saved("countries.html", "Genres by Country.html")

    if not genre_src or not country_src:
        missing = []
        if not genre_src:
            missing.append("engenremap.html / 'Every Noise at Once.html'")
        if not country_src:
            missing.append("countries.html / 'Genres by Country.html'")
        print("Missing saved page(s): " + ", ".join(missing), file=sys.stderr)
        print(f"See {(RAW / 'PUT_SAVED_PAGES_HERE.md').relative_to(ROOT)}", file=sys.stderr)
        return 1

    print(f"  reading {genre_src.name}")
    print(f"  reading {country_src.name}")

    OUT.mkdir(parents=True, exist_ok=True)

    res = parse_genres(genre_src)
    genres = res["genres"]
    (OUT / "genres.json").write_text(
        json.dumps(genres, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    with_preview = sum(1 for g in genres.values() if g["preview_url"])
    print(f"  genres parsed     : {len(genres):,}  (skipped {res['skipped']})")
    print(f"  with preview audio: {with_preview:,}")

    countries = parse_countries(country_src)
    (OUT / "country_genres.json").write_text(
        json.dumps(countries, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    total = sum(len(c["genres"]) for c in countries.values())
    print(f"  countries parsed  : {len(countries):,}")
    print(f"  country<->genre links: {total:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
