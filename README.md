# Novas e Antigas Anomalias do Sítio Urbano de São Paulo

Single-page interactive geospatial experience for the *Anomalias* series —
seven landscape-characters of the São Paulo metropolis, their stories and
geolocated field photos, over a high-resolution relief and two historical
map layers. Sibling of `../ilhasdepedra`; live at `ecotono.xyz/anomalias`.

1. A Colina de Rejeito de Santo Amaro (-23.694041, -46.688268)
2. O braço morto do Tietê (-23.505268, -46.779355)
3. A cratera de Colônia (-23.870810, -46.706796)
4. A floresta suspensa do Jaguaré (-23.545755, -46.734598)
5. Os cânions da Brasilândia (-23.451310, -46.707063)
6. O mar paulista (-23.706353, -46.667515)
7. O polígono das voçorocas (-23.563526, -46.385389)

## Layout

```
site/            the deployable static page (index.html + data/ + media/ + slides/ + tiles/)
scripts/         build_data.py (site/data + site/media) · build_tiles.py (historical map tiles)
images/raw/      the field photos (EXIF GPS + heading are the source of truth)
maps/            georeferenced historical maps (GeoTIFF, not in git — 1.3 GB)
assets/          presentation (pptx), its transcript and abstract
geodata/         raw Overpass / FlatGeobuf downloads cached by build_data.py (not in git)
```

## The page

- **Basemaps** — *relevo*: the Câmera Topográfica relief served live by
  `cameratopo.pedalhidrografi.co`, two layers with the same palette
  (`elevMin=720 elevMax=830`, one cycle): the ~5 m São Paulo DEM (`dem=sp`,
  transparent outside its footprint, which stops just north of the Colônia
  crater) over FABDEM 30 m (`dem=fabdem`). *satélite*: Esri World Imagery.
  *osm*: OpenStreetMap standard tiles. Relief tiles are render-on-demand and
  CDN-cached; a cold tile takes ~1–5 s and a burst of cold requests can
  come back as CDN error pages, so the sources are capped at their native
  zoom (13 / 15) and `python3 scripts/warm_relief.py` pre-warms the cache
  (window z9–13 + z14–15 around the anomalies) — run it after changing
  `RELIEF` in `site/index.html` or before showing the site.
- **Historical overlays** — `site/tiles/igc/` (IGC-SP sheets, 1895–1920,
  z7–14) and `site/tiles/sara1930/` (SARA Brasil 1930, z10–16), WebP XYZ
  tiles baked from `maps/` by `scripts/build_tiles.py`. Toggled with the
  *1895–1920* / *1930* buttons, with an opacity slider; on top of them the
  modern named rivers stay drawn in blue for comparison.
- **Anomalias** — `site/data/anomalias.json`, hand-edited: id, number, name,
  short label, kind (`antiga` = Ab'Sáber 1957, `nova`), position, the fly-to
  zoom and the narrative text. Rendered as numbered markers with a label, a
  left-rail index, and a card with the story and that anomaly's photos.
- **Fotos** — markers built from `site/data/media.json`; FOV cones are drawn
  for photos with a compass heading (`GPSImgDirection`), aperture from the
  35 mm-equivalent focal length. Each photo is attached to the nearest
  anomaly within 3 km (`anom` field).
- **Morros e Águas** — the Pedal Hidrográfico map layer (see
  amora.pedalhidrografi.co), read from the same FlatGeobuf the amora app
  uses (`south-america-hidro.fgb`): OSM `waterway=*` lines and
  `natural=ridge` crests, clipped to the window into
  `site/data/hidro.geojson` (k = river|stream|canal|ditch|drain|ridge, t = 1
  when culverted) and `hidro-names.geojson` (named rivers/canals/ridges
  merged per name, for labels). Same style as amora: rivers green
  `#A6C045`, streams/canals ochre `#DDB84F` (dashed in culverts), ridges
  orange `#EF7A30`. Always drawn, on every basemap and over the historical
  sheets. Reservoirs (OSM water polygons), main roads and railways only show
  on the plain relief. The map deliberately carries **no municipality or
  bairro names** — only serras, planalto, represas and the OSM hill/ridge
  names; the human place context lives in the anomaly cards.
  **3D terrain** comes from the public Mapzen/AWS terrarium tiles (no
  baking needed), exaggeration 1.6.
  Every camera animation passes `freezeElevation:true`: MapLibre 5.6 sets
  its terrain elevation freeze on each ease but only releases it with that
  option, and a fly that starts before the destination's terrain tile has
  loaded would otherwise pin the camera centre at sea level (markers on a
  hillside then project off-screen). Keep it if you touch `fly()`.
- **Apresentação** — `site/slides/s01..s12.jpg` + `site/apresentacao.pdf`,
  rendered from `assets/PPT Anomalias.pptx` (LibreOffice → PDF → `pdftoppm
  -r 110`; `gs -dPDFSETTINGS=/ebook` for the PDF). If the slide count
  changes, update `SN` in `site/index.html`.

Share links: `…/anomalias/#a/braco` opens an anomaly, `#m/<photo id>` a
photo, `#slides` the presentation.

## Updating content

1. Drop new photos into `images/raw/` (keep GPS EXIF — beware exports that
   strip it; Apple Photos "export unmodified original" keeps it).
2. `python3 scripts/build_data.py media` (needs exiftool + Pillow). Photos
   get ids like `braco-20250506-2215` (anomaly + timestamp) and are copied to
   `site/media/` (≤1600 px, orientation baked in) with 256 px square thumbs
   in `site/media/t/`.
3. Fill `title` and `text` for each item in `site/data/media.json`; override
   `anom` if the nearest-anomaly guess is wrong. These fields survive
   rebuilds (keyed by the source filename).
4. Edit the stories in `site/data/anomalias.json` directly.

The cartography steps (`hidro roads water places`) only need re-running if
the display window or the label list in `scripts/build_data.py` change;
the first run downloads (Overpass for roads/rail, the Pedal Hidrográfico
FlatGeobufs for hidro/water) and caches into `geodata/`.

### Historical map tiles

```
python3 scripts/build_tiles.py            # both; ~1 min, ~200 MB in site/tiles/
python3 scripts/build_tiles.py sara1930   # one of: igc, sara1930
```

Needs the GeoTIFFs in `maps/` (`folhas-rmsp.tif`, `sara1930.tif`) and GDAL
with the WEBP driver. Output is git-ignored but must be deployed with the
rest of `site/`.

## Preview and deploy

```
python3 -m http.server 8822 -d site    # → http://localhost:8822
```

Production is the `www.ecotono.xyz` Cloud Run server, which serves the
`ecotono-data` GCS bucket's `site/` directory as web root, so the page lives
at `ecotono.xyz/anomalias`. Deploy is the same rsync as ilhasdepedra
(`site/tiles` included, ~200 MB the first time, incremental afterwards):

```
gcloud storage rsync --recursive site gs://ecotono-data/site/anomalias
```

Content is live on the next request. **Caveat:** `gcs-push.sh --mirror` in
the www.ecotono.xyz repo mirrors *its* `site/` over the bucket and would
delete `site/anomalias` — re-run the rsync above after any `--mirror` push.
