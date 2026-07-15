"""Overture-based road and rail mask for cropland-mask preprocessing.

USDA's CSB pipeline filters CDL noise in Google Earth Engine and re-imposes
the road/rail network so adjacent fields with identical crop sequences but
separated by a road do not merge into a single polygon (Hunt et al. 2024).
We reproduce the same effect using the Overture Maps transportation theme
(segments classified as motorways through tertiary roads, plus rail), which
is published to public S3 in GeoParquet — no API key, no rate limits, AWS
in-region inbound is free.

Two-step usage:

1. Once per release: ``csb roads-prep`` downloads + reprojects + buffers the
   road/rail centerlines for CONUS into a single GeoParquet. ~150 MB.
2. During polygonize, pass ``--roads-mask <path>`` and each tile's cropland
   mask is AND'd with the rasterized road exclusion before
   connected-components labeling.

Without ``--roads-mask`` the behavior is unchanged.
"""

import logging
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio.features
import shapely

logger = logging.getLogger(__name__)

# Pinned Overture release. Update when a newer release is desired; the
# schema is stable across recent releases per the Overture spec.
DEFAULT_OVERTURE_RELEASE = "2026-04-15.0"

# Road classes worth preserving as field separators. We exclude footways /
# cycleways / paths (too thin to matter at 30m) but keep everything from
# motorway down through tertiary plus all rail.
_ROAD_CLASSES = (
    "motorway",
    "primary",
    "secondary",
    "tertiary",
    "trunk",
)
_RAIL_SUBTYPE = "rail"

# CONUS bbox in EPSG:4326 (WGS-84). Wide enough to cover Alaska / Hawaii too,
# but the rasterize step crops to the CDL grid which is CONUS-only.
CONUS_4326 = (-125.0, 24.0, -66.5, 50.0)

# Buffer applied to road centerlines before rasterization. ~15m = half a CDL
# pixel; ensures the centerline reliably falls in at least one pixel after
# burning. Rail typically has a wider right-of-way; we use the same value
# for simplicity.
DEFAULT_BUFFER_M = 15.0


def fetch_overture_roads(
    output: Path,
    *,
    release: str = DEFAULT_OVERTURE_RELEASE,
    bbox_4326: tuple[float, float, float, float] = CONUS_4326,
    buffer_m: float = DEFAULT_BUFFER_M,
    threads: int = 16,
) -> Path:
    """Download road + rail segments from Overture S3 and write a GeoParquet.

    Output schema: ``(geometry: BLOB, kind: VARCHAR)``. Geometry is the
    centerline in EPSG:5070, buffered by ``buffer_m`` metres so it
    rasterizes to at least one pixel.
    """
    bx0, by0, bx1, by1 = bbox_4326
    classes = ", ".join(f"'{c}'" for c in _ROAD_CLASSES)

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    conn.install_extension("httpfs")
    conn.load_extension("httpfs")
    conn.execute(f"PRAGMA threads={threads}")

    overture_root = (
        f"s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment/*"
    )
    logger.info("downloading Overture transportation segments (release %s)", release)
    conn.execute("SET s3_region='us-west-2'")
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE roads_4326 AS
        SELECT
            geometry AS geom,
            CASE
                WHEN subtype = 'rail' THEN 'rail'
                ELSE class
            END AS kind
        FROM read_parquet('{overture_root}', filename=true, hive_partitioning=1)
        WHERE
            bbox.xmin >= {bx0} AND bbox.xmax <= {bx1}
            AND bbox.ymin >= {by0} AND bbox.ymax <= {by1}
            AND (
                (subtype = 'road' AND class IN ({classes}))
                OR subtype = '{_RAIL_SUBTYPE}'
            )
    """)
    n = conn.execute("SELECT COUNT(*) FROM roads_4326").fetchone()
    assert n is not None
    logger.info("  %s segments before reproject + buffer", n[0])

    output.parent.mkdir(parents=True, exist_ok=True)
    # DuckDB spatial 1.5.0 segfaults on ST_Buffer of transformed lines, so we
    # only do reprojection in DuckDB and buffer in shapely.
    rows = conn.execute("""
        SELECT ST_AsWKB(ST_Transform(geom, 'EPSG:4326', 'EPSG:5070', always_xy => true)) AS wkb, kind
        FROM roads_4326
    """).fetchall()
    conn.close()

    geoms = shapely.from_wkb([bytes(r[0]) for r in rows])
    kinds = [r[1] for r in rows]
    buffered = shapely.buffer(geoms, buffer_m)
    minx, miny, maxx, maxy = shapely.bounds(buffered).T
    wkb_out = shapely.to_wkb(buffered)
    table = pa.table(
        {
            "geometry": pa.array(wkb_out, type=pa.binary()),
            "kind": pa.array(kinds, type=pa.string()),
            "xmin": pa.array(minx, type=pa.float64()),
            "ymin": pa.array(miny, type=pa.float64()),
            "xmax": pa.array(maxx, type=pa.float64()),
            "ymax": pa.array(maxy, type=pa.float64()),
        }
    )
    pq.write_table(table, output, compression="zstd", row_group_size=50000)
    logger.info("wrote %s (%.2f GB)", output, output.stat().st_size / 1e9)
    return output


def rasterize_roads_for_window(
    roads_parquet: Path,
    transform: rasterio.Affine,  # type: ignore[name-defined]
    width: int,
    height: int,
    *,
    bbox_5070: tuple[float, float, float, float] | None = None,
    threads: int = 4,
) -> np.ndarray:
    """Build an HxW boolean mask: True where a road/rail buffer covers the pixel.

    Pushdown via DuckDB on the parquet's xmin/xmax/ymin/ymax columns (added
    by :func:`fetch_overture_roads` if you write through that path; otherwise
    falls back to an ST_Intersects predicate — slower but correct). Each
    polygonize tile worker calls this independently, so tile-level
    parallelism comes for free; we keep DuckDB threads modest (4) to avoid
    oversubscribing inside a phase-1 worker process.
    """
    if bbox_5070 is None:
        left = transform.c
        top = transform.f
        right = left + width * transform.a
        bottom = top + height * transform.e
        bbox_5070 = (min(left, right), min(top, bottom), max(left, right), max(top, bottom))

    bx0, by0, bx1, by1 = bbox_5070
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    conn.execute(f"PRAGMA threads={threads}")

    # Prefer the cheap bbox-stats predicate so DuckDB skips row groups before
    # decoding any geometry. ST_Intersects then prunes false positives in the
    # remaining rows.
    schema_cols = {
        r[0]
        for r in conn.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{roads_parquet}') LIMIT 0"
        ).fetchall()
    }
    has_bbox = {"xmin", "ymin", "xmax", "ymax"} <= schema_cols
    if has_bbox:
        rows = conn.execute(f"""
            SELECT geometry
            FROM read_parquet('{roads_parquet}')
            WHERE xmax >= {bx0} AND xmin <= {bx1}
              AND ymax >= {by0} AND ymin <= {by1}
              AND ST_Intersects(
                  ST_GeomFromWKB(geometry),
                  ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})
              )
        """).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT geometry
            FROM read_parquet('{roads_parquet}')
            WHERE ST_Intersects(
                ST_GeomFromWKB(geometry),
                ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})
            )
        """).fetchall()
    conn.close()

    if not rows:
        return np.zeros((height, width), dtype=bool)

    geoms = shapely.from_wkb([bytes(r[0]) for r in rows])
    mask = rasterio.features.rasterize(
        [(g, 1) for g in geoms if g is not None and not g.is_empty],
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=True,
    )
    return mask.astype(bool)
