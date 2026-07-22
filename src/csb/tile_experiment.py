"""Single-tile parameter experiments for parity tuning.

Polygonize one tile under an arbitrary parameter set, then score the result
against the USDA CSB1825 reference clipped to that tile's bbox — all from
geometry (no postprocess/CSBID needed). This is the engine for sweeping the
knobs that drive parity (cropland-years threshold, simplify tolerance, minimum
mapping unit, elimination schedule, roads mask) across representative tiles to
find the settings that best reproduce USDA CSB.

Everything here is diagnostic; it does not touch the production pipeline.
"""

import logging
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import rasterio

from csb.config import ACRES_PER_SQM, DEFAULT_NATIONAL_CDL_DIR
from csb.polygonize import _tile_windows

logger = logging.getLogger(__name__)


def region_to_tile(
    target_x: float,
    target_y: float,
    cdl_path: Path,
    tile_size: int = 5000,
) -> tuple[str, tuple[float, float, float, float]]:
    """Map an EPSG:5070 point to the (tile_name, bbox_5070) that contains it."""
    with rasterio.open(cdl_path) as src:
        transform = src.transform
        tiles = _tile_windows(src.width, src.height, tile_size)
    px = transform[0]
    for name, w in tiles:
        left = transform[2] + w.col_off * px
        top = transform[5] + w.row_off * (-px)
        right = left + w.width * px
        bottom = top - w.height * px
        if left <= target_x < right and bottom <= target_y < top:
            return name, (left, bottom, right, top)
    msg = f"no tile contains ({target_x}, {target_y})"
    raise ValueError(msg)


def _connect(threads: int) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    conn.execute(f"PRAGMA threads={threads}")
    return conn


def tile_parity(
    ours_tile_parquet: Path,
    usda_indexed: Path,
    bbox: tuple[float, float, float, float],
    threads: int = 16,
) -> dict:
    """Geometry-only parity of one tile vs USDA, clipped to ``bbox``.

    Inclusion-exclusion IoU (each side is a non-overlapping coverage), plus
    polygon counts and acreage on each side. Reads ours from a polygonize-stage
    parquet (``geometry`` WKB) and USDA from the prepped index.
    """
    bx0, by0, bx1, by1 = bbox
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn = _connect(threads)
    try:
        conn.execute(f"""
            CREATE TEMP TABLE ours AS
            SELECT ST_MakeValid(geometry) AS g FROM read_parquet('{ours_tile_parquet}')
            WHERE ST_Intersects(geometry, {env})
        """)
        conn.execute(f"""
            CREATE TEMP TABLE usda AS
            SELECT ST_MakeValid(geometry) AS g FROM read_parquet('{usda_indexed}')
            WHERE xmax >= {bx0} AND xmin <= {bx1} AND ymax >= {by0} AND ymin <= {by1}
              AND ST_Intersects(geometry, {env})
        """)

        def scalar(sql: str) -> float:
            row = conn.execute(sql).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0

        n_ours = int(scalar("SELECT COUNT(*) FROM ours"))
        n_usda = int(scalar("SELECT COUNT(*) FROM usda"))
        a_ours = scalar(f"SELECT SUM(ST_Area(ST_Intersection(g, {env}))) FROM ours")
        a_usda = scalar(f"SELECT SUM(ST_Area(ST_Intersection(g, {env}))) FROM usda")
        a_inter = scalar(
            "SELECT SUM(ST_Area(ST_Intersection(o.g, u.g))) "
            "FROM ours o JOIN usda u ON ST_Intersects(o.g, u.g)"
        )
        union = a_ours + a_usda - a_inter
        return {
            "n_ours": n_ours,
            "n_usda": n_usda,
            "ratio_polys": (n_ours / n_usda) if n_usda else None,
            "acres_ours": a_ours * ACRES_PER_SQM,
            "acres_usda": a_usda * ACRES_PER_SQM,
            "ratio_acres": (a_ours / a_usda) if a_usda else None,
            "iou": (a_inter / union) if union else None,
            "ours_only_acres": (a_ours - a_inter) * ACRES_PER_SQM,
            "usda_only_acres": (a_usda - a_inter) * ACRES_PER_SQM,
        }
    finally:
        conn.close()


def run_tile_experiment(
    *,
    region_name: str,
    target_x: float,
    target_y: float,
    params: dict[str, Any],
    usda_indexed: Path,
    start_year: int = 2018,
    end_year: int = 2025,
    national_cdl_dir: str | Path = DEFAULT_NATIONAL_CDL_DIR,
    tile_size: int = 5000,
    threads: int = 16,
    workdir: Path | None = None,
) -> dict:
    """Polygonize one tile under ``params`` and score parity vs USDA.

    ``params`` may set: min_cropland_years, simplify_tolerance, min_polygon_area,
    eliminate_thresholds, roads_mask, same_combo_dissolve. Returns the merged
    params + parity metrics as one flat record.
    """
    from csb.polygonize import run_polygonize

    cdl_dir = Path(national_cdl_dir)
    cdl_path = cdl_dir / str(start_year) / f"{start_year}_30m_cdls.tif"
    tile_name, bbox = region_to_tile(target_x, target_y, cdl_path, tile_size)

    if workdir is not None:
        tmp_ctx = None
        out_dir = Path(workdir)
    else:
        tmp_ctx = tempfile.TemporaryDirectory()
        out_dir = Path(tmp_ctx.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        run_polygonize(
            start_year=start_year,
            end_year=end_year,
            output_dir=out_dir,
            national_cdl_dir=cdl_dir,
            tile_size=tile_size,
            area=tile_name,
            phase1_workers=1,
            phase2_workers=1,
            **params,
        )
        tile_parquet = out_dir / f"{tile_name}.parquet"
        if not tile_parquet.exists():
            return {"region": region_name, "tile": tile_name, "params": params,
                    "error": "no output (tile empty or all filtered)"}
        metrics = tile_parity(tile_parquet, usda_indexed, bbox, threads=threads)
        return {"region": region_name, "tile": tile_name, "bbox_5070": list(bbox),
                "params": params, **metrics}
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()
