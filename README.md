# SoundMap

SoundMap places music genres on an interactive globe. Click a city to see what
it listens to. Click a genre to hear a short clip, and to watch arcs trace from
where the sound began to every place it reached. Click a country for its
signature sounds.

The genre taxonomy comes from Every Noise at Once by Glenn McDonald. The
geography comes from the Internet Archive, Wikipedia, MusicBrainz and GeoNames.

## How to run it

Install the dependencies once:

```bash
npm install
python -m pip install -r requirements.txt
```

Build the data, then start the server:

```bash
python scripts/01_index_cities.py
python scripts/02_fetch_cities.py
python scripts/03_geocode.py
python scripts/05_build_app_data.py
npm run dev
```

Step 2 takes about five hours. Every crawl writes one line per record and skips
records it already has, so you can stop it and start it again at any time. A
crash costs time and never data.

Two steps are optional. Step 4 reads pages you save by hand and adds preview
clips. Steps 6 and 8 add genre origins.

## The pipeline

| Script | Input | Output |
|---|---|---|
| `01_index_cities.py` | Wayback CDX index | One best snapshot per city |
| `02_fetch_cities.py` | Those snapshots | `city_genres.jsonl` |
| `03_geocode.py` | GeoNames | `city_coords.json` |
| `04_parse_everynoise.py` | Saved pages in `data/raw/` | `genres.json` |
| `05_build_app_data.py` | All of the above | Files under `public/data/` |
| `06_musicbrainz_origins.py` | MusicBrainz | `genre_origins.jsonl` |
| `07_autorebuild.py` | The crawl files | Reruns step 5 as they grow |
| `08_wikipedia_origins.py` | Wikipedia | `wikipedia_origins.jsonl` |

Run step 7 beside the crawls and leave it going. It rebuilds the app data every
minute, so refreshing the page is all you need to see new cities appear.

Every script finds the project from its own file path. An absolute path
therefore works from any folder.

## Where the data comes from

| Layer | Source | License |
|---|---|---|
| Genre names, colors, preview clips | Every Noise at Once | See the note below |
| Genres per city | Internet Archive snapshots | See the note below |
| Genre origins, first choice | Wikipedia infoboxes | CC BY-SA |
| Genre origins, second choice | MusicBrainz | CC0 |
| City coordinates | GeoNames `cities5000` | CC BY 4.0 |
| Fallback audio | iTunes Search API | Public API |

No data is committed. Run the pipeline to build your own copy.

## An important constraint

The file `robots.txt` on everynoise.com blocks all automated access. No script
here fetches that site. Step 4 reads two pages from `data/raw/`, and a person
must save those pages by hand from a browser. See
`data/raw/PUT_SAVED_PAGES_HERE.md` for the steps.

The per-city pages on everynoise.com now return an error for every visitor.
Glenn McDonald lost access to the Spotify API in late 2023, and the dynamic
parts of the site stopped working. Step 2 reads those pages from the Internet
Archive instead, one request per second. It never contacts the origin server.

Credit Every Noise at Once in any interface that shows this data. The preview
audio belongs to Spotify and to Apple. Check both terms before you publish this
project in public.

## How a genre origin is chosen

Three sources rank in order. Each result records which one produced it, so the
interface never shows a cited entry and a statistical guess in the same way.

1. **Wikipedia** supplies the `cultural_origins` field from music genre
   infoboxes. People write and cite it. It often names a district rather than a
   city, and it is the only source that gives a date. It also ignores which
   countries Spotify serves, so it reaches gqom, highlife and afrobeats.
2. **MusicBrainz** supplies the most common `begin-area` among the artists who
   carry a genre tag.
3. **The name of the genre** supplies the rest. The phrase `detroit techno`
   names its own origin.

About 13 percent of genres carry a Wikipedia origin. The taxonomy holds many
narrow inventions that no encyclopedia describes, such as `deep australian
indie`. Most results name a country rather than a city.

MusicBrainz results pass a coverage test, not a share test. Coverage means the
count of artists sampled divided by the count of artists who carry the tag.
Share alone reverses the answer. The tag `rock` gives London 52 percent of its
placed artists, drawn from 100 of 43,811. The tag `detroit techno` gives Detroit
78 percent, drawn from all 47. The first number rests on 0.2 percent of the
evidence and supports nothing. SoundMap therefore shows no origin unless one
place holds at least 20 percent coverage and 30 percent share.

This rule refuses some correct answers. Reggaeton began in San Juan, and
SoundMap says nothing, because the sample covers 8 percent of the tag. An
answer the evidence cannot support is worse than no answer.

## Two dates matter

The genre block on each city page first appeared in 2018. A capture from 2017
is a complete page with no genres in it. Step 1 therefore prefers the most
recent capture of each city. About 96 percent of cities have a capture from
2018 or later.

## What the colors mean

A city takes the color of the genre its listeners favor most.

Select a genre and the globe lights every city that listens to it. That map
shows where people play a sound. A ring marks where the sound began, and arcs
run from the ring to the cities that took it up.

## How the globe stays fast

The scene holds about 133 draw calls and runs above 200 frames per second. An
earlier version held 1,641 draw calls and a million triangles, and ran at 20 to
34 frames per second. Three changes account for the difference. Read them before
you change the rendering.

- **Countries live in the globe texture.** Drawing each country as its own
  raised shape cost about 1,500 draw calls per frame, at every zoom level. The
  map is now painted once into an image that wraps the sphere. Clicks on a
  country run a point in polygon test on the processor instead.
- **Cities are one point cloud.** Each city used to be a separate sphere, so
  the scene carried thousands of objects to move and test every frame. They now
  share a single buffer that the graphics card draws in one pass.
- **Country labels rebuild only when they change.** The label set used to
  rebuild on every frame of every drag. That alone caused most of the delay
  people noticed.

Dots keep a fixed size on screen and sit flat against the surface. Both choices
follow radio.garden, which draws its circles in the same way. Dots that scale
with distance blur as you approach, and dots that float above the surface drift
away from their true position at a shallow angle.

## Known limits

**The city layer shows where Spotify had users.** It does not show where music
lives. The archive holds 54 African cities and 116 Swedish ones. Spotify reached
South Africa in 2018 and most of the rest of Africa in February 2021, so few
African listeners existed for Every Noise to count. Wikipedia narrows this gap
for the places where sounds began. No Spotify measurement can close it for the
listening layer.

**A skewed catalog still yields a skewed answer.** The coverage test corrects a
sample that is too small. It cannot correct a record that under counts a whole
region. The genre `desi` resolves to London across every one of its 61 tagged
artists, because MusicBrainz lists more British Asian artists than South Asian
ones.

**GeoNames covers cities above 5000 people.** About 8 percent of the archived
cities fall below that line and carry no coordinates. Most are districts inside
larger cities.

**Some Spotify preview links may stop working.** The player then searches the
iTunes Search API and plays a clip from there.

**One dependency, and the reason for it.** Python here trusts an old list of
certificate authorities. That list rejects the Wikipedia certificate while it
still accepts MusicBrainz and the Internet Archive. The package `certifi`
supplies a current list. The project never turns certificate checking off.
