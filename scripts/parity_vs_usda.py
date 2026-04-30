"""Parity check: our raster-side pipeline vs USDA's official CSB 1825 GDB.

Picks the same Iowa-center tile we've been profiling, runs Path B end-to-end,
extracts USDA polygons inside the tile bbox from `data/CSB1825.gdb`, and reports:

- polygon count, total area
- dissolved-union IoU
- per-polygon IoU distribution (1:N matching by best overlap)

Run:
    uv run python scripts/parity_vs_usda.py --tile-size 2500 --pick-iowa --years 2018 2025
"""

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyogrio
import rasterio
import shapely

from csb.config import load_config
from csb.polygonize import _tile_windows
from scripts.profile_tile_v2 import combine_unique, load_window, run_raster, Stopwatch


def pick_iowa(tiles, sample_path):
    target_x, target_y = -100_000, 1_950_000
    with rasterio.open(sample_path) as src:
        T = src.transform
    return min(
        tiles,
        key=lambda nw: (
            ((T * (nw[1].col_off + nw[1].width / 2, nw[1].row_off + nw[1].height / 2))[0] - target_x) ** 2
            + ((T * (nw[1].col_off + nw[1].width / 2, nw[1].row_off + nw[1].height / 2))[1] - target_y) ** 2
        ),
    )


def tile_bbox(window, sample_path):
    with rasterio.open(sample_path) as src:
        T = src.transform
    left, top = T * (window.col_off, window.row_off)
    right, bottom = T * (window.col_off + window.width, window.row_off + window.height)
    return min(left, right), min(top, bottom), max(left, right), max(top, bottom)


def read_usda_in_bbox(gdb_path, bbox):
    """Read USDA polygons whose bbox intersects ours. Returns shapely geoms + attrs."""
    df = pyogrio.read_dataframe(
        gdb_path,
        layer="national1825",
        bbox=bbox,
        columns=["CSBID", "CSBACRES"],
    )
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/local.yaml")
    ap.add_argument("--gdb", default="data/CSB1825.gdb")
    ap.add_argument("--tile-size", type=int, default=2500)
    ap.add_argument("--pick-iowa", action="store_true")
    ap.add_argument("--area", default=None)
    ap.add_argument("--years", nargs=2, type=int, default=[2018, 2025])
    ap.add_argument("--save", default=None)
    ap.add_argument("--clip-usda", action="store_true",
                    help="Clip USDA polygons to the tile bbox before computing IoU "
                         "(fairer apples-to-apples since our output is already clipped)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    years = list(range(args.years[0], args.years[1] + 1))
    sample = Path(cfg["paths"]["national_cdl"]) / str(years[0]) / f"{years[0]}_30m_cdls.tif"
    with rasterio.open(sample) as src:
        W, H = src.width, src.height
    tiles = _tile_windows(W, H, args.tile_size)
    if args.pick_iowa:
        area, window = pick_iowa(tiles, sample)
    else:
        area, window = next((n, w) for n, w in tiles if n == args.area)

    bbox = tile_bbox(window, sample)
    print(f"tile {area}, window {window}")
    print(f"bbox EPSG:5070: {bbox}")

    # ---- Run path B ----
    sw_load = Stopwatch()
    with sw_load.stage("load_window"):
        seq_ids, transform = load_window(cfg, window, years)
    with sw_load.stage("combine_unique"):
        combo_raster, effective = combine_unique(seq_ids, len(years))
    sw_b = Stopwatch()
    raster_table = run_raster(combo_raster, effective, transform, cfg, sw_b)
    print(f"\nour pipeline: {raster_table.num_rows} polys, "
          f"{float(np.asarray(raster_table['area_sqm']).sum())/1e6:.1f} km² "
          f"in {sw_b.total():.2f}s")

    # ---- Read USDA ground truth in bbox ----
    print(f"\nreading USDA GDB at {args.gdb} ...")
    usda_df = read_usda_in_bbox(args.gdb, bbox)
    print(f"USDA polys in bbox: {len(usda_df)}")
    usda_total_acres = float(usda_df["CSBACRES"].sum())
    usda_total_km2 = usda_total_acres * 4046.86 / 1e6
    print(f"USDA total area in bbox (sum CSBACRES): {usda_total_km2:.1f} km² "
          f"({usda_total_acres:,.0f} acres, NOTE: this is full polygons even if straddling bbox)")

    # ---- Compute IoU via DuckDB spatial ----
    print("\ncomputing parity via DuckDB ST_Union_Agg ...")
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    ours_geoms = pa.array(raster_table["geometry"], type=pa.binary())
    ours_tbl = pa.table({"geometry": ours_geoms})
    conn.register("ours", ours_tbl)

    usda_wkb = shapely.to_wkb(usda_df.geometry.values)
    usda_tbl = pa.table({"geometry": pa.array(usda_wkb, type=pa.binary())})
    conn.register("usda", usda_tbl)

    # Build tile bbox geometry
    bx0, by0, bx1, by1 = bbox
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE tile_bbox AS
        SELECT ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}) AS g
    """)

    if args.clip_usda:
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE usda_clipped AS
            SELECT ST_Intersection(ST_GeomFromWKB(u.geometry::BLOB), t.g) AS g
            FROM usda u, tile_bbox t
        """)
        usda_view = "usda_clipped"
        usda_geom_col = "g"
    else:
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE usda_clipped AS
            SELECT ST_GeomFromWKB(geometry::BLOB) AS g FROM usda
        """)
        usda_view = "usda_clipped"
        usda_geom_col = "g"

    print("  unioning ours ...")
    a_area = conn.execute("SELECT ST_Area(ST_Union_Agg(ST_GeomFromWKB(geometry::BLOB))) FROM ours").fetchone()[0]
    print(f"  ours dissolved area:  {a_area/1e6:.2f} km²")
    print("  unioning USDA ...")
    b_area = conn.execute(f"SELECT ST_Area(ST_Union_Agg({usda_geom_col})) FROM {usda_view}").fetchone()[0]
    print(f"  USDA dissolved area:  {b_area/1e6:.2f} km²")
    print("  intersecting ...")
    iou = conn.execute(f"""
        WITH au AS (SELECT ST_Union_Agg(ST_GeomFromWKB(geometry::BLOB)) AS g FROM ours),
             bu AS (SELECT ST_Union_Agg({usda_geom_col}) AS g FROM {usda_view})
        SELECT
            ST_Area(ST_Intersection(au.g, bu.g)) AS inter,
            ST_Area(ST_Union(au.g, bu.g)) AS uni
        FROM au, bu
    """).fetchone()
    inter, uni = iou
    print(f"  intersection: {inter/1e6:.2f} km²")
    print(f"  union:        {uni/1e6:.2f} km²")
    iou_val = inter / uni if uni else None
    print(f"  dissolved IoU vs USDA: {iou_val:.4f}" if iou_val is not None else "  IoU: n/a")

    # Per-polygon IoU summary: spatial join ours -> usda, take best overlap per ours polygon
    print("\nper-polygon best-match IoU distribution (sample of 5000 of ours)...")
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE matches AS
        WITH oursg AS (
            SELECT ROW_NUMBER() OVER () AS oid, ST_GeomFromWKB(geometry::BLOB) AS g FROM ours
            USING SAMPLE 5000
        ),
        cand AS (
            SELECT o.oid,
                   ST_Area(ST_Intersection(o.g, u.{usda_geom_col})) AS inter,
                   ST_Area(o.g) AS oa,
                   ST_Area(u.{usda_geom_col}) AS ua
            FROM oursg o, {usda_view} u
            WHERE ST_Intersects(o.g, u.{usda_geom_col})
        ),
        best AS (
            SELECT oid, MAX(inter / NULLIF(oa + ua - inter, 0)) AS iou
            FROM cand GROUP BY oid
        )
        SELECT iou FROM best WHERE iou IS NOT NULL
    """)
    iou_arr = conn.execute("SELECT iou FROM matches").fetchnumpy()["iou"]
    if len(iou_arr):
        print(f"  n={len(iou_arr)}  mean={iou_arr.mean():.3f}  median={np.median(iou_arr):.3f}  "
              f"p10={np.percentile(iou_arr, 10):.3f}  p90={np.percentile(iou_arr, 90):.3f}")

    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        out = {
            "tile": area,
            "window": {"col_off": window.col_off, "row_off": window.row_off,
                       "width": window.width, "height": window.height},
            "bbox_5070": list(bbox),
            "ours": {"n": raster_table.num_rows,
                     "area_km2": float(np.asarray(raster_table["area_sqm"]).sum()) / 1e6,
                     "runtime_sec": sw_b.total()},
            "usda": {"n": int(len(usda_df)), "area_km2_csbacres": usda_total_km2},
            "dissolved": {"ours_km2": a_area / 1e6, "usda_km2": b_area / 1e6,
                          "intersection_km2": inter / 1e6, "union_km2": uni / 1e6,
                          "iou": iou_val},
            "per_polygon_iou": {
                "n_sampled": int(len(iou_arr)),
                "mean": float(iou_arr.mean()) if len(iou_arr) else None,
                "median": float(np.median(iou_arr)) if len(iou_arr) else None,
                "p10": float(np.percentile(iou_arr, 10)) if len(iou_arr) else None,
                "p90": float(np.percentile(iou_arr, 90)) if len(iou_arr) else None,
            } if len(iou_arr) else None,
            "clip_usda": args.clip_usda,
        }
        with open(args.save, "w") as f:
            json.dump(out, f, indent=2)
        print(f"\nsaved {args.save}")


if __name__ == "__main__":
    main()
