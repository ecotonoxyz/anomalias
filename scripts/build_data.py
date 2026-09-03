#!/usr/bin/env python3
"""Build site/data + site/media for the Anomalias page.

    python3 scripts/build_data.py            # every step
    python3 scripts/build_data.py media      # one step (hidro, roads, water,
                                             #   places, media)

Needs: GDAL CLI (ogr2ogr), exiftool, Pillow. The first run downloads the
cartography (Overpass API + telhas.pedalhidrografi.co); the raw downloads are
cached in geodata/ (git-ignored) so later runs are offline.

Hand-edited content lives in site/data/anomalias.json (never touched here)
and in the editorial fields of site/data/media.json (anom, title, text),
which survive rebuilds — they are keyed by the source photo's filename.
"""
import json
import math
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "geodata"
IMAGES = ROOT / "images" / "raw"
SITE = ROOT / "site"
DATA = SITE / "data"
MEDIA = SITE / "media"
THUMB = MEDIA / "t"

# Display window: the São Paulo metropolis (lonmin latmin lonmax latmax).
BBOX = (-47.0, -24.05, -46.2, -23.3)
OVERPASS = "https://overpass-api.de/api/interpreter"
WATER_FGB = ("https://telhas.pedalhidrografi.co/viario/"
             "south-america-water-areas.fgb")
# A photo is attached to the closest anomaly if it is within this radius.
ATTACH_M = 3000


def run(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def simplify(src, dst, tol):
    """ogr2ogr pass: Douglas-Peucker + 5-decimal coordinates."""
    dst.unlink(missing_ok=True)
    run(["ogr2ogr", "-f", "GeoJSON", "-lco", "COORDINATE_PRECISION=5",
         "-lco", "RFC7946=YES", "-simplify", str(tol), dst, src])
    print(f"    -> {dst.name}: {dst.stat().st_size/1e6:.2f} MB")


def overpass(name, body):
    """Cached Overpass query; `body` uses {bbox} for S,W,N,E."""
    GEO.mkdir(exist_ok=True)
    cache = GEO / f"overpass-{name}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    lonmin, latmin, lonmax, latmax = BBOX
    q = ("[out:json][timeout:280];"
         + body.format(bbox=f"{latmin},{lonmin},{latmax},{lonmax}")
         + "out geom;")
    print(f"  overpass: {name} …")
    req = urllib.request.Request(
        OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "ecotono-anomalias build (danilo.lessa@gmail.com)"})
    with urllib.request.urlopen(req, timeout=320) as r:
        raw = r.read()
    cache.write_bytes(raw)
    return json.loads(raw)


def way_feature(w, props):
    coords = [[round(p["lon"], 5), round(p["lat"], 5)] for p in w["geometry"]]
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "LineString", "coordinates": coords}}


def write_fc(path, feats):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                               ensure_ascii=False))


HIDRO_FGB = ("https://telhas.pedalhidrografi.co/viario/"
             "south-america-hidro.fgb")
HIDRO_KINDS = {"river", "stream", "canal", "ditch", "drain"}


def build_hidro():
    """'Morros e Águas' (Pedal Hidrográfico): OSM waterway lines + natural=ridge
    crest lines from the same FlatGeobuf the amora app reads, clipped to the
    window. site/data/hidro.geojson carries the lines (k = river|stream|canal|
    ditch|drain|ridge, t = 1 when culverted/tunnelled); hidro-names.geojson
    merges named rivers, canals and ridges into one feature per name, so the
    map labels each once instead of per OSM way."""
    GEO.mkdir(exist_ok=True)
    cache = GEO / "hidro-raw.geojson"
    if not cache.exists():
        run(["ogr2ogr", "-f", "GeoJSON", "-lco", "COORDINATE_PRECISION=6",
             "-spat", *BBOX, cache, "/vsicurl/" + HIDRO_FGB])
    fc = json.loads(cache.read_text())
    lines, names = [], {}
    for f in fc["features"]:
        g, p = f.get("geometry"), f.get("properties") or {}
        if not g or g["type"] not in ("LineString", "MultiLineString"):
            continue
        k = "ridge" if p.get("natural") == "ridge" else p.get("waterway")
        if k not in HIDRO_KINDS and k != "ridge":
            continue
        t = 1 if p.get("tunnel") and p["tunnel"] != "no" else 0
        name = (p.get("name") or "").strip()
        lines.append({"type": "Feature", "geometry": g,
                      "properties": {"k": k, "t": t, "name": name}})
        if name and k in ("river", "canal", "ridge"):
            parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            names.setdefault((name, k), []).extend(parts)
    name_feats = [{"type": "Feature", "properties": {"name": n, "k": k},
                   "geometry": {"type": "MultiLineString", "coordinates": v}}
                  for (n, k), v in names.items()]
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "hidro.geojson"
        write_fc(tmp, lines)
        simplify(tmp, DATA / "hidro.geojson", 0.00004)
        tmp = Path(td) / "hidro-names.geojson"
        write_fc(tmp, name_feats)
        simplify(tmp, DATA / "hidro-names.geojson", 0.00006)
    kinds = {}
    for f in lines:
        kinds[f["properties"]["k"]] = kinds.get(f["properties"]["k"], 0) + 1
    print(f"    {len(lines)} lines {kinds}; {len(name_feats)} named features")


def build_roads():
    raw = overpass(
        "roads",
        '(way["highway"~"^(motorway|trunk|primary)$"]({bbox});'
        'way["railway"="rail"]["usage"!~"industrial|military"]'
        '["service"!~"."]({bbox}););')
    roads, rail = [], []
    for w in raw["elements"]:
        t = w.get("tags") or {}
        if t.get("railway") == "rail":
            rail.append(way_feature(w, {}))
        elif t.get("highway") in ("motorway", "trunk", "primary"):
            roads.append(way_feature(w, {"cls": t["highway"]}))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "roads.geojson"
        write_fc(tmp, roads)
        simplify(tmp, DATA / "roads.geojson", 0.00015)
        tmp = Path(td) / "rail.geojson"
        write_fc(tmp, rail)
        simplify(tmp, DATA / "rail.geojson", 0.0001)
    print(f"    {len(roads)} road + {len(rail)} rail segments")


def ring_area_m2(ring):
    """Planar shoelace on lon/lat scaled to metres — fine at this size."""
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111320 * math.cos(math.radians(lat0)), 111320
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        s += x1 * kx * y2 * ky - x2 * kx * y1 * ky
    return abs(s) / 2


def build_water():
    """Reservoirs and river channels (OSM natural=water polygons) from the
    Pedal Hidrográfico FlatGeobuf, keeping bodies above ~2 ha."""
    GEO.mkdir(exist_ok=True)
    cache = GEO / "water-areas-raw.geojson"
    if not cache.exists():
        run(["ogr2ogr", "-f", "GeoJSON", "-lco", "COORDINATE_PRECISION=5",
             "-spat", *BBOX, cache, "/vsicurl/" + WATER_FGB])
    fc = json.loads(cache.read_text())
    keep = []
    for f in fc["features"]:
        g = f["geometry"]
        rings = (g["coordinates"] if g["type"] == "Polygon"
                 else [r for poly in g["coordinates"] for r in poly[:1]])
        area = sum(ring_area_m2(r) for r in rings[:1]) if g["type"] == "Polygon" \
            else sum(ring_area_m2(r) for r in rings)
        if area >= 20000:
            f["properties"] = {"km2": round(area / 1e6, 3)}
            keep.append(f)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "water.geojson"
        write_fc(tmp, keep)
        simplify(tmp, DATA / "water.geojson", 0.00008)
    print(f"    {len(keep)} of {len(fc['features'])} water bodies kept")


# Label layer: landscape features only — no municipality or bairro names on
# the map (the anomaly cards carry the human place context). Positions are
# approximate; ridge/hill names from OSM come with the Morros e Águas data.
RELEVO = [
    {"name": "serra da cantareira",    "lat": -23.400, "lon": -46.600},
    {"name": "planalto de taipas",     "lat": -23.435, "lon": -46.735},
    {"name": "represa billings",       "lat": -23.760, "lon": -46.600},
    {"name": "represa guarapiranga",   "lat": -23.720, "lon": -46.745},
    {"name": "serra do mar",           "lat": -23.860, "lon": -46.480},
]


def build_places():
    feats = [{"type": "Feature",
               "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
               "properties": {"name": s["name"], "rank": 4, "kind": "relevo"}}
              for s in RELEVO]
    out = DATA / "places.geojson"
    write_fc(out, feats)
    print(f"    -> {out.name}: {out.stat().st_size/1e3:.1f} kB")


def hfov_deg(focal35, width, height):
    """Horizontal FOV from 35 mm-equivalent focal length (36×24 mm frame)."""
    if not focal35:
        return None
    half = 18.0 if width >= height else 12.0
    return round(2 * math.degrees(math.atan(half / focal35)), 1)


def dist_m(lat1, lon1, lat2, lon2):
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 111320)


def build_media():
    """Geolocated photos from images/raw → site/media (+ square thumbs) and
    site/data/media.json. Each photo is attached to the nearest anomaly."""
    MEDIA.mkdir(parents=True, exist_ok=True)
    THUMB.mkdir(parents=True, exist_ok=True)
    anoms = json.loads((DATA / "anomalias.json").read_text())
    exts = {".jpeg", ".jpg", ".png", ".heic"}
    files = sorted(p for p in IMAGES.iterdir()
                   if p.suffix.lower() in exts and not p.name.startswith("."))
    if not files:
        print("  no photos found"); return

    raw = json.loads(subprocess.run(
        ["exiftool", "-json", "-n",
         "-GPSLatitude", "-GPSLongitude", "-GPSAltitude", "-GPSImgDirection",
         "-DateTimeOriginal", "-CreateDate", "-Model", "-FocalLength35efl",
         "-ImageWidth", "-ImageHeight", "-Orientation",
         *[str(f) for f in files]],
        capture_output=True, check=True).stdout)

    mj = DATA / "media.json"
    old = {}
    if mj.exists():
        old = {m["src"]: m for m in json.loads(mj.read_text())}

    items, used_ids = [], set()
    for meta in raw:
        src = Path(meta["SourceFile"])
        lat, lon = meta.get("GPSLatitude"), meta.get("GPSLongitude")
        if lat is None or lon is None:
            print(f"  !! {src.name}: no GPS, skipped"); continue
        prev = old.get(src.stem, {})
        dt = meta.get("DateTimeOriginal") or meta.get("CreateDate") or ""

        near = min(anoms, key=lambda a: dist_m(lat, lon, a["lat"], a["lon"]))
        auto = near["id"] if dist_m(lat, lon, near["lat"], near["lon"]) <= ATTACH_M else ""
        anom = prev.get("anom") or auto
        if not anom:
            print(f"  !! {src.name}: no anomaly within {ATTACH_M} m — set 'anom' by hand")

        stamp = dt.replace(":", "").replace(" ", "-")[:13] or src.stem[:8]
        mid = f"{anom or 'solta'}-{stamp}"
        while mid in used_ids:
            mid += "b"
        used_ids.add(mid)

        # web copy: orientation baked in, ≤1600 px; square thumb for markers
        im = ImageOps.exif_transpose(Image.open(src))
        web = MEDIA / f"{mid}.jpeg"
        if not web.exists():
            w = im.convert("RGB"); w.thumbnail((1600, 1600))
            w.save(web, quality=85, optimize=True)
        th = THUMB / f"{mid}.jpeg"
        if not th.exists():
            ImageOps.fit(im.convert("RGB"), (256, 256)).save(
                th, quality=75, optimize=True)

        wpx, hpx = im.width, im.height
        heading = meta.get("GPSImgDirection")
        alt = meta.get("GPSAltitude")
        items.append({
            "id": mid, "src": src.stem, "file": web.name,
            "anom": anom,
            "lat": round(lat, 6), "lon": round(lon, 6),
            "alt": round(alt, 1) if alt else None,
            "heading": round(heading, 1) if heading is not None else None,
            "hfov": hfov_deg(meta.get("FocalLength35efl"), wpx, hpx),
            "datetime": dt or None,
            "camera": meta.get("Model"),
            "w": wpx, "h": hpx,
            # editorial — fill by hand, preserved across rebuilds
            "title": prev.get("title", ""),
            "text": prev.get("text", ""),
        })

    items.sort(key=lambda m: m["datetime"] or "")
    mj.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    per = {a["id"]: sum(1 for m in items if m["anom"] == a["id"]) for a in anoms}
    (DATA / "stats.json").write_text(json.dumps(
        {"anomalias": len(anoms), "fotos": len(items), "por_anomalia": per}))
    print(f"    -> media.json: {len(items)} items; per anomaly: {per}")


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    steps = {"hidro": build_hidro, "roads": build_roads, "water": build_water,
             "places": build_places, "media": build_media}
    only = sys.argv[1:]
    for name, fn in steps.items():
        if only and name not in only:
            continue
        print(f"[{name}]")
        fn()
