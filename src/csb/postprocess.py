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
import pyarrow as pa
from rich.console import Console

from csb.config import ACRES_PER_SQM, DEFAULT_BOUNDARIES_PATH, DEFAULT_CPU_FRACTION, STATE_FIPS
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
    start_year: int = params["start_year"]
    end_year: int = params["end_year"]
    output_dir = Path(params["output_dir"])
    boundaries_path = Path(params["boundaries"])

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


def _dissolve_tile_edges(conn: duckdb.DuckDBPyConnection, start_year: int, end_year: int) -> int:
    """Dissolve adjacent polygons with identical CDL{year} sequences.

    Polygonize runs per 5000-px tile and emits independent polygons per tile;
    a single field straddling a tile boundary becomes two records. This step
    finds pairs of touching polygons whose entire CDL{year} sequence agrees
    and merges each connected group into one (Multi)Polygon. Returns the
    number of features removed (i.e. dissolved into a neighbor).
    """
    # Intersect requested years with columns actually present (defensive
    # against fixtures / partial schemas).
    present = {r[0] for r in conn.execute("DESCRIBE SELECT * FROM national LIMIT 0").fetchall()}
    years = [y for y in range(start_year, end_year + 1) if f"CDL{y}" in present]
    if not years:
        return 0
    # DuckDB's binder errors on wide self-joins that also reference a spatial
    # predicate (`ST_Touches`) — see github.com/duckdb/duckdb-spatial #389.
    # Workaround: spatial-join on geometry+oid only, then re-join attributes.
    cdl_eq = " AND ".join(f"na.CDL{y} = nb.CDL{y}" for y in years)
    pairs = conn.execute(f"""
        WITH geom_pairs AS (
            SELECT a.national_oid AS a_id, b.national_oid AS b_id
            FROM national a JOIN national b
              ON a.national_oid < b.national_oid
             AND ST_Touches(a.geometry, b.geometry)
        )
        SELECT gp.a_id, gp.b_id
        FROM geom_pairs gp
        JOIN national na ON na.national_oid = gp.a_id
        JOIN national nb ON nb.national_oid = gp.b_id
        WHERE na.STATEFIPS = nb.STATEFIPS AND {cdl_eq}
    """).fetchall()
    if not pairs:
        return 0

    # Union-find over the candidate pairs to compute connected components.
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for a, b in pairs:
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        union(a, b)

    # roots[id] = canonical id for each member of a multi-member group.
    roots = [(oid, find(oid)) for oid in parent]
    # Stable across re-runs: pick the smallest national_oid as the root.
    roots_table = pa.table(
        {
            "national_oid": pa.array([r[0] for r in roots], type=pa.int64()),
            "root_oid": pa.array([r[1] for r in roots], type=pa.int64()),
        }
    )
    conn.register("dissolve_roots", roots_table)
    n_groups = len({r for _, r in roots})
    n_members = len(roots)

    # GROUP BY root, ST_Union_Agg geometry, keep first attribute values.
    conn.execute("""
        CREATE OR REPLACE TABLE national_dissolved AS
        WITH groups AS (
            SELECT n.*, COALESCE(r.root_oid, n.national_oid) AS root_oid
            FROM national n LEFT JOIN dissolve_roots r USING (national_oid)
        )
        SELECT
            ANY_VALUE(STATEFIPS) AS STATEFIPS,
            ANY_VALUE(STATEASD) AS STATEASD,
            ANY_VALUE(ASD) AS ASD,
            ANY_VALUE(CNTY) AS CNTY,
            ANY_VALUE(CNTYFIPS) AS CNTYFIPS,
            ANY_VALUE(CSBYEARS) AS CSBYEARS,
            ANY_VALUE(effective_count) AS effective_count,
            * EXCLUDE (
                STATEFIPS, STATEASD, ASD, CNTY, CNTYFIPS, CSBYEARS,
                effective_count, geometry, national_oid, root_oid
            ),
            ST_Union_Agg(geometry) AS geometry,
            MIN(national_oid) AS national_oid
        FROM groups
        GROUP BY root_oid, ALL
    """)
    conn.execute("DROP TABLE national; ALTER TABLE national_dissolved RENAME TO national")
    # Re-number national_oid 1..N by spatial-stable order (smallest old oid first).
    conn.execute("""
        CREATE OR REPLACE TABLE national_renumbered AS
        SELECT * EXCLUDE (national_oid),
               ROW_NUMBER() OVER (ORDER BY national_oid) AS national_oid
        FROM national
    """)
    conn.execute("DROP TABLE national; ALTER TABLE national_renumbered RENAME TO national")
    return n_members - n_groups


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
    *,
    start_year: int,
    end_year: int,
    polygonize_dir: str | Path,
    output_dir: str | Path,
    boundaries_path: str | Path = DEFAULT_BOUNDARIES_PATH,
    cpu_fraction: float = DEFAULT_CPU_FRACTION,
) -> Path:
    """Enrich polygonize-stage tiles and split into national + per-state outputs."""
    console = Console()
    polygonize_dir = Path(polygonize_dir)
    output_dir = Path(output_dir)
    boundaries_path = Path(boundaries_path)

    enrich_dir = output_dir / "enrich"
    enrich_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("national", "state"):
        (output_dir / sub).mkdir(parents=True, exist_ok=True)

    parquet_files = sorted(polygonize_dir.glob("*.parquet"))
    console.print(f"POSTPROCESS: {len(parquet_files)} tiles from POLYGONIZE")

    done = {f.stem for f in enrich_dir.glob("*.parquet")}
    remaining = [f for f in parquet_files if f.stem not in done]

    if remaining:
        n_workers = worker_count(cpu_fraction)
        console.print(f"  Enrich: {len(remaining)} tiles, {n_workers} workers")

        params = {
            "boundaries": str(boundaries_path),
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

    console.print("Dissolving polygons across tile boundaries...")
    t_dissolve = time.perf_counter()
    n_dissolved = _dissolve_tile_edges(conn, start_year, end_year)
    if n_dissolved:
        console.print(
            f"  merged {n_dissolved:,} cross-tile fragments "
            f"in {time.perf_counter() - t_dissolve:.1f}s"
        )

    console.print("Computing CSBID, CSBACRES, INSIDE_X/Y...")
    _compute_fields(conn)

    national_parquet = output_dir / "national" / f"CSB{csb_tag}.parquet"
    national_table = conn.execute("SELECT * FROM national").arrow().read_all()
    write_geoparquet(national_table, national_parquet)
    conn.close()
    console.print(f"National parquet: {national_parquet}")

    n_workers = worker_count(cpu_fraction)
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
