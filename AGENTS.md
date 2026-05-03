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
csb build-boundaries # TIGER + NASS county/ASD GeoParquet
csb polygonize       # CDL → connected components → eliminate → simplify
csb postprocess      # spatial-join + state split → GeoParquet
csb run-all          # polygonize + postprocess
csb parity-prep      # Hilbert-sort + bbox cols for fast parity queries
csb parity           # 16-region IoU vs USDA ground truth
csb pmtiles          # GeoParquet → FlatGeobuf → tippecanoe → .pmtiles
```

## Module map

| Module                | Role                                                                             |
| --------------------- | -------------------------------------------------------------------------------- |
| `cli.py`              | Click command group                                                              |
| `polygonize.py`       | Two-phase tiled raster → polygon driver (streaming pool)                         |
| `postprocess.py`      | Boundary join, CSBID/CSBACRES/INSIDE_X,Y, state split                            |
| `raster_eliminate.py` | Label-raster connected components + neighbor adjacency + union-find merge passes |
| `download.py`         | Parallel CDL fetch from NASS                                                     |
| `boundaries.py`       | TIGER + NASS county/ASD crosswalk                                                |
| `parity.py`           | USDA ground-truth IoU validation                                                 |
| `pmtiles.py`          | GeoParquet → FlatGeobuf → tippecanoe                                             |
| `io.py`               | GeoParquet 1.1 writer (full PROJJSON CRS)                                        |
| `config.py`           | YAML config + constants (`STATE_FIPS`, `BARREN_CODE`, …)                         |
| `utils.py`            | `polygonize` wrapper, `parallel_map`/`parallel_starmap`                          |

## Conventions

- CRS is fixed to `EPSG:5070` (NAD83 / Conus Albers) throughout.
- Outputs are GeoParquet 1.1 with full-PROJJSON CRS metadata; the short
    `{id: {authority, code}}` form is rejected by pyproj 3.x and breaks
    geopandas / pyogrio / GDAL readers.
- Parallel stages use `ProcessPoolExecutor` with `cpu_fraction` from config.
- Each stage is resumable — completed area tiles are skipped automatically.
- Tests live in `tests/`; pytest with xdist available.
- Lint/format: ruff (line-length 100); type-check: ty; pre-commit covers both
    plus mdformat and pyproject-fmt.
- Commits follow Conventional Commits (`feat|fix|refactor|chore|docs|...`).
- The bundled default config lives at `src/csb/_data/default.yaml` and is
    resolved via `importlib.resources` so it travels with the wheel.

## Build & test

```bash
make install   # uv sync --all-extras + console script
make check     # pre-commit
make test      # pytest --cov
make build     # uv build (sdist + wheel)
```
