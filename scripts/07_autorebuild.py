"""Rebuild the app data whenever the crawls have produced something new.

The crawls append to data/out/*.jsonl, but the browser reads public/data/*.json,
and nothing connects the two until 05 runs. This watches the crawl files and
reruns 05 when they grow, so refreshing the page is genuinely all you need to
see new cities appear.

Run it alongside the crawls and leave it going:

    python scripts/07_autorebuild.py

Stop it with Ctrl+C at any time. It changes nothing except public/data/.
"""

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WATCH = [
    ROOT / "data" / "out" / "city_genres.jsonl",
    ROOT / "data" / "out" / "genre_origins.jsonl",
]
BUILD = ROOT / "scripts" / "05_build_app_data.py"

INTERVAL = 60      # seconds between checks
MIN_NEW = 25       # rebuild once this many new rows have landed


def sizes() -> tuple[int, ...]:
    return tuple(p.stat().st_size if p.exists() else 0 for p in WATCH)


def rows() -> int:
    n = 0
    for p in WATCH:
        if p.exists():
            with p.open("rb") as f:
                n += sum(1 for _ in f)
    return n


def rebuild() -> str:
    r = subprocess.run(
        [sys.executable, str(BUILD)], capture_output=True, text=True, cwd=ROOT
    )
    for line in r.stdout.splitlines():
        if "cities" in line and ":" in line:
            return line.strip()
    return "rebuilt"


def main() -> int:
    print("watching the crawls; rebuilding as they grow. Ctrl+C to stop.\n", flush=True)
    last_sizes, last_rows = sizes(), rows()
    print(f"  starting at {last_rows:,} crawled rows", flush=True)

    try:
        while True:
            time.sleep(INTERVAL)
            now_sizes, now_rows = sizes(), rows()

            if now_sizes == last_sizes:
                print("  (no new data — are the crawls still running?)", flush=True)
                continue
            if now_rows - last_rows < MIN_NEW:
                continue

            stamp = time.strftime("%H:%M:%S")
            print(f"  {stamp}  +{now_rows - last_rows} rows -> {rebuild()}", flush=True)
            last_sizes, last_rows = now_sizes, now_rows
    except KeyboardInterrupt:
        print("\nstopped. public/data/ holds the last rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
