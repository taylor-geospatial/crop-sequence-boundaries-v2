# csb — Crop Sequence Boundaries

Open-source pipeline that turns USDA Cropland Data Layer rasters into
field-level crop sequence boundary polygons. A drop-in replacement for the
official ArcPy CSB pipeline at
[USDA-REE-NASS/crop-sequence-boundaries](https://github.com/USDA-REE-NASS/crop-sequence-boundaries):

- **No ArcGIS license required.** Pure-Python + a few Rust/C extensions.
- **~25 minutes** for the full 8-year CONUS rebuild on a single 32-core node
    (USDA's published runtime: 5 days on a 96-core AWS workstation —
    [Hunt et al. 2024](https://journals.sagepub.com/doi/full/10.3233/SJI-230078)).
- **~$2 of AWS spot compute** per CONUS run. See [PRICING.md](PRICING.md).
- **USDA-identical output schema** (`CSBID`, `CSBYEARS`, `CSBACRES`,
    `CDL{year}`, `STATEFIPS`, `STATEASD`, `ASD`, `CNTY`, `CNTYFIPS`,
    `INSIDE_X/Y`, `Shape_Length`, `Shape_area`).
- **Mean IoU 0.843, median 0.895** vs USDA ground truth across 16
    geospatially diverse test tiles; acreage match within 2% in median.

## Install

```bash
pip install csb
```

For development:

```bash
git clone https://github.com/isaaccorley/crop-sequence-boundaries-v2
cd crop-sequence-boundaries-v2
uv sync --all-extras
```

The `csb` console script is installed automatically.

## Quickstart

```bash
# 1. Pull the inputs (parallel; ~5 min from NASS over a fast pipe).
csb download 2018 2025 --workers 8

# 2. Build the county/ASD boundary file (one-time, ~30s).
csb build-boundaries

# 3. Run the full pipeline.
csb run-all 2018 2025
```

Output: a national GeoParquet plus 48 per-state GeoParquets at
`data/output/postprocess/2018_2025/`.

## Pipeline

```text
┌──────────┐   ┌──────────────────┐   ┌─────────────┐   ┌─────────────┐
│ download │──▸│ build-boundaries │──▸│  polygonize │──▸│ postprocess │
└──────────┘   └──────────────────┘   └─────────────┘   └─────────────┘
   CDL TIFs        ASD/county GeoParquet      raster→polygon         per-state
                                              eliminate +            GeoParquets
                                              simplify
```

| Stage              | What it does                                                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `download`         | Fetch USDA national CDL rasters in parallel from NASS.                                                                            |
| `build-boundaries` | Build the CONUS county+ASD boundary GeoParquet from Census TIGER + NASS crosswalk.                                                |
| `polygonize`       | Combine multi-year CDL → label connected components → multi-pass label-raster elimination → coverage simplify → tiled GeoParquet. |
| `postprocess`      | Spatial-join to county/ASD (largest overlap), derive `CSBID`/`CSBACRES`/`INSIDE_X,Y`, write national + per-state GeoParquets.     |
| `run-all`          | `polygonize` then `postprocess` back-to-back.                                                                                     |

Two extra commands handle validation and serving:

| Stage         | What it does                                                                              |
| ------------- | ----------------------------------------------------------------------------------------- |
| `parity-prep` | Hilbert-sort + add bbox columns to ours and USDA parquets so DuckDB can prune row groups. |
| `parity`      | 16-region IoU/acreage validation vs USDA ground truth.                                    |
| `pmtiles`     | Build a CONUS PMTiles archive from the national parquet (requires `tippecanoe` on PATH).  |

## Configuration

CSB ships with a default YAML config; override paths or thresholds via:

```bash
csb --config configs/conus.yaml run-all 2018 2025
```

```yaml
# configs/conus.yaml
global:
  cpu_fraction: 0.95
  min_cropland_years: 2

paths:
  output: data/output/conus
  national_cdl: data/input/national_cdl
  boundaries: data/input/boundaries/US48_ASD_CNTY_Albers.parquet

polygonize:
  phase1_workers: 16
  phase2_workers: 16
  tile_size: 5000
  eliminate_thresholds: [100, 1000, 10000, 10000]   # matches USDA CSBElimination
  min_polygon_area: 10000
  simplify_tolerance: 60
```

## Output schema

Identical to USDA CSB:

| Column                       | Type         | Description                                            |
| ---------------------------- | ------------ | ------------------------------------------------------ |
| `CSBID`                      | text(15)     | `STATEFIPS + CSBYEARS + zfill(OBJECTID, 9)`            |
| `CSBYEARS`                   | text(4)      | e.g. `1825` for 2018–2025                              |
| `CSBACRES`                   | float64      | polygon area in acres                                  |
| `CDL2018`..`CDL2025`         | int32        | dominant CDL class per year (0 for non-cropland years) |
| `STATEFIPS`                  | text(2)      | state FIPS code                                        |
| `STATEASD`                   | text(10)     | state + agricultural statistics district               |
| `ASD`                        | text(2)      | ASD within state                                       |
| `CNTY`                       | text         | county name                                            |
| `CNTYFIPS`                   | text(3)      | county FIPS code                                       |
| `INSIDE_X`, `INSIDE_Y`       | float64      | EPSG:5070 coordinates of a guaranteed-interior point   |
| `Shape_area`, `Shape_Length` | float64      | EPSG:5070 area / perimeter                             |
| `geometry`                   | binary (WKB) | polygon, EPSG:5070                                     |

## Parity vs USDA ground truth

Across 16 geospatially diverse 5000² test tiles (Iowa corn belt, Texas
panhandle, Mississippi delta, Imperial Valley, Palouse, Snake River,
Wisconsin dairy belt, Delmarva, …):

| metric                    | mean  | median | min   | max   |
| ------------------------- | ----- | ------ | ----- | ----- |
| IoU                       | 0.843 | 0.895  | 0.527 | 0.934 |
| polygon ratio (ours/USDA) | 1.02  | 0.91   | 0.47  | 1.57  |
| acres ratio               | 0.95  | 0.98   | 0.68  | 1.01  |

To reproduce:

```bash
csb parity-prep \
    --ours data/output/conus/postprocess/2018_2025/national/CSB1825.parquet \
    --ours-out data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet \
    --usda-gdb data/CSB1825.gdb \
    --usda-out data/CSB1825_indexed.parquet

csb parity \
    --ours data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet \
    --usda data/CSB1825_indexed.parquet \
    --report data/profile/parity.json
```

## Cluster runs

SLURM submission scripts for HPC sites are in
[`examples/cluster/`](examples/cluster). Each defaults to a single fat node
sized for the CONUS dataset; adjust `--account` and `--partition` for your
site.

```bash
sbatch examples/cluster/conus_run.sbatch
sbatch examples/cluster/build_pmtiles.sbatch
```

## Stack

| Component                                                                                                                                                | Used for                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| [`rasterio`](https://github.com/rasterio/rasterio)                                                                                                       | Windowed CDL reads                                      |
| [`scikit-image`](https://github.com/scikit-image/scikit-image)                                                                                           | Connected-components labelling                          |
| [`contourrs`](https://github.com/cubao/contourrs)                                                                                                        | Rust-backed raster → polygon                            |
| [`shapely`](https://github.com/shapely/shapely)                                                                                                          | `coverage_simplify` (analogue of arcpy `BEND_SIMPLIFY`) |
| [`duckdb`](https://github.com/duckdb/duckdb) (+ spatial extension)                                                                                       | Spatial joins, reads, GeoParquet output                 |
| [`pyarrow`](https://github.com/apache/arrow) / [`geopandas`](https://github.com/geopandas/geopandas) / [`pyogrio`](https://github.com/geopandas/pyogrio) | Columnar I/O                                            |
| [`tippecanoe`](https://github.com/felt/tippecanoe)                                                                                                       | PMTiles build (optional)                                |

CRS is fixed to `EPSG:5070` throughout. Outputs are GeoParquet 1.1 at every
stage.

## Cost

A full annual CONUS rebuild costs **~$1.15 to $2.00 of AWS spot compute**
(plus near-zero storage on Cloudflare R2). Detailed pricing breakdown,
hardware comparisons, and a side-by-side vs USDA's ArcGIS pipeline are in
[PRICING.md](PRICING.md).

## Data hosting

Generated CSB datasets are published on **[Source Cooperative](https://source.coop)**
under a public bucket. Source Coop offers fast HTTP range-request access
(better than Zenodo for large geospatial archives) and a stable URL per
release. Each archive corresponds to a `vX.Y.Z` GitHub Release on this
repo — the GitHub release notes are the canonical changelog and the
Source Cooperative bucket holds the GeoParquet + PMTiles artifacts.

Cite the dataset via the [CITATION.cff](CITATION.cff) (software) and the
technical report in [`paper/`](paper/) (methodology).

## Development

```bash
make install      # uv sync --all-extras + console script
make check        # pre-commit: ruff, ruff-format, ty, mdformat, …
make test         # pytest with coverage
make build        # build sdist + wheel
```

## License

Apache-2.0. See [LICENSE](LICENSE).
