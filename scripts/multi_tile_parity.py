"""Multi-tile parity study: run E2E pipeline on a geospatially diverse sample
of CONUS tiles, then compare against USDA CSB ground truth.

Reports per-tile counts, areas, and dissolved-union IoU. Designed to verify
that Iowa-Q30/I15 (densest cropland) is an outlier rather than the typical
parity case.

Usage (under SLURM):
    sbatch scripts/multi_tile_parity.sbatch

Or directly (one tile per row of REGIONS, sequential):
    uv run python scripts/multi_tile_parity.py --tile-size 5000 --years 2018 2025
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import rasterio
import shapely
from rasterio.windows import Window

from csb.config import load_config
from csb.polygonize import _tile_windows

# (region_name, target_x_5070, target_y_5070, what_it_stresses)
REGIONS = [
    ("iowa_corn_belt", -100_000, 1_950_000, "high-density corn/soy"),
    ("texas_panhandle", -625_000, 1_100_000, "medium density wheat/cotton, large fields"),
    ("central_valley_ca", -2_000_000, 1_650_000, "irrigated mixed crops, tight boundaries"),
    ("mississippi_delta", 250_000, 1_100_000, "cotton/soy/rice"),
    ("florida_sugarcane", 1_000_000, 600_000, "sugarcane (validates BARREN_CODE=254)"),
    ("palouse_wheat", -1_850_000, 2_850_000, "sparse, very large wheat fields"),
    ("wisconsin_mixed", 200_000, 2_400_000, "small fields, dairy/corn mosaic"),
    ("maine_sparse", 1_950_000, 2_950_000, "mostly empty / sparse cropland"),
]


def find_tile(
    target_x: float, target_y: float, tile_size: int, sample_path: Path
) -> tuple[str, Window]:
    with rasterio.open(sample_path) as src:
        T = src.transform
        W, H = src.width, src.height
    tiles = _tile_windows(W, H, tile_size)

    def dist(nw: tuple[str, Window]) -> float:
        cx, cy = T * (nw[1].col_off + nw[1].width / 2, nw[1].row_off + nw[1].height / 2)
        return (cx - target_x) ** 2 + (cy - target_y) ** 2

    return min(tiles, key=dist)


def tile_bbox(window: Window, sample_path: Path) -> tuple[float, float, float, float]:
    with rasterio.open(sample_path) as src:
        T = src.transform
    left, top = T * (window.col_off, window.row_off)
    right, bottom = T * (window.col_off + window.width, window.row_off + window.height)
    return (min(left, right), min(top, bottom), max(left, right), max(top, bottom))


def run_pipeline(config_path: str, area: str, years: list[int], output_root: Path) -> Path:
    """Run csb polygonize + postprocess for one tile via the CLI. Returns the
    national parquet path."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    poly_dir = output_root / "polygonize"
    pp_dir = output_root / "postprocess"
    sy, ey = years[0], years[1]

    if not (poly_dir / f"{area}.parquet").exists():
        subprocess.run(
            [
                "uv",
                "run",
                "csb",
                "--config",
                config_path,
                "polygonize",
                str(sy),
                str(ey),
                "--area",
                area,
                "--output",
                str(poly_dir),
            ],
            check=True,
        )

    if not (pp_dir / "national" / f"CSB{str(sy)[2:]}{str(ey)[2:]}.parquet").exists():
        subprocess.run(
            [
                "uv",
                "run",
                "csb",
                "--config",
                config_path,
                "postprocess",
                str(sy),
                str(ey),
                "--polygonize-dir",
                str(poly_dir),
                "--output",
                str(pp_dir),
            ],
            check=True,
        )

    return pp_dir / "national" / f"CSB{str(sy)[2:]}{str(ey)[2:]}.parquet"


def compute_parity(
    national_parquet: Path, gdb_path: Path, bbox: tuple[float, float, float, float]
) -> dict:
    """Dissolved-union IoU + per-tile feature/area stats."""
    ours = pq.read_table(national_parquet)
    n_ours = ours.num_rows
    ours_acres = float(np.asarray(ours["CSBACRES"]).sum())

    usda = pyogrio.read_dataframe(
        str(gdb_path), layer="national1825", bbox=bbox, columns=["CSBID", "CSBACRES"]
    )
    n_usda = len(usda)
    usda_acres = float(usda["CSBACRES"].sum())

    if n_ours == 0 and n_usda == 0:
        return {
            "n_ours": 0,
            "n_usda": 0,
            "ours_acres": 0,
            "usda_acres": 0,
            "ours_dissolved_km2": 0,
            "usda_dissolved_km2": 0,
            "intersection_km2": 0,
            "union_km2": 0,
            "iou": None,
            "ratio_polys": None,
            "ratio_acres": None,
        }

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    ours_tbl = pa.table({"g": pa.array(ours["geometry"], type=pa.binary())})
    if n_usda > 0:
        usda_tbl = pa.table({"g": pa.array(shapely.to_wkb(usda.geometry.values), type=pa.binary())})
    else:
        usda_tbl = pa.table({"g": pa.array([], type=pa.binary())})
    conn.register("ours", ours_tbl)
    conn.register("usda", usda_tbl)

    bx0, by0, bx1, by1 = bbox
    if n_ours > 0 and n_usda > 0:
        r = conn.execute(f"""
            WITH au AS (SELECT ST_Union_Agg(ST_MakeValid(ST_GeomFromWKB(g::BLOB))) AS g FROM ours),
                 bu AS (SELECT ST_Union_Agg(ST_Intersection(ST_MakeValid(ST_GeomFromWKB(g::BLOB)),
                                                            ST_MakeEnvelope({bx0},{by0},{bx1},{by1}))) AS g FROM usda)
            SELECT ST_Area(au.g)/1e6, ST_Area(bu.g)/1e6,
                   ST_Area(ST_Intersection(au.g, bu.g))/1e6,
                   ST_Area(ST_Union(au.g, bu.g))/1e6
            FROM au, bu
        """).fetchone()
        assert r is not None
        oa, ua, inter, uni = r
        iou = inter / uni if uni else None
    elif n_ours > 0:
        r1 = conn.execute(
            "SELECT ST_Area(ST_Union_Agg(ST_MakeValid(ST_GeomFromWKB(g::BLOB))))/1e6 FROM ours"
        ).fetchone()
        assert r1 is not None
        oa = r1[0]
        ua, inter, uni, iou = 0.0, 0.0, oa, 0.0
    else:
        r2 = conn.execute(f"""
            SELECT ST_Area(ST_Union_Agg(ST_Intersection(ST_MakeValid(ST_GeomFromWKB(g::BLOB)),
                                                        ST_MakeEnvelope({bx0},{by0},{bx1},{by1}))))/1e6
            FROM usda
        """).fetchone()
        assert r2 is not None
        ua = r2[0]
        oa, inter, uni, iou = 0.0, 0.0, ua, 0.0
    conn.close()

    return {
        "n_ours": n_ours,
        "n_usda": n_usda,
        "ours_acres": ours_acres,
        "usda_acres": usda_acres,
        "ours_dissolved_km2": float(oa),
        "usda_dissolved_km2": float(ua),
        "intersection_km2": float(inter) if inter is not None else 0,
        "union_km2": float(uni) if uni is not None else 0,
        "iou": float(iou) if iou is not None else None,
        "ratio_polys": (n_ours / n_usda) if n_usda > 0 else None,
        "ratio_acres": (ours_acres / usda_acres) if usda_acres > 0 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/local.yaml")
    ap.add_argument("--gdb", default="data/CSB1825.gdb")
    ap.add_argument("--tile-size", type=int, default=5000)
    ap.add_argument("--years", nargs=2, type=int, default=[2018, 2025])
    ap.add_argument("--output-root", default="data/output/multi_tile")
    ap.add_argument("--report", default="data/profile/multi_tile_parity.json")
    ap.add_argument("--regions", nargs="*", help="Subset of region names to run (default: all)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    sample = (
        Path(cfg["paths"]["national_cdl"]) / str(args.years[0]) / f"{args.years[0]}_30m_cdls.tif"
    )
    output_root = Path(args.output_root)
    regions = REGIONS if not args.regions else [r for r in REGIONS if r[0] in args.regions]

    results = []
    for name, tx, ty, what in regions:
        t0 = time.perf_counter()
        area, window = find_tile(tx, ty, args.tile_size, sample)
        bbox = tile_bbox(window, sample)
        region_root = output_root / name

        print(f"\n=== {name} (tile {area}, stresses: {what}) ===")
        print(f"    bbox: {bbox}")

        try:
            national = run_pipeline(args.config, area, args.years, region_root)
        except subprocess.CalledProcessError as e:
            print(f"    PIPELINE FAILED: {e}")
            results.append({"region": name, "tile": area, "bbox": list(bbox), "error": str(e)})
            continue

        try:
            parity = compute_parity(national, Path(args.gdb), bbox)
        except Exception as e:
            print(f"    PARITY FAILED: {type(e).__name__}: {e}")
            results.append({"region": name, "tile": area, "bbox": list(bbox), "error": str(e)})
            continue

        elapsed = time.perf_counter() - t0
        rec = {
            "region": name,
            "tile": area,
            "bbox": list(bbox),
            "stresses": what,
            "elapsed_sec": elapsed,
            **parity,
        }
        results.append(rec)
        print(
            f"    ours: {rec['n_ours']:>7,} polys / {rec['ours_acres']:>10,.0f} acres  |"
            f"  USDA: {rec['n_usda']:>7,} polys / {rec['usda_acres']:>10,.0f} acres  |"
            f"  IoU: {rec['iou']:.4f}"
            if rec.get("iou") is not None
            else f"    ours: {rec['n_ours']} / {rec['ours_acres']:.0f}  USDA: {rec['n_usda']}  IoU: n/a"
        )

    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    with open(args.report, "w") as f:
        json.dump(
            {
                "config": str(args.config),
                "years": args.years,
                "tile_size": args.tile_size,
                "results": results,
            },
            f,
            indent=2,
        )

    print("\n=== Multi-tile parity summary ===")
    print(f"{'region':<22}{'tile':>6}{'n_ours':>10}{'n_usda':>10}{'ratio':>7}{'IoU':>8}")
    print("-" * 63)
    ious = []
    for r in results:
        if "error" in r:
            print(f"{r['region']:<22}{r['tile']:>6}  ERROR")
            continue
        ratio = r.get("ratio_polys")
        iou = r.get("iou")
        ious.append(iou) if iou is not None else None
        print(
            f"{r['region']:<22}{r['tile']:>6}"
            f"{r['n_ours']:>10,}{r['n_usda']:>10,}"
            f"{(ratio or 0):>7.2f}{(iou or 0):>8.3f}"
        )
    if ious:
        print(
            f"\nIoU stats — n={len(ious)}, mean={np.mean(ious):.3f}, "
            f"median={np.median(ious):.3f}, min={min(ious):.3f}, max={max(ious):.3f}"
        )
    print(f"\nReport: {args.report}")


if __name__ == "__main__":
    main()
