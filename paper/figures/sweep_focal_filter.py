"""Sweep USDA-style focal-mode filter parameters on Iowa I15.

For each (radius, min_patch_size, iterations, final_pass_radius) combo:
1. Run polygonize directly via run_polygonize() (single-tile, single-process)
2. Compute per-field IoU vs USDA CSB1825 best-match
3. Report polygon count + IoU stats

Lets us pick the best filter parameters before committing to a CONUS rerun.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from csb.parity import find_bbox_5070  # noqa: E402
from csb.polygonize import run_polygonize  # noqa: E402

USDA = ROOT / "data" / "CSB1825_indexed.parquet"
NATIONAL_CDL = ROOT / "data" / "input" / "national_cdl"
OUT_ROOT = ROOT / "data" / "output" / "filter_sweep"
RESULTS = Path(__file__).resolve().parent / "filter_sweep_results.json"

TILE = "I15"
TX, TY = -100_000, 1_950_000
BBOX = find_bbox_5070(TX, TY)

# (id, radius, min_patch, iters, final_radius, label)
SWEEPS = [
    ("baseline", 0, 5, 0, 0, "no filter (baseline)"),
    ("r1_p3_i2",  1, 3, 2, 0, "r=1 patch<3 i=2 (mild)"),
    ("r1_p5_i4",  1, 5, 4, 0, "r=1 patch<5 i=4"),
    ("r2_p5_i4",  2, 5, 4, 0, "r=2 patch<5 i=4 (USDA-equiv at 30m)"),
    ("r2_p10_i4", 2, 10, 4, 0, "r=2 patch<10 i=4"),
    ("r2_p10_i8", 2, 10, 8, 0, "r=2 patch<10 i=8 (USDA iters)"),
    ("r2_p5_i4_f1", 2, 5, 4, 1, "r=2 patch<5 i=4 + final r=1"),
    ("r3_p10_i4", 3, 10, 4, 0, "r=3 patch<10 i=4 (aggressive)"),
]


def per_field_iou(ours_parquet: Path) -> dict:
    bx0, by0, bx1, by1 = BBOX
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET threads=32;")
    conn.execute(
        f"""
        CREATE TABLE ours AS
        SELECT row_number() OVER () AS oid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area
        FROM read_parquet('{ours_parquet}')
        WHERE ST_Intersects(geometry, {env})
        """
    )
    conn.execute(
        f"""
        CREATE TABLE usda AS
        SELECT row_number() OVER () AS uid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area
        FROM read_parquet('{USDA}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
        """
    )
    n_ours = conn.execute("SELECT COUNT(*) FROM ours").fetchone()[0]
    n_usda = conn.execute("SELECT COUNT(*) FROM usda").fetchone()[0]

    conn.execute(
        """
        CREATE TABLE pairs AS
        SELECT u.uid, o.oid, ST_Area(ST_Intersection(u.g, o.g)) AS inter,
               u.area AS u_area, o.area AS o_area
        FROM usda u JOIN ours o ON ST_Intersects(u.g, o.g)
        """
    )
    conn.execute(
        """
        CREATE TABLE best AS
        SELECT uid, inter / (u_area + o_area - inter) AS iou
        FROM (
            SELECT *, row_number() OVER (PARTITION BY uid ORDER BY inter DESC) AS rn
            FROM pairs
        ) WHERE rn = 1
        """
    )
    rows = conn.execute("SELECT iou FROM best").fetchall()
    import statistics as s

    ious = [r[0] for r in rows]
    if not ious:
        return {"n_ours": n_ours, "n_usda": n_usda, "matched": 0}
    near = sum(1 for x in ious if x >= 0.9)
    poor = sum(1 for x in ious if x < 0.5)
    return {
        "n_ours": int(n_ours),
        "n_usda": int(n_usda),
        "matched": len(ious),
        "iou_mean": round(s.mean(ious), 4),
        "iou_median": round(s.median(ious), 4),
        "share_near": round(near / n_usda, 4),
        "share_poor": round(poor / n_usda, 4),
    }


def run_one(sweep_id: str, radius: int, min_patch: int, iters: int, final_radius: int, label: str) -> dict:
    out = OUT_ROOT / sweep_id
    target = out / f"{TILE}.parquet"
    if target.exists():
        print(f"[{sweep_id}] cached")
    else:
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)
        print(f"[{sweep_id}] running polygonize ...")
        t0 = time.perf_counter()
        run_polygonize(
            start_year=2018,
            end_year=2025,
            output_dir=out,
            national_cdl_dir=NATIONAL_CDL,
            area=TILE,
            phase1_workers=1,
            phase2_workers=1,
            focal_radius=radius,
            focal_min_patch=min_patch,
            focal_iterations=iters,
            focal_final_pass_radius=final_radius,
        )
        elapsed = time.perf_counter() - t0
        print(f"[{sweep_id}] wall {elapsed:.1f} s")
    metrics = per_field_iou(target)
    return {
        "id": sweep_id,
        "label": label,
        "radius": radius,
        "min_patch": min_patch,
        "iters": iters,
        "final_radius": final_radius,
        **metrics,
    }


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for sweep_id, radius, min_patch, iters, final_radius, label in SWEEPS:
        r = run_one(sweep_id, radius, min_patch, iters, final_radius, label)
        print(json.dumps(r, indent=2))
        results.append(r)
        # Stream to disk so partial results survive a crash
        RESULTS.write_text(json.dumps(results, indent=2))

    print("\n=== summary ===")
    print(
        f"{'id':<14} {'mean':>6} {'median':>7} {'near%':>6} {'poor%':>6} {'n_ours':>8} {'label':<40}"
    )
    for r in results:
        print(
            f"{r['id']:<14} "
            f"{r.get('iou_mean', 0):>6.3f} "
            f"{r.get('iou_median', 0):>7.3f} "
            f"{r.get('share_near', 0)*100:>5.1f}% "
            f"{r.get('share_poor', 0)*100:>5.1f}% "
            f"{r.get('n_ours', 0):>8,} "
            f"{r['label']:<40}"
        )


if __name__ == "__main__":
    main()
