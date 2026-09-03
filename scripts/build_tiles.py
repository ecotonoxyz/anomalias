#!/usr/bin/env python3
"""Bake XYZ WebP tiles for the historical maps in maps/ (not in git).

    python3 scripts/build_tiles.py            # both maps
    python3 scripts/build_tiles.py sara1930   # one of: igc, sara1930

Each map is first warped to a tiled Web-Mercator GeoTIFF in a temp dir (the
source files are strip-organised, which makes direct tiling very slow), then
cut with gdal2tiles into site/tiles/<name>/{z}/{x}/{y}.webp. WebP keeps the
alpha of the sheet edges and is ~4x smaller than PNG on scanned paper.

Output (site/tiles/) is git-ignored: ~200 MB, regenerable. Deploy it with the
rest of site/ (see README).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / "maps"
OUT = ROOT / "site" / "tiles"

# name → source, zoom range, extra gdalwarp args.
#   igc:      ~14.8 m/px scan (UTM 23S, nodata 0)  → native ≈ z13, z14 crisp
#   sara1930: ~1.8 m/px mosaic (WGS84, alpha band) → native ≈ z16
SETS = {
    "igc": {"src": "folhas-rmsp.tif", "zooms": "7-14",
            "warp": ["-b", "1", "-b", "2", "-b", "3", "-srcnodata", "0",
                     "-dstalpha"]},
    "sara1930": {"src": "sara1930.tif", "zooms": "10-16", "warp": []},
}


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def build(name):
    cfg = SETS[name]
    src = MAPS / cfg["src"]
    if not src.exists():
        sys.exit(f"missing {src} — the source rasters are not in git")
    dst = OUT / name
    with tempfile.TemporaryDirectory() as td:
        warped = Path(td) / f"{name}-3857.tif"
        run(["gdalwarp", "-q", "-t_srs", "EPSG:3857", "-r", "bilinear",
             "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
             "-co", "TILED=YES", "-co", "BLOCKXSIZE=512", "-co", "BLOCKYSIZE=512",
             "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=2", "-co", "BIGTIFF=YES",
             "-co", "NUM_THREADS=ALL_CPUS", *cfg["warp"], src, warped])
        if dst.exists():
            shutil.rmtree(dst)
        run(["gdal2tiles", "--xyz", "-z", cfg["zooms"], "--processes=5",
             "--tiledriver=WEBP", "--webp-quality=78", "-r", "bilinear",
             "-w", "none", "-q", warped, dst])
    n = sum(1 for _ in dst.rglob("*.webp"))
    mb = sum(f.stat().st_size for f in dst.rglob("*.webp")) / 1e6
    print(f"  -> {dst.relative_to(ROOT)}: {n} tiles, {mb:.0f} MB")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    only = sys.argv[1:] or list(SETS)
    for name in only:
        print(f"[{name}]")
        build(name)
