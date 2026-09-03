#!/usr/bin/env python3
"""Even out the per-sheet colour and brightness of the scanned map mosaics.

    python3 scripts/uniformize_sheets.py            # both maps
    python3 scripts/uniformize_sheets.py sara1930   # one of: igc, sara1930

Each mosaic is a patchwork of sheets scanned with different tones (pinkish,
yellowed, dark...). The script splits the mosaic into regions — the sheet
lattice for SARA (500 m cells that the fit in the README recovers to within
~10 px), tone steps + nodata gaps for the IGC patchwork — measures each
region's paper (90th percentile) and ink (3rd percentile) per channel, and
maps them linearly onto the mosaic-wide medians. Parameters are blended over
a few pixels at region edges so a slightly-off boundary doesn't leave a
line. Output: maps/<name>-uniform.tif, which scripts/build_tiles.py tiles.

The IGC source is UTM; its sheets follow meridians/parallels, so it is
warped to EPSG:4326 first (sheet edges become axis-aligned) and the uniform
file stays in EPSG:4326 — build_tiles warps it to Web Mercator anyway.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
MAPS = ROOT / "maps"
SCALE = 8            # analysis is done on a 1/8 overview
BLOCK = 512          # rows per output block

SETS = {
    # SARA: white = nodata; sheets on a regular lattice (px at full res,
    # measured on the source: pitch and phase of the column/row edges)
    "sara1930": {"src": "sara1930.tif", "nodata": "white",
                 "lattice": (299.2, 108.7, 275.7, 115.9), "feather": 2,
                 "warp4326": False},
    # IGC: 0 = nodata; irregular patchwork → segment by tone steps and gaps
    "igc": {"src": "folhas-rmsp.tif", "nodata": "zero", "lattice": None,
            "feather": 1, "warp4326": True},
}


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def valid_mask(rgb, nodata):
    if nodata == "white":
        return ~((rgb[0] >= 250) & (rgb[1] >= 250) & (rgb[2] >= 250))
    return rgb.max(axis=0) > 0


def detected_walls(lum, valid, thr=4.0):
    """Boundary columns/rows: where the mean |step| across a column/row of the
    mosaic stands well above the texture baseline, or the valid mask flips."""
    H, W = lum.shape
    walls = np.zeros((H, W), bool)
    for axis in (0, 1):
        d = np.abs(np.diff(lum, axis=1 - axis))          # steps along the other axis
        v = valid[:, :-1] & valid[:, 1:] if axis == 0 else valid[:-1, :] & valid[1:, :]
        cnt = v.sum(axis=axis)
        prof = np.where(cnt > 0, (d * v).sum(axis=axis) / np.maximum(cnt, 1), 0)
        base = np.median(prof[cnt > 0.05 * cnt.max()]) if cnt.max() else 0
        strong = (prof - base > thr) & (cnt > 0.05 * cnt.max())
        # nodata transitions (gaps between sheets)
        vc = valid.sum(axis=axis)
        flip = np.abs(np.diff((vc > 0.02 * vc.max()).astype(int))) > 0
        idx = np.where(strong | flip)[0]
        for i in idx:
            if axis == 0:
                walls[:, max(0, i):i + 2] = True
            else:
                walls[max(0, i):i + 2, :] = True
    return walls


def lattice_walls(shape, lattice):
    H, W = shape
    px, phx, py, phy = [v / SCALE for v in lattice]
    walls = np.zeros((H, W), bool)
    x = phx
    while x < W:
        walls[:, int(round(x))] = True
        x += px
    y = phy
    while y < H:
        walls[int(round(y)), :] = True
        y += py
    return walls


def region_params(rgb, valid, labels, nlab, lo_p=3, hi_p=90, min_px=150):
    """Per-region linear map (A, B) per channel: ink→target ink, paper→target
    paper. Regions with too few pixels get no params (NaN) and later inherit
    the nearest region's."""
    A = np.full((nlab + 1, 3), np.nan, np.float32)
    B = np.full((nlab + 1, 3), np.nan, np.float32)
    inks, papers, areas = [], [], []
    idx = ndimage.find_objects(labels)
    stats = {}
    for lab, sl in enumerate(idx, start=1):
        if sl is None:
            continue
        m = (labels[sl] == lab) & valid[sl]
        if m.sum() < min_px:
            continue
        px = rgb[:, sl[0], sl[1]][:, m]              # (3, n)
        ink = np.percentile(px, lo_p, axis=1)
        paper = np.percentile(px, hi_p, axis=1)
        # not a paper-and-ink sheet (legend strips, colour keys, title
        # blocks): no bright paper or almost no dynamic range → inherit the
        # neighbours' correction instead of a wild stretch
        if paper.mean() < 90 or (paper - ink).mean() < 30:
            continue
        stats[lab] = (ink, paper, m.sum())
        inks.append(ink); papers.append(paper); areas.append(m.sum())
    t_ink = np.median(np.array(inks), axis=0)
    t_paper = np.median(np.array(papers), axis=0)
    for lab, (ink, paper, n) in stats.items():
        span = np.maximum(paper - ink, 8.0)
        a = np.clip((t_paper - t_ink) / span, 0.5, 2.5)
        A[lab] = a
        B[lab] = t_ink - a * ink
    print(f"    {len(stats)} regions with stats; target ink {t_ink.round(1)} "
          f"paper {t_paper.round(1)}")
    return A, B


def build_param_maps(rgb, valid, walls, feather):
    labels, nlab = ndimage.label(valid & ~walls)
    print(f"    {nlab} regions")
    A, B = region_params(rgb, valid, labels, nlab)
    good = ~np.isnan(A[:, 0])
    good[0] = False
    lab_ok = np.where(good[labels], labels, 0)
    # every pixel (walls, tiny regions, nodata) takes the nearest good region
    _, (iy, ix) = ndimage.distance_transform_edt(lab_ok == 0, return_indices=True)
    near = lab_ok[iy, ix]
    Amap = A[near]                                    # (H, W, 3)
    Bmap = B[near]
    if feather > 0:
        size = 2 * feather + 1
        for c in range(3):
            Amap[..., c] = ndimage.uniform_filter(Amap[..., c], size)
            Bmap[..., c] = ndimage.uniform_filter(Bmap[..., c], size)
    return Amap, Bmap, labels


def upsample_rows(m, r0, r1, W):
    """Bilinear upsample of the 1/SCALE map to cover full-res rows [r0, r1)
    → (r1-r0, W, 3). The map has H//SCALE rows, so the last few source rows
    beyond its coverage take the map's last row."""
    from PIL import Image
    h = r1 - r0
    y0 = max(0, r0 // SCALE - 1)
    y1 = min(m.shape[0], r1 // SCALE + 2)
    out = np.empty((h, W, 3), np.float32)
    rows = np.clip(np.arange(r0, r1) - y0 * SCALE, 0, (y1 - y0) * SCALE - 1)
    for c in range(3):
        im = Image.fromarray(np.ascontiguousarray(m[y0:y1, :, c], dtype=np.float32))
        big = im.resize((W, (y1 - y0) * SCALE), Image.BILINEAR)
        arr = np.asarray(big, dtype=np.float32)
        out[..., c] = arr[rows, :]
    return out


def uniformize(src_path, dst_path, nodata, lattice, feather):
    with rasterio.open(src_path) as src:
        H, W = src.height, src.width
        h8, w8 = H // SCALE, W // SCALE
        print(f"    {src_path.name}: {W}x{H}, analysing at {w8}x{h8}")
        rgb8 = src.read([1, 2, 3], out_shape=(3, h8, w8)).astype(np.float32)
        valid8 = valid_mask(rgb8, nodata)
        lum8 = rgb8.mean(axis=0)
        walls = lattice_walls(lum8.shape, lattice) if lattice else \
            detected_walls(lum8, valid8)
        Amap, Bmap, labels = build_param_maps(rgb8, valid8, walls, feather)

        prof = src.profile.copy()
        prof.update(count=3, dtype="uint8", tiled=True, blockxsize=512,
                    blockysize=512, compress="deflate", predictor=2,
                    bigtiff="YES", nodata=(0 if nodata == "zero" else None))
        prof.pop("photometric", None)
        lo, hi = (1, 255) if nodata == "zero" else (0, 254)
        with rasterio.open(dst_path, "w", **prof) as dst:
            for r0 in range(0, H, BLOCK):
                r1 = min(H, r0 + BLOCK)
                win = Window(0, r0, W, r1 - r0)
                blk = src.read([1, 2, 3], window=win).astype(np.float32)
                v = valid_mask(blk, nodata)
                A = upsample_rows(Amap, r0, r1, W)
                Bm = upsample_rows(Bmap, r0, r1, W)
                out = np.empty_like(blk)
                for c in range(3):
                    out[c] = np.clip(A[..., c] * blk[c] + Bm[..., c], lo, hi)
                fill = 0 if nodata == "zero" else 255
                out = np.where(v[None], out, fill).astype(np.uint8)
                dst.write(out, window=win)
                if (r0 // BLOCK) % 10 == 0:
                    print(f"    rows {r0}/{H}", end="\r")
        print(f"    -> {dst_path.name}: {dst_path.stat().st_size/1e6:.0f} MB")
        # overview for a quick look
        from PIL import Image
        with rasterio.open(dst_path) as d:
            ov = d.read([1, 2, 3], out_shape=(3, h8 // 2, w8 // 2))
        Image.fromarray(ov.transpose(1, 2, 0)).save(
            dst_path.with_suffix(".preview.jpg"), quality=80)


def main():
    only = sys.argv[1:] or list(SETS)
    for name in only:
        cfg = SETS[name]
        src = MAPS / cfg["src"]
        dst = MAPS / f"{name}-uniform.tif"
        print(f"[{name}]")
        if cfg["warp4326"]:
            with tempfile.TemporaryDirectory() as td:
                w = Path(td) / "src4326.tif"
                run(["gdalwarp", "-q", "-t_srs", "EPSG:4326", "-r", "bilinear",
                     "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
                     "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
                     "-co", "BIGTIFF=YES", "-b", "1", "-b", "2", "-b", "3",
                     "-srcnodata", "0", "-dstnodata", "0", src, w])
                uniformize(w, dst, cfg["nodata"], cfg["lattice"], cfg["feather"])
        else:
            uniformize(src, dst, cfg["nodata"], cfg["lattice"], cfg["feather"])


if __name__ == "__main__":
    main()
