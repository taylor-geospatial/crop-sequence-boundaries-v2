"""One-time prep: rewrite both sides for spatial-pushdown parity.

For each input (our CONUS national parquet + USDA gdb), we:

1. Add explicit bbox columns (xmin, ymin, xmax, ymax) computed from each
   polygon's geometry envelope.
2. Sort rows by Hilbert curve over the centroid so neighboring row groups
   in the output parquet contain spatially-local features.
3. Write back as parquet with a small row-group size.

After this, DuckDB row-group stats pruning kicks in for any bbox WHERE
predicate — a 150km tile bbox lookup against CONUS reads ~1% of the file
instead of the whole thing.

Outputs:
    data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet
    data/CSB1825_indexed.parquet  (USDA, converted from gdb)

Run via: srun ... uv run python scripts/prep_parity_inputs.py
"""

import sys
import time
from pathlib import Path

import duckdb


def prep_ours(conn: duckdb.DuckDBPyConnection, src: Path, dst: Path) -> None:
    print(f"prep ours: {src} -> {dst}", flush=True)
    t0 = time.perf_counter()
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
            ORDER BY ST_Hilbert(
                geometry,
                {{'min_x': -2356095.0, 'min_y': 270000.0,
                  'max_x': 2260000.0, 'max_y': 3175000.0}}::BOX_2D
            )
        ) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
    """)
    print(
        f"  done in {time.perf_counter() - t0:.1f}s, {dst.stat().st_size / 1e9:.2f} GB", flush=True
    )


def prep_usda(conn: duckdb.DuckDBPyConnection, gdb: Path, dst: Path) -> None:
    print(f"prep usda: {gdb} -> {dst}", flush=True)
    t0 = time.perf_counter()
    # Stage 1: load gdb into a temp table with bbox columns. Skip ST_MakeValid
    # — USDA polygons are valid; cheaper to handle invalidity at query time.
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
    n = conn.execute("SELECT COUNT(*) FROM usda_raw").fetchone()
    assert n is not None
    print(f"  loaded {n[0]:,} USDA features in {time.perf_counter() - t0:.1f}s", flush=True)

    t0 = time.perf_counter()
    conn.execute(f"""
        COPY (
            SELECT * FROM usda_raw
            ORDER BY ST_Hilbert(
                geometry,
                {{'min_x': -2356095.0, 'min_y': 270000.0,
                  'max_x': 2260000.0, 'max_y': 3175000.0}}::BOX_2D
            )
        ) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
    """)
    print(
        f"  wrote {dst.stat().st_size / 1e9:.2f} GB in {time.perf_counter() - t0:.1f}s", flush=True
    )


def main() -> None:
    # Subset selector ("ours", "usda", "both") for testing.
    which = sys.argv[1] if len(sys.argv) > 1 else "both"

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    # Use most of the node — sort + bbox compute parallelize well.
    conn.execute("PRAGMA threads=32")

    ours_src = Path("data/output/conus/postprocess/2018_2025/national/CSB1825.parquet")
    ours_dst = Path("data/output/conus/postprocess/2018_2025/national/CSB1825_indexed.parquet")
    usda_gdb = Path("data/CSB1825.gdb")
    usda_dst = Path("data/CSB1825_indexed.parquet")

    if which in ("ours", "both"):
        prep_ours(conn, ours_src, ours_dst)
    if which in ("usda", "both"):
        prep_usda(conn, usda_gdb, usda_dst)
    conn.close()


if __name__ == "__main__":
    main()
