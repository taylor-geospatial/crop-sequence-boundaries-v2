# AGENTS.md

Notes for coding agents working in this repo.

## What this is

`csb` is an open-source pipeline that turns USDA Cropland Data Layer rasters
into Crop Sequence Boundary polygons. Drop-in replacement for the
[USDA-REE-NASS arcpy pipeline](https://github.com/USDA-REE-NASS/crop-sequence-boundaries),
USDA-identical output schema, no ArcGIS license. See [`README.md`](README.md)
for the user-facing overview and [`PRICING.md`](PRICING.md) for cost.

## CLI shape

```
csb download         # NASS CDL rasters (parallel)
csb roads-prep       # TIGER/Overture road+rail lines → mask GeoParquet
csb build-boundaries # TIGER + NASS county/ASD GeoParquet
csb polygonize       # CDL → reclass/denoise → components → eliminate → simplify
csb postprocess      # spatial-join + state split → GeoParquet
csb run-all          # polygonize + postprocess
csb parity-prep      # Hilbert-sort + bbox cols for fast parity queries
csb parity           # 16-region (or --whole-conus) IoU vs USDA ground truth
csb object-eval      # per-USDA-polygon best-match IoU distribution
csb instance-metrics # panoptic quality (PQ/RQ/SQ) + boundary error
csb visualize        # 4-panel coverage diff plot for a window
csb pmtiles          # GeoParquet → FlatGeobuf → tippecanoe → .pmtiles
csb bench-eliminate  # raster vs DuckDB polygon-side eliminate benchmark
csb tile-sweep       # single-tile parameter sweep scored vs USDA
```

## Module map

| Module                 | Role                                                                             |
| ---------------------- | -------------------------------------------------------------------------------- |
| `cli/`                 | Click command group (see `cli/_group.py`)                                        |
| `polygonize.py`        | Two-phase tiled raster → polygon driver (streaming pool)                         |
| `postprocess.py`       | Boundary join, CSBID/CSBACRES/INSIDE_X,Y, state split                            |
| `raster_eliminate.py`  | Label-raster connected components + neighbor adjacency + union-find merge passes |
| `download.py`          | Parallel CDL fetch from NASS                                                     |
| `boundaries.py`        | TIGER + NASS county/ASD crosswalk                                                |
| `parity.py`            | USDA ground-truth IoU validation                                                 |
| `reclass.py`           | USDA CDL temp-general-code reclass table (bundled CSV)                           |
| `usda_filter.py`       | USDA production noise filter (RegionGroup/Con/Shrink port)                       |
| `roads.py`             | TIGER/Overture road+rail rasterized mask                                         |
| `tiger.py`             | Census TIGER road/rail shapefile fetcher (`csb roads-prep --source tiger`)       |
| `focal.py`             | Legacy GEE-style focal denoise baseline (off by default)                         |
| `instance_metrics.py`  | Panoptic quality, per-threshold F1, boundary error                               |
| `object_eval.py`       | Directional best-match IoU per USDA polygon                                      |
| `bench.py`             | Subprocess-isolated eliminate benchmark harness                                  |
| `polygon_eliminate.py` | DuckDB polygon-side eliminate (documented baseline, not production)              |
| `tile_experiment.py`   | Single-tile parameter sweep diagnostic                                           |
| `visualize.py`         | Matplotlib coverage-diff panels                                                  |
| `pmtiles.py`           | GeoParquet → FlatGeobuf → tippecanoe                                             |
| `io.py`                | GeoParquet 1.1 writer (full PROJJSON CRS)                                        |
| `config.py`            | CLI defaults + constants (`STATE_FIPS`, `BARREN_CODE`, …)                        |
| `utils.py`             | `polygonize` wrapper, `parallel_map`/`parallel_starmap`                          |

## Conventions

- CRS is fixed to `EPSG:5070` (NAD83 / Conus Albers) throughout.
- Outputs are GeoParquet 1.1 with full-PROJJSON CRS metadata; the short
    `{id: {authority, code}}` form is rejected by pyproj 3.x and breaks
    geopandas / pyogrio / GDAL readers.
- Parallel stages use `ProcessPoolExecutor` with `cpu_fraction` from the CLI/API.
- Each stage is resumable — completed area tiles are skipped automatically.
- Tests live in `tests/`; pytest with xdist available.
- Lint/format: ruff (line-length 100); type-check: ty; pre-commit covers both
    plus mdformat and pyproject-fmt.
- Commits follow Conventional Commits (`feat|fix|refactor|chore|docs|...`).
- Defaults live in `src/csb/config.py`; each is exposed as a CLI option and a
    public Python API keyword argument.

## Build & test

```bash
make install   # uv sync --all-extras + console script
make check     # pre-commit
make test      # pytest --cov
make build     # uv build (sdist + wheel)
```
