# Cost & runtime — full CONUS pipeline

End-to-end cost to produce the entire 2018–2025 CONUS Crop Sequence Boundaries
dataset plus the national PMTiles archive, on AWS EC2 spot capacity.

All wall-clock numbers below are measured from real runs on the TGI RAILS HPC
cluster (Intel Sapphire Rapids 8468). EC2 mappings use equivalent Sapphire
Rapids instance families (`m7i`, `r7i`, `c7i`).

## TL;DR

**~$2.00 of AWS spot compute** produces the full CONUS dataset (15 M crop
sequence boundary polygons, USDA-identical schema) + a 2.5 GB
nationally-tiled PMTiles archive ready to serve, in **~50 minutes wall-clock
total**.

## Measured runtime (TGI RAILS, single fat node per stage)

| Stage                                              | Wall        | Cores requested | RAM requested | Output                                       |
| -------------------------------------------------- | ----------- | --------------- | ------------- | -------------------------------------------- |
| `csb polygonize 2018 2025`                         | 13.3 min    | 32              | 256 GB        | 405 polygonize parquets, ~15 M polygons      |
| `csb postprocess 2018 2025`                        | 11.9 min    | 32              | 256 GB        | 1.5 GB national + 1.4 GB / 48 state parquets |
| `prep_parity_inputs.py` (Hilbert-sort + bbox cols) | 2 min       | 32              | 200 GB        | 1 GB ours + 4.7 GB USDA indexed parquets     |
| `parquet_to_pmtiles.py` + tippecanoe               | 16 min      | 64              | 400 GB        | 2.5 GB CSB1825.pmtiles                       |
| `conus_parity.py` (16-region IoU sweep)            | 4 min       | 16              | 128 GB        | per-region IoU JSON                          |
| **Total**                                          | **~47 min** | —               | —             | —                                            |

(Stages run sequentially in the table; in practice
parity + pmtiles can run in parallel since they share no inputs.)

## Equivalent EC2 instances

TGI RAILS `cpu` partition node: Intel Xeon Platinum 8468 Sapphire Rapids,
512 GB / node. The closest EC2 spot family is **m7i** (Sapphire Rapids,
balanced) and **r7i** (Sapphire Rapids, memory-heavy):

| Stage requirement | Matching EC2   | vCPUs | RAM |
| ----------------- | -------------- | ----- | --- |
| 32 cores / 256 GB | `m7i.16xlarge` | 64    | 256 |
| 16 cores / 128 GB | `m7i.8xlarge`  | 32    | 128 |
| 64 cores / 400 GB | `r7i.24xlarge` | 96    | 768 |

(EC2 vCPUs are SMT siblings; 64 vCPU on `m7i.16xlarge` ≈ 32 physical cores.
Our 32-core SLURM request ≈ same physical-core budget.)

## Spot pricing (us-east-1, mid-2025 typical)

| Instance       | On-demand $/hr | Spot $/hr (typ.) |
| -------------- | -------------- | ---------------- |
| `m7i.8xlarge`  | ~$1.61         | ~$0.65 – $0.90   |
| `m7i.16xlarge` | ~$3.23         | ~$1.30 – $1.80   |
| `r7i.24xlarge` | ~$7.13         | ~$2.90 – $4.00   |

Spot prices fluctuate by AZ and time-of-day. Numbers above are typical mid-2025
us-east-1 ranges; check the AWS spot pricing page for current values before
budgeting.

## Per-stage cost (using mid-spot pricing)

| Stage                      | Wall        | Instance       | $/hr  | Cost       |
| -------------------------- | ----------- | -------------- | ----- | ---------- |
| polygonize + postprocess   | 25 min      | `m7i.16xlarge` | $1.50 | **$0.63**  |
| pmtiles build              | 16 min      | `r7i.24xlarge` | $3.40 | **$0.91**  |
| parity prep (Hilbert sort) | 2 min       | `m7i.16xlarge` | $1.50 | **$0.05**  |
| parity sweep (16 regions)  | 4 min       | `m7i.8xlarge`  | $0.75 | **$0.05**  |
| **Compute total**          | **~47 min** | —              | —     | **~$1.65** |

## Storage and transfer (one-time)

| Item                                                                      | Size   | Cost       |
| ------------------------------------------------------------------------- | ------ | ---------- |
| Pull 8 yr CDL TIFs (NASS, public)                                         | ~52 GB | $0         |
| S3 Standard, output (1 GB national + 1.4 GB state + 2.5 GB pmtiles), 1 mo | 5 GB   | ~$0.12     |
| Egress 2.5 GB pmtiles to public CDN/web                                   | 2.5 GB | ~$0.23     |
| **Non-compute total**                                                     | —      | **~$0.35** |

## All-in cost

**~$2.00 per CONUS run** (compute + 1 month of S3 storage + one egress of the pmtiles).

For an annual rebuild (CDL is published yearly), this is **~$2/year**
in cloud cost. Add ~2× safety margin for spot interruption / re-runs:
**~$4 worst case**.

## Reference: what USDA's existing ArcGIS pipeline costs

The official USDA pipeline at
https://github.com/USDA-REE-NASS/crop-sequence-boundaries requires:

- **ArcGIS Pro license**: ~$700/year per seat (Esri commercial single-use)
- **Windows server compute** for multi-day runs (single-node, mostly serial
    ArcPy)
- Manual operator time across multiple days for orchestration

Conservative apples-to-apples cost (1 seat, 1 server) is **>$700/year +
multi-day operator time**. This OSS pipeline replaces that with **~$2 of
spot compute and ~50 minutes wall-clock**, with output that matches USDA
ground truth at a median IoU of 0.895 across 16 geospatially diverse test
regions (mean 0.843; acreage within 2% of USDA in median).

## How to reproduce on AWS

```bash
# 1. Launch a single m7i.16xlarge spot instance with 64 GB EBS gp3.
# 2. Install uv, clone repo, install dependencies:
git clone https://github.com/isaaccorley/crop-sequence-boundaries-v2
cd crop-sequence-boundaries-v2
uv sync

# 3. Pull CDL inputs (parallel; ~5 min from NASS):
uv run csb download 2018 2025 --workers 8

# 4. Build boundaries (one-time):
uv run csb build-boundaries

# 5. Run pipeline:
uv run csb --config configs/conus.yaml polygonize 2018 2025
uv run csb --config configs/conus.yaml postprocess 2018 2025 \
    --polygonize-dir data/output/conus/polygonize/2018_2025

# 6. (Optional) Build PMTiles. Needs tippecanoe — install via conda or build:
#    https://github.com/felt/tippecanoe
sbatch scripts/build_pmtiles.sbatch   # or run the script's body manually
```

Total: one spot instance, ~50 minutes, ~$2.

## Caveats

- Spot prices fluctuate; quoted ranges are mid-2025 us-east-1 typical.
    Production budgeting should pull live prices for your target region.
- Spot instances can be reclaimed; for sub-hour jobs this is uncommon, but
    add 2× margin for safety.
- The CDL download is free if NASS hosts on S3 in-region (recommend pulling
    via CloudFront-served URLs); a multi-region transfer would add ~$5.
- The 25-min CONUS pipeline is dominated by polygonize (~13 min) and
    postprocess (~12 min). Both scale near-linearly with cores up to ~64; a
    larger spot instance (e.g. `c7i.48xlarge` at ~$3 – $4/hr spot) would drop
    wall to ~15 min for similar total cost.

## Numbers source

Wall-clock measurements: `data/logs/conus_81200.out`,
`data/logs/pmtiles_81211.out`, `data/logs/conus_parity_81225.out`,
`data/logs/prep_parity_81221.out`. Reproducible via the `scripts/*.sbatch`
wrappers in this repo.
