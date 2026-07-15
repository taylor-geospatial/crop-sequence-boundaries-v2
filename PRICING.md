# Cost and runtime

This document separates measured runtime from modeled cloud cost. Runtime
comes from runs on the Taylor Geospatial RAILS cluster. AWS figures map those
resource requests to roughly comparable EC2 instances; they are estimates,
not measurements from an EC2 run.

## Measured runtime

The processing stages used Intel Xeon Platinum 8468 (Sapphire Rapids) nodes.

| Stage                       |        Wall time | Requested CPUs | Requested RAM | Output                                      |
| --------------------------- | ---------------: | -------------: | ------------: | ------------------------------------------- |
| `csb polygonize 2018 2025`  |         13.3 min |             32 |        256 GB | 405 tile Parquet files, about 15 M polygons |
| `csb postprocess 2018 2025` |         11.9 min |             32 |        256 GB | National and state GeoParquet files         |
| Parity preparation          |            2 min |             32 |        200 GB | Hilbert-sorted reference files              |
| PMTiles build               |           16 min |             64 |        400 GB | National PMTiles archive                    |
| 16-region parity sweep      |            4 min |             16 |        128 GB | Per-region IoU results                      |
| **Total**                   | **about 47 min** |              — |             — | —                                           |

Polygonization and postprocessing—the stages that generate the GeoParquet
dataset—take 25.2 minutes. Parity preparation, parity evaluation, and PMTiles
packaging are optional downstream stages. Input download, boundary preparation,
instance startup, and data upload are not included in these measurements.

## EC2 sizing assumptions

These are capacity matches, not hardware-equivalence claims. EC2 vCPUs are
hardware threads, and performance can differ from the RAILS nodes.

| Stage requirement | Modeled EC2 instance | vCPUs |     RAM |
| ----------------- | -------------------- | ----: | ------: |
| 32 CPUs / 256 GB  | `m7i.16xlarge`       |    64 | 256 GiB |
| 16 CPUs / 128 GB  | `m7i.8xlarge`        |    32 | 128 GiB |
| 64 CPUs / 400 GB  | `r7i.24xlarge`       |    96 | 768 GiB |

## AWS estimate

Spot prices vary by Availability Zone and over time. The rates below are the
lowest Linux prices shown for us-east-1 on 15 July 2026, not guaranteed launch
prices. AWS updates its [Spot pricing table](https://aws.amazon.com/ec2/spot/pricing/)
frequently; check it before running.

| Stage                      |        Wall time | Instance       | Spot price |  Estimated cost |
| -------------------------- | ---------------: | -------------- | ---------: | --------------: |
| Polygonize and postprocess |           25 min | `m7i.16xlarge` | $0.9089/hr |           $0.38 |
| PMTiles build              |           16 min | `r7i.24xlarge` | $1.9712/hr |           $0.53 |
| Parity preparation         |            2 min | `m7i.16xlarge` | $0.9089/hr |           $0.03 |
| Parity sweep               |            4 min | `m7i.8xlarge`  | $0.5774/hr |           $0.04 |
| **Compute estimate**       | **about 47 min** | —              |          — | **about $0.97** |

The core 25-minute GeoParquet build accounts for about $0.38 of that estimate.
The full $0.97 includes the optional PMTiles and parity stages.

The listed outputs occupy roughly 5 GB. At the us-east-1 S3 Standard rate of
$0.023 per GB-month, one month costs about $0.12. AWS includes the first
100 GB per month of internet data transfer out across its services, so one
2.5 GB download costs $0 if that allowance is otherwise unused. Data transfer
into AWS is free. See [AWS S3 pricing](https://aws.amazon.com/s3/pricing/).

Under those assumptions, the measured stages plus one month of S3 storage cost
about **$1.09**. Keeping about 5 GB in S3 for a full year brings the modeled
run-and-retain total to roughly **$2.40**. These figures exclude taxes and
small EBS, public IPv4, and request charges. A complete retry would add about
$0.97 in compute; that is a contingency, not a worst-case bound.

## Comparison with the published USDA workflow

Hunt et al. report that the original eight-year CONUS CSB build took about
five days on a 96-core AWS workstation. They do not report the instance type,
operating-system charge, purchase model, effective ArcGIS license cost, or
AWS bill. The estimates below are therefore public-list equivalents, not
USDA's documented spend.

The public ArcPy implementation uses ArcGIS Pro, which runs on Windows.
Its `Eliminate` operation [requires an Advanced
license](https://pro.arcgis.com/en/pro-app/latest/tool-reference/data-management/eliminate.htm).
Esri's current user-type model supplies ArcGIS Pro Advanced through
[Professional Plus](https://www.esri.com/en-us/arcgis/products/arcgis-online/buy).
The latest first-party public price list found, dated Q2 2025, lists
Professional Plus at
[$4,200 per year MSRP](https://www.esri.com/content/dam/esrisites/en-us/media/pdf/texas-dir-mpa-price-list.pdf).
Government contracts, enterprise agreements, and existing licenses can change
the effective price, so this is not treated as USDA's marginal cost.

As an illustrative 96-vCPU match, a `c6i.24xlarge` running license-included
Windows costs $8.496/hr on demand in us-east-1. At 120 hours, that is
$1,019.52. The lowest regional Windows Spot snapshot on 15 July 2026 was
$4.8246/hr, or $578.95 for an uninterrupted 120-hour run. Prices come from
AWS's [regional EC2 offer file](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json)
and [live Spot data](https://website.spot.ec2.aws.a2z.com/spot.json).

|                                 | Published USDA workflow |    This work |
| ------------------------------- | ----------------------: | -----------: |
| Wall time                       |                  120 hr |      0.42 hr |
| Reported/requested CPUs         |                      96 |           32 |
| Core-hours                      |                  11,520 |         13.4 |
| Illustrative on-demand EC2 cost |      $1,019.52, Windows | $1.35, Linux |
| Illustrative Spot EC2 cost      |        $578.95, Windows | $0.38, Linux |
| Required GIS license            |     ArcGIS Pro Advanced |         None |
| Output polygons                 |         fewer than 20 M |      15.30 M |
| Wall-time ratio                 |                       — |         287x |
| Core-hour ratio                 |                       — |         860x |

The runtime ratios compare this project's measurement with the published USDA
runtime. They are not a controlled same-hardware benchmark. The cost rows are
current list-price models on different operating systems and instance families,
not measured bills.

## Reproduce the modeled run

```bash
# Launch m7i.16xlarge Linux Spot capacity for the core pipeline.
# Size storage for the CDL inputs and intermediate outputs.
git clone https://github.com/taylor-geospatial/crop-sequence-boundaries-v2
cd crop-sequence-boundaries-v2
uv sync

uv run csb download 2018 2025 --workers 8
uv run csb build-boundaries
uv run csb run-all 2018 2025 --output data/output/conus

# Optional: use an instance with enough RAM for the measured PMTiles job.
# tippecanoe must be on PATH.
uv run csb pmtiles \
    -i data/output/conus/postprocess/2018_2025/national/CSB1825.parquet \
    -o data/output/conus/CSB1825.pmtiles
```

The cost table applies measured RAILS wall times to the listed EC2 rates. A
real EC2 validation run should precede any production budget.

## Sources

- Runtime logs: `data/logs/conus_81200.out`,
    `data/logs/pmtiles_81211.out`, `data/logs/conus_parity_81225.out`, and
    `data/logs/prep_parity_81221.out`
- USDA runtime and algorithm: [Hunt et al. (2024)](https://journals.sagepub.com/doi/full/10.3233/SJI-230078)
- USDA reference implementation:
    [USDA-REE-NASS/crop-sequence-boundaries](https://github.com/USDA-REE-NASS/crop-sequence-boundaries)
- AWS pricing: [Spot](https://aws.amazon.com/ec2/spot/pricing/),
    [S3](https://aws.amazon.com/s3/pricing/), and
    [EC2 On-Demand](https://aws.amazon.com/ec2/pricing/on-demand/)
- Esri licensing: [Eliminate](https://pro.arcgis.com/en/pro-app/latest/tool-reference/data-management/eliminate.htm)
    and [ArcGIS user types](https://www.esri.com/en-us/arcgis/products/arcgis-online/buy)
