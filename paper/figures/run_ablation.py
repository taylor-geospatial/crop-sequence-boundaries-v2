"""Ablation + simplify-tolerance sweep on Iowa Q30 (tile I15).

Runs polygonize variants on a single 5000x5000 tile and compares each
against USDA CSB1825 over the same bbox using the inclusion-exclusion IoU.
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
TILE = "I15"
USDA = ROOT / "data" / "CSB1825_indexed.parquet"
OUT_ROOT = ROOT / "data" / "output" / "ablation"
RESULTS = Path(__file__).resolve().parent / "ablation_results.json"

I15_BBOX = (-106095.0, 1822605.0, 43905.0, 1972605.0)
USDA_ACRES_PER_M2 = 1.0 / 4046.8564224

VARIANTS = [
    {"id": "default",      "label": "default (s=60, dissolve)",         "simplify": 60.0,  "dissolve": True},
    {"id": "s30",          "label": "simplify 30 m",                    "simplify": 30.0,  "dissolve": True},
    {"id": "s120",         "label": "simplify 120 m",                   "simplify": 120.0, "dissolve": True},
    {"id": "no_dissolve",  "label": "no same-combo dissolve",           "simplify": 60.0,  "dissolve": False},
]


def run_polygonize(variant: dict) -> Path:
    out = OUT_ROOT / variant["id"]
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{TILE}.parquet"
    if target.exists():
        print(f"[{variant['id']}] cached at {target}")
        return target
    cmd = [
        "uv", "run", "csb", "polygonize", "2018", "2025",
        "--output", str(out),
        "--area", TILE,
        "--simplify-tolerance", str(variant["simplify"]),
        "--phase1-workers", "1",
        "--phase2-workers", "1",
    ]
    if not variant["dissolve"]:
        cmd.append("--no-same-combo-dissolve")
    print(f"[{variant['id']}] {' '.join(cmd)}")
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True, cwd=ROOT)
    elapsed = time.perf_counter() - t0
    variant["wall_sec"] = elapsed
    print(f"[{variant['id']}] wall {elapsed:.1f} s")
    return target


def parity(ours_parquet: Path) -> dict:
    bx0, by0, bx1, by1 = I15_BBOX
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET threads=16;")
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn.execute(
        f"CREATE TEMP TABLE ours AS "
        f"SELECT ST_MakeValid(geometry) AS g, ST_Area(geometry) AS a "
        f"FROM read_parquet('{ours_parquet}') "
        f"WHERE ST_Intersects(geometry, {env})"
    )
    conn.execute(
        f"CREATE TEMP TABLE usda AS "
        f"SELECT ST_MakeValid(geometry) AS g, CSBACRES "
        f"FROM read_parquet('{USDA}') "
        f"WHERE xmax >= {bx0} AND xmin <= {bx1} AND ymax >= {by0} AND ymin <= {by1} "
        f"  AND ST_Intersects(geometry, {env})"
    )
    n_ours = conn.execute("SELECT COUNT(*) FROM ours").fetchone()[0]
    n_usda = conn.execute("SELECT COUNT(*) FROM usda").fetchone()[0]
    acres_ours = conn.execute(f"SELECT SUM(ST_Area(ST_Intersection(g, {env}))) * {USDA_ACRES_PER_M2} FROM ours").fetchone()[0] or 0.0
    acres_usda = conn.execute("SELECT SUM(CSBACRES) FROM usda").fetchone()[0] or 0.0
    ours_km2 = conn.execute(f"SELECT SUM(ST_Area(ST_Intersection(g, {env})))/1e6 FROM ours").fetchone()[0] or 0.0
    usda_km2 = conn.execute(f"SELECT SUM(ST_Area(ST_Intersection(g, {env})))/1e6 FROM usda").fetchone()[0] or 0.0
    inter = conn.execute("SELECT SUM(ST_Area(ST_Intersection(o.g, u.g)))/1e6 FROM ours o JOIN usda u ON ST_Intersects(o.g, u.g)").fetchone()[0] or 0.0
    union = ours_km2 + usda_km2 - inter
    return {
        "n_ours": int(n_ours),
        "n_usda": int(n_usda),
        "ratio_polys": n_ours / n_usda if n_usda else None,
        "acres_ours": float(acres_ours),
        "acres_usda": float(acres_usda),
        "ratio_acres": acres_ours / acres_usda if acres_usda else None,
        "iou": inter / union if union else None,
    }


def main() -> None:
    rows = []
    for v in VARIANTS:
        path = run_polygonize(v)
        m = parity(path)
        rows.append({**v, **m})
        print(json.dumps(rows[-1], indent=2))
    RESULTS.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {RESULTS}")


if __name__ == "__main__":
    main()
