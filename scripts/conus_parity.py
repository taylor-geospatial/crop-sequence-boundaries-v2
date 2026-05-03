"""CONUS-scale parity validation against USDA CSB1825 ground truth.

Pure DuckDB spatial pipeline. Streams both ours (GeoParquet) and USDA (FileGDB)
from disk per region — no in-memory materialization of 15M shapely objects.

For each of N geospatially diverse 5000² test tiles:
  1. Spatial-filter ours national parquet to tile bbox via DuckDB.
  2. Spatial-filter USDA `national1825` layer to same bbox via DuckDB ST_Read.
  3. Dissolve both sides clipped to bbox; compute intersection / union area
     and IoU.
  4. Report per-region counts, acres, ratios.

Aggregate stats printed at the end + JSON dumped.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

REGIONS = [
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


def find_bbox_5070(
    target_x: float, target_y: float, tile_size: int = 5000
) -> tuple[float, float, float, float]:
    """EPSG:5070 bbox of the 5000x5000 CDL tile containing (target_x, target_y)."""
    T_left, T_top = -2356095.0, 3172605.0
    px = 30.0
    col = int((target_x - T_left) / (tile_size * px))
    row = int((T_top - target_y) / (tile_size * px))
    left = T_left + col * tile_size * px
    top = T_top - row * tile_size * px
    right = left + tile_size * px
    bottom = top - tile_size * px
    return (left, bottom, right, top)


def parity_for_bbox(
    conn: duckdb.DuckDBPyConnection,
    ours_parquet: Path,
    usda_parquet: Path,
    bbox_5070: tuple[float, float, float, float],
) -> dict:
    """DuckDB IoU + counts + acres for one tile bbox.

    Assumes both inputs have been pre-prepped (via prep_parity_inputs.py) with:
      - explicit xmin/ymin/xmax/ymax columns
      - rows sorted by Hilbert curve over the centroid
      - small row groups (~50k rows)

    The bbox predicate `xmax >= bx0 AND xmin <= bx1 AND ...` is pushdown-able
    by DuckDB's parquet reader: row groups whose stats don't overlap the
    query bbox are skipped entirely.
    """
    bx0, by0, bx1, by1 = bbox_5070
    res: dict[str, object] = {"bbox_5070": list(bbox_5070)}
    bbox_pred = f"xmax >= {bx0} AND xmin <= {bx1} AND ymax >= {by0} AND ymin <= {by1}"

    # Stage 1: spatial-filter ours.
    t0 = time.perf_counter()
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE ours_clip AS
        SELECT ST_MakeValid(geometry) AS g, CSBACRES
        FROM read_parquet('{ours_parquet}')
        WHERE {bbox_pred}
          AND ST_Intersects(geometry, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))
    """)
    n_ours = conn.execute("SELECT COUNT(*) FROM ours_clip").fetchone()
    assert n_ours is not None
    res["n_ours"] = int(n_ours[0])
    s_ours = conn.execute("SELECT SUM(CSBACRES) FROM ours_clip").fetchone()
    assert s_ours is not None
    res["ours_acres"] = float(s_ours[0] or 0)
    res["ours_read_sec"] = time.perf_counter() - t0

    # Stage 2: spatial-filter USDA.
    t0 = time.perf_counter()
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE usda_clip AS
        SELECT ST_MakeValid(geometry) AS g, CSBACRES
        FROM read_parquet('{usda_parquet}')
        WHERE {bbox_pred}
          AND ST_Intersects(geometry, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))
    """)
    n_usda = conn.execute("SELECT COUNT(*) FROM usda_clip").fetchone()
    assert n_usda is not None
    res["n_usda"] = int(n_usda[0])
    s_usda = conn.execute("SELECT SUM(CSBACRES) FROM usda_clip").fetchone()
    assert s_usda is not None
    res["usda_acres"] = float(s_usda[0] or 0)
    res["usda_read_sec"] = time.perf_counter() - t0

    if res["n_ours"] == 0 or res["n_usda"] == 0:
        res["iou"] = None
        res["ratio_polys"] = None
        res["ratio_acres"] = None
        return res

    # Stage 3: IoU without ST_Union_Agg.
    # Both sides are non-overlapping polygon coverages (partitions of the
    # cropland mask within a tile), so:
    #   ours_area  = sum_o area(o ∩ bbox)
    #   usda_area  = sum_u area(u ∩ bbox)
    #   inter_area = sum_(o,u) area(o ∩ u)            -- requires spatial join
    #   union_area = ours_area + usda_area - inter_area  (incl./excl.)
    # GEOS unary union over hundreds of thousands of polygons is single-threaded
    # and slow; this version runs as a parallel scan + parallel spatial join.
    t0 = time.perf_counter()
    oa = conn.execute(
        f"SELECT SUM(ST_Area(ST_Intersection(g, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))))/1e6 FROM ours_clip"
    ).fetchone()
    assert oa is not None
    ours_km2 = float(oa[0] or 0)

    ua = conn.execute(
        f"SELECT SUM(ST_Area(ST_Intersection(g, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))))/1e6 FROM usda_clip"
    ).fetchone()
    assert ua is not None
    usda_km2 = float(ua[0] or 0)

    # Spatial join. ST_Intersects in DuckDB spatial uses bbox prefilter via the
    # geometry's internal stats; for ~hundreds of thousands per side this runs
    # as a parallel hash/loop join.
    inter = conn.execute(
        "SELECT SUM(ST_Area(ST_Intersection(o.g, u.g)))/1e6 "
        "FROM ours_clip o JOIN usda_clip u ON ST_Intersects(o.g, u.g)"
    ).fetchone()
    assert inter is not None
    inter_km2 = float(inter[0] or 0)

    union_km2 = ours_km2 + usda_km2 - inter_km2
    res["iou_sec"] = time.perf_counter() - t0
    res["ours_dissolved_km2"] = ours_km2
    res["usda_dissolved_km2"] = usda_km2
    res["intersection_km2"] = inter_km2
    res["union_km2"] = union_km2
    res["iou"] = (inter_km2 / union_km2) if union_km2 else None
    res["ratio_polys"] = (res["n_ours"] / res["n_usda"]) if res["n_usda"] else None
    res["ratio_acres"] = (res["ours_acres"] / res["usda_acres"]) if res["usda_acres"] else None
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--national",
        default="data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet",
        help="Hilbert-sorted ours parquet with xmin/xmax/ymin/ymax columns "
        "(produced by scripts/prep_parity_inputs.py).",
    )
    ap.add_argument(
        "--gdb",
        default="data/CSB1825_indexed.parquet",
        help="USDA ground truth, pre-converted to Hilbert-sorted indexed "
        "parquet (produced by scripts/prep_parity_inputs.py).",
    )
    ap.add_argument("--report", default="data/profile/conus_parity.json")
    ap.add_argument("--regions", nargs="*", help="Subset of region names (default: all 16)")
    args = ap.parse_args()

    national = Path(args.national)
    gdb = Path(args.gdb)
    if not national.exists():
        sys.exit(f"missing {national}")
    if not gdb.exists():
        sys.exit(f"missing {gdb}")

    selected = REGIONS if not args.regions else [r for r in REGIONS if r[0] in args.regions]

    print(f"ours indexed:   {national}  ({national.stat().st_size / 1e9:.2f} GB)")
    print(f"USDA indexed:   {gdb}  ({gdb.stat().st_size / 1e9:.2f} GB)")
    print(f"Regions:        {len(selected)}")
    print()

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    conn.execute("PRAGMA threads=16")

    results = []
    print(
        f"{'region':<22}{'tile':>10}{'n_ours':>10}{'n_usda':>10}{'ratio_p':>9}"
        f"{'ratio_a':>9}{'IoU':>8}{'sec':>7}",
        flush=True,
    )
    print("-" * 86, flush=True)
    for name, tx, ty, what in selected:
        bbox = find_bbox_5070(tx, ty)
        t0 = time.perf_counter()
        try:
            r = parity_for_bbox(conn, national, gdb, bbox)
        except Exception as e:
            print(f"{name:<22}  ERROR: {type(e).__name__}: {e}", flush=True)
            results.append({"region": name, "what": what, "error": str(e)})
            continue
        elapsed = time.perf_counter() - t0
        r["region"] = name
        r["what"] = what
        r["elapsed_sec"] = elapsed
        results.append(r)
        ratio_p = r.get("ratio_polys") or 0
        ratio_a = r.get("ratio_acres") or 0
        iou = r.get("iou") or 0
        # Tile name from bbox.
        T_left, T_top = -2356095.0, 3172605.0
        col = int((bbox[0] - T_left) / (5000 * 30))
        row = int((T_top - bbox[3]) / (5000 * 30))
        row_label = chr(65 + row) if row < 26 else (chr(65 + row // 26 - 1) + chr(65 + row % 26))
        tile = f"{row_label}{col}"
        print(
            f"{name:<22}{tile:>10}{r['n_ours']:>10,}{r['n_usda']:>10,}"
            f"{ratio_p:>9.2f}{ratio_a:>9.2f}{iou:>8.3f}{elapsed:>7.1f}",
            flush=True,
        )

    conn.close()

    # Aggregate.
    ok = [r for r in results if r.get("iou") is not None]
    if ok:
        ious = [r["iou"] for r in ok]
        rps = [r["ratio_polys"] for r in ok if r["ratio_polys"]]
        ras = [r["ratio_acres"] for r in ok if r["ratio_acres"]]
        print()
        print("=== aggregate (IoU-valid regions) ===")
        print(f"  n={len(ok)}/{len(selected)}")
        print(
            f"  IoU         mean={np.mean(ious):.3f}  median={np.median(ious):.3f}"
            f"  min={min(ious):.3f}  max={max(ious):.3f}"
        )
        if rps:
            print(
                f"  poly ratio  mean={np.mean(rps):.2f}  median={np.median(rps):.2f}"
                f"  min={min(rps):.2f}  max={max(rps):.2f}"
            )
        if ras:
            print(
                f"  acres ratio mean={np.mean(ras):.2f}  median={np.median(ras):.2f}"
                f"  min={min(ras):.2f}  max={max(ras):.2f}"
            )

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w") as f:
        json.dump({"national": str(national), "gdb": str(gdb), "results": results}, f, indent=2)
    print(f"\nReport: {args.report}", flush=True)


if __name__ == "__main__":
    main()
