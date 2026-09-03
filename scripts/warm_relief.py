#!/usr/bin/env python3
"""Pre-warm the CDN cache of the Câmera Topográfica relief tiles.

    python3 scripts/warm_relief.py           # window z9–13 + anomalies z14–15
    python3 scripts/warm_relief.py 3         # concurrency (default 3)

The relief basemap is rendered on demand by cameratopo.pedalhidrografi.co
(~1–5 s per cold tile) and cached for a week at the CDN. Run this after
changing RELIEF in site/index.html, or before showing the site, so the first
visitor doesn't stare at blurry tiles. Both DEMs are warmed: FABDEM over the
whole window, the São Paulo DEM only inside its footprint.
"""
import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "site" / "index.html").read_text()

# keep these in sync with site/index.html
RELIEF = HTML.split('var RELIEF = "')[1].split('"')[0]
BASE = "https://cameratopo.pedalhidrografi.co/{z}/{x}/{y}.png?" + RELIEF + "&dem={dem}"
WINDOW = (-47.0, -24.05, -46.2, -23.3)              # lonmin latmin lonmax latmax
SP_DEM = (-46.9482, -23.8062, -46.2347, -23.3730)   # dem=sp footprint
ANOMS = json.loads((ROOT / "site" / "data" / "anomalias.json").read_text())


def tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def tiles_in(bbox, z):
    x0, y0 = tile(bbox[0], bbox[3], z)
    x1, y1 = tile(bbox[2], bbox[1], z)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def inside(bbox, z, x, y):
    n = 2 ** z
    lon = x / n * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def main():
    conc = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    jobs = set()
    for z in range(9, 14):
        for t in tiles_in(WINDOW, z):
            jobs.add(("fabdem",) + t)
            if inside(SP_DEM, *t):
                jobs.add(("sp",) + t)
    for a in ANOMS:                       # ~2 km around each anomaly, z14–15
        d = 0.012
        box = (a["lon"] - d, a["lat"] - d, a["lon"] + d, a["lat"] + d)
        for z in (14, 15):
            for t in tiles_in(box, z):
                jobs.add(("fabdem",) + t) if z == 14 else None
                if inside(SP_DEM, *t):
                    jobs.add(("sp",) + t)
    jobs = sorted(jobs)
    print(f"{len(jobs)} tiles, concurrency {conc}")

    def fetch(j):
        dem, z, x, y = j
        url = BASE.format(z=z, x=x, y=y, dem=dem)
        try:
            req = urllib.request.Request(url, headers={   # CDN 403s bare urllib
                "User-Agent": "Mozilla/5.0 ecotono-anomalias warm_relief.py"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, r.headers.get("cf-cache-status", "?")
        except Exception as e:                    # noqa: BLE001
            return "ERR", str(e)[:60]

    hits = miss = err = 0
    with ThreadPoolExecutor(conc) as ex:
        for i, (st, cf) in enumerate(ex.map(fetch, jobs), 1):
            if st == "ERR":
                err += 1
            elif cf == "HIT":
                hits += 1
            else:
                miss += 1
            if i % 50 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  hit {hits}  rendered {miss}  err {err}")


if __name__ == "__main__":
    main()
