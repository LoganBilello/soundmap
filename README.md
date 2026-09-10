# SoundMap

SoundMap places music genres on an interactive globe. Click a city to see what
it listens to. Click a genre to hear a short clip and to see every other place
that genre reaches.

The project takes its genre taxonomy from Every Noise at Once by Glenn McDonald.
It takes its geography from the Internet Archive, MusicBrainz, and GeoNames.

## How to run it

Install the dependencies once:

```bash
npm install
```

Build the data files, then start the server:

```bash
python scripts/01_index_cities.py
python scripts/02_fetch_cities.py
python scripts/03_geocode.py
python scripts/05_build_app_data.py
npm run dev
```

Step 2 takes about two hours. It writes one line per city and skips cities it
already has, so you can stop it and start it again at any time.

## Where the data comes from

| Layer | Source | License |
|---|---|---|
| Genre names, colors, preview clips | Every Noise at Once | See note below |
| Genres per city | Internet Archive snapshots | See note below |
| City coordinates | GeoNames `cities5000` | CC BY 4.0 |
| Fallback audio | iTunes Search API | Public API |

## An important constraint

The file `robots.txt` on everynoise.com blocks all automated access. No script
in this project fetches that site. Step 4 reads two pages from `data/raw/`,
and a person must save those pages by hand from a browser. See
`data/raw/PUT_SAVED_PAGES_HERE.md` for the steps.

The per-city drill-down pages on everynoise.com now return an error for every
visitor. Glenn McDonald lost access to the Spotify API in late 2023, and the
dynamic parts of the site stopped working. Step 2 reads those pages from the
Internet Archive instead. It never contacts the origin server.

Step 2 asks for one page per second and waits longer after any error.

Credit Every Noise at Once in any interface that shows this data. The preview
audio belongs to Spotify and to Apple. Check both terms before you publish this
project in public.

## The pipeline

| Script | Input | Output |
|---|---|---|
| `01_index_cities.py` | Wayback CDX index | One best snapshot per city |
| `02_fetch_cities.py` | Those snapshots | `city_genres.jsonl` |
| `03_geocode.py` | GeoNames | `city_coords.json` |
| `04_parse_everynoise.py` | Saved pages in `data/raw/` | `genres.json` |
| `05_build_app_data.py` | All of the above | Files under `public/data/` |

Step 4 is optional. The crawl in step 2 already records the name and the color
of every genre, so the globe draws without it. Step 4 adds the preview clips
and the example artists.

## Two dates matter

The genre block on each city page first appeared in 2018. A capture from 2017
is a complete page with no genres in it. Step 1 therefore prefers the most
recent capture of each city. About 96 percent of cities have a capture from
2018 or later.

## What the colors mean

A city takes the color of the genre its listeners favor most.

When you select a genre, the globe highlights every city that listens to it.
That map shows where people play a sound. It does not prove where the sound
began. For genres named after a place, such as `detroit techno`, step 5 also
records a likely origin.

## Known limits

GeoNames covers cities above 5000 people. About 8 percent of the archived
cities fall below that line and have no coordinates. Most of them are small
districts inside larger cities.

Some Spotify preview links may stop working. The player then searches the
iTunes Search API and plays a clip from there.
