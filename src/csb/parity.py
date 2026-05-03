"""USDA-parity validation for CSB outputs.

Two entrypoints:

* :func:`prep_inputs` — rewrite our GeoParquet output and the USDA FileGDB into
  Hilbert-sorted parquets with explicit ``xmin/ymin/xmax/ymax`` columns. Enables
  DuckDB row-group pruning on bbox predicates.
* :func:`run_parity` — for each test tile, spatial-filter both sides to the
  tile bbox and compute polygon counts, total acres, and dissolved-union IoU
  via inclusion-exclusion (avoids the GEOS unary-union path entirely).

The default 16 test tiles span CONUS — Iowa corn belt to Imperial Valley to
Palouse to Delmarva — to give an honest IoU distribution rather than a single
dense corn-belt sample.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import duckdb
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)

# CONUS bounding box in EPSG:5070 (Albers Equal Area). Used as the reference
# extent for ST_Hilbert sort during prep so the resulting parquet's row-group
# stats are spatially local across the whole country.
CONUS_5070_BOX = {
    "min_x": -2_356_095.0,
    "min_y": 270_000.0,
    "max_x": 2_260_000.0,
    "max_y": 3_175_000.0,
}

# Default validation tiles: (region_name, target_x_5070, target_y_5070, what).
# 16 geospatially diverse 5000² tiles spanning CONUS.
DEFAULT_REGIONS: list[tuple[str, float, float, str]] = [
    ("iowa_corn_belt", -100_000, 1_950_000, "high-density corn/soy"),
    ("illinois_corn", 250_000, 2_000_000, "central IL corn"),
    ("nebraska_irrigated", -300_000, 1_700_000, "central NE irrigated"),
    ("kansas_wheat", -300_000, 1_400_000, "KS winter wheat"),
    ("texas_panhandle", -625_000, 1_100_000, "wheat/cotton, large fields"),
    ("texas_cotton_belt", -350_000, 900_000, "central TX cotton"),
    ("mississippi_delta", 250_000, 1_100_000, "cotton/soy/rice"),
    ("georgia_peanut", 950_000, 1_100_000, "GA peanut/cotton"),
    ("central_valley_ca", -2_000_000, 1_650_000, "irrigated specialty crops"),
    ("imperial_valley_ca", -1_750_000, 1_300_000, "winter veg, irrigated"),
    ("palouse_wheat", -1_850_000, 2_850_000, "PNW wheat, large fields"),
    ("snake_river_id", -1_500_000, 2_400_000, "ID potatoes / irrigated"),
    ("northern_plains_nd", -150_000, 2_700_000, "ND wheat / spring grains"),
    ("wisconsin_mixed", 200_000, 2_400_000, "WI dairy/corn mosaic"),
    ("ohio_corn_soy", 950_000, 2_100_000, "OH/IN corn/soy"),
    ("delmarva", 1_700_000, 1_900_000, "Delmarva poultry/soy"),
]


def _connect(threads: int) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    conn.execute(f"PRAGMA threads={threads}")
    return conn


def _hilbert_box_literal() -> str:
    b = CONUS_5070_BOX
    return (
        f"{{'min_x': {b['min_x']}, 'min_y': {b['min_y']},"
        f" 'max_x': {b['max_x']}, 'max_y': {b['max_y']}}}::BOX_2D"
    )


def prep_ours_parquet(src: Path, dst: Path, threads: int = 32) -> None:
    """Rewrite our CSB GeoParquet with bbox columns + Hilbert sort."""
    conn = _connect(threads)
    conn.execute(f"""
        COPY (
            SELECT
                geometry,
                CSBID, CSBYEARS, CSBACRES,
                CDL2018, CDL2019, CDL2020, CDL2021,
                CDL2022, CDL2023, CDL2024, CDL2025,
                STATEFIPS, STATEASD, ASD, CNTY, CNTYFIPS,
                INSIDE_X, INSIDE_Y, Shape_area, Shape_Length,
                ST_XMin(geometry) AS xmin,
                ST_YMin(geometry) AS ymin,
                ST_XMax(geometry) AS xmax,
                ST_YMax(geometry) AS ymax
            FROM read_parquet('{src}')
            ORDER BY ST_Hilbert(geometry, {_hilbert_box_literal()})
        ) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
    """)
    conn.close()


def prep_usda_gdb(gdb: Path, dst: Path, threads: int = 32) -> None:
    """Convert USDA ``national1825`` FileGDB layer to indexed parquet."""
    conn = _connect(threads)
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE usda_raw AS
        SELECT
            Shape AS geometry,
            CSBID, CSBYEARS, CSBACRES,
            CDL2018, CDL2019, CDL2020, CDL2021,
            CDL2022, CDL2023, CDL2024, CDL2025,
            STATEFIPS, STATEASD, ASD, CNTY, CNTYFIPS,
            ST_XMin(Shape) AS xmin,
            ST_YMin(Shape) AS ymin,
            ST_XMax(Shape) AS xmax,
            ST_YMax(Shape) AS ymax
        FROM ST_Read('{gdb}', layer='national1825')
    """)
    conn.execute(f"""
        COPY (
            SELECT * FROM usda_raw
            ORDER BY ST_Hilbert(geometry, {_hilbert_box_literal()})
        ) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
    """)
    conn.close()


def prep_inputs(
    ours_src: Path,
    ours_dst: Path,
    usda_gdb: Path,
    usda_dst: Path,
    threads: int = 32,
) -> None:
    """Run both :func:`prep_ours_parquet` and :func:`prep_usda_gdb`."""
    logger.info("prep ours: %s -> %s", ours_src, ours_dst)
    t0 = time.perf_counter()
    prep_ours_parquet(ours_src, ours_dst, threads=threads)
    logger.info("  done in %.1fs, %.2f GB", time.perf_counter() - t0, ours_dst.stat().st_size / 1e9)
    logger.info("prep usda: %s -> %s", usda_gdb, usda_dst)
    t0 = time.perf_counter()
    prep_usda_gdb(usda_gdb, usda_dst, threads=threads)
    logger.info("  done in %.1fs, %.2f GB", time.perf_counter() - t0, usda_dst.stat().st_size / 1e9)


def find_bbox_5070(
    target_x: float, target_y: float, tile_size: int = 5000
) -> tuple[float, float, float, float]:
    """EPSG:5070 bbox of the CDL tile containing (target_x, target_y)."""
    t_left, t_top = -2_356_095.0, 3_172_605.0
    px = 30.0
    col = int((target_x - t_left) / (tile_size * px))
    row = int((t_top - target_y) / (tile_size * px))
    left = t_left + col * tile_size * px
    top = t_top - row * tile_size * px
    right = left + tile_size * px
    bottom = top - tile_size * px
    return (left, bottom, right, top)


def parity_for_bbox(
    conn: duckdb.DuckDBPyConnection,
    ours_parquet: Path,
    usda_parquet: Path,
    bbox: tuple[float, float, float, float],
) -> dict:
    """Counts, acres and IoU for one tile bbox.

    Both inputs must have ``xmin/ymin/xmax/ymax`` columns (see
    :func:`prep_inputs`); the bbox WHERE predicate then prunes parquet row
    groups via stats. IoU uses inclusion-exclusion since each side is a
    non-overlapping polygon coverage (no GEOS unary union needed).
    """
    bx0, by0, bx1, by1 = bbox
    res: dict[str, object] = {"bbox_5070": list(bbox)}
    bbox_pred = f"xmax >= {bx0} AND xmin <= {bx1} AND ymax >= {by0} AND ymin <= {by1}"

    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE ours_clip AS
        SELECT ST_MakeValid(geometry) AS g, CSBACRES
        FROM read_parquet('{ours_parquet}')
        WHERE {bbox_pred}
          AND ST_Intersects(geometry, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))
    """)
    n_ours = conn.execute("SELECT COUNT(*) FROM ours_clip").fetchone()
    s_ours = conn.execute("SELECT SUM(CSBACRES) FROM ours_clip").fetchone()
    res["n_ours"] = int(n_ours[0]) if n_ours else 0
    res["ours_acres"] = float(s_ours[0] or 0) if s_ours else 0.0

    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE usda_clip AS
        SELECT ST_MakeValid(geometry) AS g, CSBACRES
        FROM read_parquet('{usda_parquet}')
        WHERE {bbox_pred}
          AND ST_Intersects(geometry, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))
    """)
    n_usda = conn.execute("SELECT COUNT(*) FROM usda_clip").fetchone()
    s_usda = conn.execute("SELECT SUM(CSBACRES) FROM usda_clip").fetchone()
    res["n_usda"] = int(n_usda[0]) if n_usda else 0
    res["usda_acres"] = float(s_usda[0] or 0) if s_usda else 0.0

    if res["n_ours"] == 0 or res["n_usda"] == 0:
        res["iou"] = None
        res["ratio_polys"] = None
        res["ratio_acres"] = None
        return res

    envelope = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    oa = conn.execute(
        f"SELECT SUM(ST_Area(ST_Intersection(g, {envelope})))/1e6 FROM ours_clip"
    ).fetchone()
    ua = conn.execute(
        f"SELECT SUM(ST_Area(ST_Intersection(g, {envelope})))/1e6 FROM usda_clip"
    ).fetchone()
    inter = conn.execute(
        "SELECT SUM(ST_Area(ST_Intersection(o.g, u.g)))/1e6 "
        "FROM ours_clip o JOIN usda_clip u ON ST_Intersects(o.g, u.g)"
    ).fetchone()
    ours_km2 = float((oa[0] if oa else 0) or 0)
    usda_km2 = float((ua[0] if ua else 0) or 0)
    inter_km2 = float((inter[0] if inter else 0) or 0)
    union_km2 = ours_km2 + usda_km2 - inter_km2

    res["ours_dissolved_km2"] = ours_km2
    res["usda_dissolved_km2"] = usda_km2
    res["intersection_km2"] = inter_km2
    res["union_km2"] = union_km2
    res["iou"] = (inter_km2 / union_km2) if union_km2 else None
    res["ratio_polys"] = (res["n_ours"] / res["n_usda"]) if res["n_usda"] else None
    res["ratio_acres"] = (res["ours_acres"] / res["usda_acres"]) if res["usda_acres"] else None
    return res


def run_parity(
    ours_parquet: Path,
    usda_parquet: Path,
    regions: Iterable[tuple[str, float, float, str]] = DEFAULT_REGIONS,
    *,
    tile_size: int = 5000,
    threads: int = 16,
    report_path: Path | None = None,
) -> list[dict]:
    """Compute parity for each region. Returns one dict per region."""
    conn = _connect(threads)
    results: list[dict] = []
    for name, tx, ty, what in regions:
        bbox = find_bbox_5070(tx, ty, tile_size=tile_size)
        t0 = time.perf_counter()
        try:
            r = parity_for_bbox(conn, ours_parquet, usda_parquet, bbox)
        except Exception:
            logger.exception("parity failed for %s", name)
            results.append({"region": name, "what": what, "error": "exception"})
            continue
        r["region"] = name
        r["what"] = what
        r["elapsed_sec"] = time.perf_counter() - t0
        results.append(r)
    conn.close()

    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w") as f:
            json.dump(
                {
                    "ours": str(ours_parquet),
                    "usda": str(usda_parquet),
                    "results": results,
                },
                f,
                indent=2,
            )
    return results


def summarize(results: list[dict]) -> dict:
    """Aggregate IoU / ratio stats across all valid (non-error) regions."""
    ok = [r for r in results if r.get("iou") is not None]
    if not ok:
        return {"n": 0}
    ious = [r["iou"] for r in ok]
    rps = [r["ratio_polys"] for r in ok if r.get("ratio_polys")]
    ras = [r["ratio_acres"] for r in ok if r.get("ratio_acres")]
    return {
        "n": len(ok),
        "iou_mean": float(np.mean(ious)),
        "iou_median": float(np.median(ious)),
        "iou_min": float(min(ious)),
        "iou_max": float(max(ious)),
        "ratio_polys_mean": float(np.mean(rps)) if rps else None,
        "ratio_polys_median": float(np.median(rps)) if rps else None,
        "ratio_acres_mean": float(np.mean(ras)) if ras else None,
        "ratio_acres_median": float(np.median(ras)) if ras else None,
    }
