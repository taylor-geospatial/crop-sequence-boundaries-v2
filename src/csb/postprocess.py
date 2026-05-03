"""Postprocess stage: enrich polygons with boundary attributes, then distribute.

Per tile, in parallel: spatial-join to county/ASD boundaries (largest overlap),
write enriched GeoParquet. CDL{year} columns arrive pre-computed from
polygonize phase 1. Then nationally: merge all tiles, derive CSBID/CSBACRES/
INSIDE_X,Y, write the national GeoParquet, split by state.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import duckdb
from rich.console import Console

from csb.config import ACRES_PER_SQM, STATE_FIPS
from csb.io import write_geoparquet
from csb.utils import parallel_map, parallel_starmap, worker_count

logger = logging.getLogger(__name__)


def _spatial_join_boundaries(
    conn: duckdb.DuckDBPyConnection,
    boundaries_path: Path,
) -> None:
    """Spatial-join area polygons to county/ASD boundaries, picking largest overlap."""
    suffix = boundaries_path.suffix.lower()
    if suffix == ".parquet":
        conn.execute(f"CREATE TABLE boundaries AS SELECT * FROM '{boundaries_path}'")
    else:
        conn.execute(f"CREATE TABLE boundaries AS SELECT * FROM ST_Read('{boundaries_path}')")

    # ST_MakeValid both sides — coverage_simplify can produce degenerate edges
    # and TIGER counties occasionally have self-intersecting rings.
    conn.execute("""
        CREATE TABLE area_joined AS
        WITH ranked AS (
            SELECT
                a.*,
                b.STATEFIPS,
                b.STATEASD,
                b.ASD,
                b.CNTY,
                b.CNTYFIPS,
                ROW_NUMBER() OVER (
                    PARTITION BY a.row_id
                    ORDER BY ST_Area(ST_Intersection(
                        ST_MakeValid(a.geometry),
                        ST_MakeValid(b.geometry)
                    )) DESC
                ) AS rn
            FROM area a
            JOIN boundaries b
            ON ST_Intersects(a.geometry, b.geometry)
        )
        SELECT * EXCLUDE (rn) FROM ranked WHERE rn = 1
    """)
    conn.execute("DROP TABLE area; ALTER TABLE area_joined RENAME TO area")


def _enrich_tile(args: tuple[Path, dict[str, Any]]) -> str:
    """Enrich a single tile parquet with boundary join + zonal CDL stats."""
    parquet_path, params = args
    cfg = params["config"]
    start_year: int = params["start_year"]
    end_year: int = params["end_year"]
    output_dir = Path(params["output_dir"])
    boundaries_path = Path(cfg["paths"]["boundaries"])

    area_name = parquet_path.stem
    csb_years = f"{str(start_year)[2:]}{str(end_year)[2:]}"
    t0 = time.perf_counter()

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    logger.info("%s: Loading and joining boundaries", area_name)
    conn.execute(
        f"CREATE TABLE area AS SELECT *, ROW_NUMBER() OVER () AS row_id FROM '{parquet_path}'"
    )
    conn.execute(f"""
        ALTER TABLE area ADD COLUMN CSBYEARS VARCHAR DEFAULT '{csb_years}';
        ALTER TABLE area ADD COLUMN CSBID VARCHAR;
    """)

    _spatial_join_boundaries(conn, boundaries_path)

    row = conn.execute("SELECT COUNT(*) FROM area").fetchone()
    assert row is not None
    count = row[0]
    if count == 0:
        conn.close()
        return f"Skipped {area_name} (empty after join)"

    logger.info("%s: writing %s features -> GeoParquet", area_name, count)
    out_table = (
        conn.execute("SELECT * EXCLUDE (row_id) REPLACE (ST_AsWKB(geometry) AS geometry) FROM area")
        .arrow()
        .read_all()
    )
    conn.close()
    del conn

    out_path = output_dir / f"{area_name}.parquet"
    write_geoparquet(out_table, out_path)

    elapsed = (time.perf_counter() - t0) / 60
    logger.info("%s: Done in %.2f min", area_name, elapsed)
    return f"Finished {area_name} ({out_table.num_rows} features, {elapsed:.1f} min)"


def _build_national(conn: duckdb.DuckDBPyConnection, enrich_dir: Path) -> int:
    """Union all enriched parquets into a single national table. Returns row count."""
    parquets = sorted(enrich_dir.glob("*.parquet"))
    if not parquets:
        msg = f"No enriched parquets in {enrich_dir}"
        raise FileNotFoundError(msg)

    parts = [f"SELECT * FROM '{f}'" for f in parquets]
    conn.execute(f"""
        CREATE TABLE national AS
        SELECT *, ROW_NUMBER() OVER () AS national_oid
        FROM ({" UNION ALL ".join(parts)})
    """)
    row = conn.execute("SELECT COUNT(*) FROM national").fetchone()
    assert row is not None
    return row[0]


def _compute_fields(conn: duckdb.DuckDBPyConnection) -> None:
    """Add derived fields: CSBACRES, INSIDE_X, INSIDE_Y, final CSBID."""
    conn.execute(f"""
        ALTER TABLE national ADD COLUMN IF NOT EXISTS CSBACRES DOUBLE;
        ALTER TABLE national ADD COLUMN IF NOT EXISTS Shape_area DOUBLE;
        ALTER TABLE national ADD COLUMN IF NOT EXISTS Shape_Length DOUBLE;
        UPDATE national SET
            Shape_area = ST_Area(geometry),
            Shape_Length = ST_Perimeter(geometry),
            CSBACRES = ST_Area(geometry) * {ACRES_PER_SQM};
    """)
    conn.execute("""
        ALTER TABLE national ADD COLUMN IF NOT EXISTS INSIDE_X DOUBLE;
        ALTER TABLE national ADD COLUMN IF NOT EXISTS INSIDE_Y DOUBLE;
        UPDATE national SET
            INSIDE_X = ST_X(ST_PointOnSurface(geometry)),
            INSIDE_Y = ST_Y(ST_PointOnSurface(geometry));
    """)
    conn.execute("""
        UPDATE national
        SET CSBID = STATEFIPS || CSBYEARS || LPAD(CAST(national_oid AS VARCHAR), 9, '0')
    """)


def _export_state(state: str, fips: str, params: dict[str, Any]) -> str:
    """Export a single state to GeoParquet."""
    national_parquet = Path(params["national_parquet"])
    output_dir = Path(params["output_dir"])
    csb_tag = params["csb_tag"]

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    state_table = (
        conn.execute(
            f"SELECT * EXCLUDE (national_oid) FROM '{national_parquet}' WHERE STATEFIPS = '{fips}'"
        )
        .arrow()
        .read_all()
    )
    conn.close()

    if state_table.num_rows == 0:
        return f"Skipped {state} (no data)"

    parquet_path = output_dir / f"CSB{state}{csb_tag}.parquet"
    write_geoparquet(state_table, parquet_path)

    logger.info("%s: %s features exported", state, state_table.num_rows)
    return f"Finished {state} ({state_table.num_rows} features)"


def run_postprocess(
    cfg: dict[str, Any],
    start_year: int,
    end_year: int,
    polygonize_dir: str | Path,
    output_dir: str | Path,
) -> Path:
    """Enrich polygonize-stage tiles and split into national + per-state outputs."""
    console = Console()
    polygonize_dir = Path(polygonize_dir)
    output_dir = Path(output_dir)

    enrich_dir = output_dir / "enrich"
    enrich_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("national", "state"):
        (output_dir / sub).mkdir(parents=True, exist_ok=True)

    parquet_files = sorted(polygonize_dir.glob("*.parquet"))
    console.print(f"POSTPROCESS: {len(parquet_files)} tiles from POLYGONIZE")

    done = {f.stem for f in enrich_dir.glob("*.parquet")}
    remaining = [f for f in parquet_files if f.stem not in done]

    if remaining:
        n_workers = worker_count(cfg["global"]["cpu_fraction"])
        console.print(f"  Enrich: {len(remaining)} tiles, {n_workers} workers")

        params = {
            "config": cfg,
            "start_year": start_year,
            "end_year": end_year,
            "output_dir": str(enrich_dir),
        }
        task_args = [(f, params) for f in remaining]
        enrich_results = parallel_map(_enrich_tile, task_args, max_workers=n_workers)
        for r in enrich_results:
            console.print(f"  {r}")
        console.print(f"[blue]Enrich complete: {len(enrich_results)} tiles")
    else:
        console.print("[green]All tiles already enriched.")

    csb_tag = f"{str(start_year)[2:]}{str(end_year)[2:]}"
    t0 = time.perf_counter()

    # Empty-input guard: ocean / pure-forest tiles legitimately produce zero polygons.
    enriched = sorted(enrich_dir.glob("*.parquet"))
    if not enriched:
        console.print("[yellow]No enriched tiles — nothing to merge.")
        return output_dir

    console.print("Merging tiles into national dataset...")
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    count = _build_national(conn, enrich_dir)
    console.print(f"National: {count} features in {(time.perf_counter() - t0) / 60:.2f} min")

    console.print("Computing CSBID, CSBACRES, INSIDE_X/Y...")
    _compute_fields(conn)

    national_parquet = output_dir / "national" / f"CSB{csb_tag}.parquet"
    national_table = conn.execute("SELECT * FROM national").arrow().read_all()
    write_geoparquet(national_table, national_parquet)
    conn.close()
    console.print(f"National parquet: {national_parquet}")

    n_workers = worker_count(cfg["global"]["cpu_fraction"])
    console.print(f"Distributing to {len(STATE_FIPS)} states with {n_workers} workers...")

    params_dist = {
        "national_parquet": str(national_parquet),
        "output_dir": str(output_dir / "state"),
        "csb_tag": csb_tag,
    }
    dist_results = parallel_starmap(
        _export_state,
        [(state, fips, params_dist) for state, fips in STATE_FIPS.items()],
        max_workers=n_workers,
    )
    for r in dist_results:
        console.print(f"  {r}")

    total = (time.perf_counter() - t0) / 60
    console.print(f"[bold magenta]POSTPROCESS complete in {total:.2f} min")
    return output_dir
