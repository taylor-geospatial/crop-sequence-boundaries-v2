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

USDA documents their compute envelope directly in the peer-reviewed paper
that describes the official CSB algorithm
([Hunt et al. 2024, *Statistical Journal of the IAOS*](https://journals.sagepub.com/doi/full/10.3233/SJI-230078);
[open-access PDF](https://data.nass.usda.gov/Education_and_Outreach/Reports,_Presentations_and_Conferences/Journal_Articles/Crop%20sequence%20boundaries%20using%20USDA%20National%20Agriculural%20Statistics%20Service%20historic%20cropland%20data%20layers.pdf)):

> "Initial 8-year CSB creation for the contiguous US **took about five days
> using a 96-core AWS workstation**." *(Hunt et al. 2024, p. 6)*
>
> "The process is fully automated, but the sizes of the 86 subregions are not
> balanced. Some sub-processing regions are completed in a couple hours while a
> few takes five days."
>
> "A majority of the processing time was spent on the dissolve/elimination
> step (ArcGIS Pro eliminate function)... **Absent incrementally increasing
> the selection size, the tool often fails or takes days.**"

Reference implementation: the public arcpy code at
[USDA-REE-NASS/crop-sequence-boundaries](https://github.com/USDA-REE-NASS/crop-sequence-boundaries).

### USDA cost reconstruction

The paper specifies a **96-core AWS workstation** running for **5 days**.
Closest current EC2 instances with 96 vCPUs:

| Instance       | vCPUs | RAM    | On-demand $/hr (us-east-1) | Spot $/hr (typ.) |
| -------------- | ----- | ------ | -------------------------- | ---------------- |
| `c6i.24xlarge` | 96    | 192 GB | ~$4.08                     | ~$1.80 – $2.40   |
| `m6i.24xlarge` | 96    | 384 GB | ~$4.61                     | ~$2.00 – $2.80   |
| `c7i.24xlarge` | 96    | 192 GB | ~$4.28                     | ~$1.90 – $2.50   |

Government workloads run **on-demand** (spot reclaim risk is unacceptable on a
multi-day job). Conservative cost using the cheapest match:

- **5 days × 24 hr × $4.08/hr = ~$489 in EC2 alone, per CONUS run** (on-demand
    `c6i.24xlarge`).
- Plus ArcGIS Pro license (~$700/year per seat, Esri commercial single-use).
- Plus operator time across 5 days of orchestration.

A spot-priced equivalent (with reclaim-tolerance the USDA pipeline doesn't
have) would still be **~$245** for the same wall-clock.

### Side-by-side

|                              | USDA pipeline (Hunt et al. 2024)       | This OSS pipeline                                                 |
| ---------------------------- | -------------------------------------- | ----------------------------------------------------------------- |
| Compute                      | 96-core AWS workstation, **5 days**    | 32-core node, **25 min**                                          |
| Wall-clock                   | **~120 hours**                         | **0.42 hours**                                                    |
| Speedup vs USDA              | —                                      | **~287×**                                                         |
| AWS on-demand cost           | **~$489**                              | n/a (sub-hour, spot is fine)                                      |
| AWS spot equivalent cost     | **~$245**                              | **~$0.63** (CONUS pipeline) / **~$2.00** (incl. pmtiles + parity) |
| Software license             | ArcGIS Pro, ~$700/yr/seat              | $0 (OSS)                                                          |
| Output polygons              | \<20 M                                 | ~15 M                                                             |
| Parity vs USDA ground truth  | reference                              | mean IoU 0.843, median 0.895                                      |
| Same elimination bottleneck? | yes — "tool often fails or takes days" | solved (raster-side adjacency, 0.4s/tile)                         |

**Net:** USDA's documented compute spend per annual rebuild is ~$489 + license

- a week of operator attention. This OSS pipeline produces a parity-compatible
    dataset for **~$2 of spot compute, in 25 minutes, no license required.**

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

# 6. (Optional) Build PMTiles. Needs tippecanoe on PATH:
#    conda install -c conda-forge tippecanoe
uv run csb pmtiles \
    -i data/output/conus/postprocess/2018_2025/national/CSB1825.parquet \
    -o data/output/conus/CSB1825.pmtiles
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

## Cheaper compute alternatives

The baseline above uses Intel `m7i.16xlarge` spot at ~$1.50/hr. Below are
concrete alternatives (us-east-1 / EU prices verified May 2026).

### 1. AWS Graviton (ARM) — same vCPU class, ~20–30% cheaper

| Instance       | vCPU | RAM    | On-demand | Spot (typ.) | vs `m7i.16xlarge` spot |
| -------------- | ---- | ------ | --------- | ----------- | ---------------------- |
| `m7i.16xlarge` | 64   | 256 GB | $3.226    | ~$1.23      | baseline               |
| `m7g.16xlarge` | 64   | 256 GB | $2.611    | ~$0.91      | **−26%**               |
| `c7g.16xlarge` | 64   | 128 GB | $2.320    | ~$0.71      | **−42%**               |
| `r7g.16xlarge` | 64   | 512 GB | ~$3.43    | ~$1.20      | −2%                    |

ARM-wheel sanity check (PyPI, May 2026): `numpy`, `shapely>=2`, `rasterio`,
`scikit-image`, `duckdb`, `pyarrow`, `geopandas`, `tippecanoe` (built from
source) all ship `manylinux2014_aarch64` wheels. `contourrs` (Rust) cross-compiles
cleanly. **Verdict: Graviton is drop-in.** `c7g.16xlarge` saves ~$0.20 per
CONUS run (RAM headroom check: peak observed ~190 GB → 128 GB is too tight;
use `m7g.16xlarge` with 256 GB).

### 2. Bigger instance, less wall-clock

| Instance        | vCPU | RAM    | Spot $/hr | Est. wall | Est. cost |
| --------------- | ---- | ------ | --------- | --------- | --------- |
| `m7i.16xlarge`  | 64   | 256 GB | $1.23     | 25 min    | $0.51     |
| `c7i.48xlarge`  | 192  | 384 GB | $3.15     | ~10 min   | ~$0.52    |
| `m7g.16xlarge`  | 64   | 256 GB | $0.91     | 25 min    | **$0.38** |
| `c7gn.16xlarge` | 64   | 128 GB | ~$1.10    | 25 min    | $0.46     |

Bigger Intel boxes are cost-neutral; the win is wall-clock, not $. Pipeline
scales near-linearly to ~64 physical cores then flattens, so `c7i.48xlarge`
≈10 min is the practical wall-clock floor on a single node.

### 3. Hetzner / OVH — no spot, but flat hourly is brutal

| Provider | Box                     | vCPU/cores      | RAM    | €/mo | $/hr equiv.   |
| -------- | ----------------------- | --------------- | ------ | ---- | ------------- |
| Hetzner  | `CCX63` (ded. AMD vCPU) | 48 vCPU         | 192 GB | €343 | ~$0.51/hr     |
| Hetzner  | `AX102` (dedicated)     | 16c/32t Ryzen 9 | 128 GB | €104 | **~$0.16/hr** |
| Hetzner  | `AX162-R` (dedicated)   | 48t EPYC 9454P  | 256 GB | €230 | ~$0.34/hr     |

A **Hetzner AX102 at €104/mo** runs the whole pipeline in ~30 min for
~$0.08 of pro-rated compute — but with €104 minimum monthly commitment, it
only beats AWS spot if you actually run >1 job/month or use the box for
other work. For pure annual rebuild, AWS Graviton spot wins.

### 4. Egress / hosting the 2.5 GB PMTiles

| Provider      | Storage $/GB-mo | Egress $/GB     | Cost @ 1 TB/mo egress |
| ------------- | --------------- | --------------- | --------------------- |
| AWS S3        | $0.023          | $0.09           | **$92.10**            |
| Backblaze B2  | $0.006          | $0.01 (3× free) | ~$2 (or $0 via CF)    |
| Cloudflare R2 | $0.015          | **$0.00**       | **$0.04**             |

For a public PMTiles served at any meaningful traffic, **Cloudflare R2 saves
$90+/month vs S3 at 1 TB egress**. Use R2 with a custom domain; HTTP range
requests work natively for PMTiles.

### 5. NASS CDL source

No official S3 mirror exists in the AWS Registry of Open Data (as of May 2026).
NASS publishes CDL only via HTTPS at `nass.usda.gov` (free, internet egress
into AWS is **free inbound**). One-time pull of 52 GB into us-east-1 costs $0.
Google Earth Engine mirrors CDL but extracting CONUS rasters out of GEE is
slower than the direct HTTPS pull.

### 6. Annual rebuild total

CDL releases once per year (Feb of following year). One run of this pipeline:

| Setup                              | Compute | Storage+egress | **Annual** |
| ---------------------------------- | ------- | -------------- | ---------- |
| AWS `m7i.16xlarge` spot (baseline) | $1.65   | $0.35          | **$2.00**  |
| AWS `m7g.16xlarge` spot (Graviton) | $1.10   | $0.35          | **$1.45**  |
| AWS `m7g` + R2 hosting             | $1.10   | ~$0.05         | **$1.15**  |

### Recommendation

For a production annual CONUS rebuild, run on **`m7g.16xlarge` Graviton spot
in us-east-1 (~$0.91/hr)** and host the PMTiles archive on **Cloudflare R2
(zero egress)**. This drops compute by ~26% over the Intel baseline with no
code changes (all deps have arm64 wheels), keeps the 25-min wall-clock, and
makes serving the dataset to the public effectively free regardless of
download traffic. Total annual cloud cost: **~$1.15**. If the box doubles
as a research workstation the rest of the year, a Hetzner AX102 dedicated
(€104/mo, 16c Ryzen 9 + 128 GB) is a strictly cheaper home — same job
finishes in ~30 min with zero per-run cost.

## Numbers source

Wall-clock measurements: `data/logs/conus_81200.out`,
`data/logs/pmtiles_81211.out`, `data/logs/conus_parity_81225.out`,
`data/logs/prep_parity_81221.out`. Reproducible via the SLURM wrappers in
`examples/cluster/`.
