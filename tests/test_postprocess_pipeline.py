"""Tests for csb.postprocess — _spatial_join_boundaries, _build_national,
_compute_fields, _export_state, and run_postprocess."""

from pathlib import Path
from unittest.mock import patch

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely import box, to_wkb

from csb.io import write_geoparquet
from csb.postprocess import (
    _build_national,
    _compute_fields,
    _export_state,
    _spatial_join_boundaries,
    run_postprocess,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_area_table(conn: duckdb.DuckDBPyConnection, tmp_path: Path, n: int = 3) -> None:
    """Create a small area table in DuckDB with row_id and geometry (via GeoParquet)."""
    geoms = [to_wkb(box(i * 100, 0, (i + 1) * 100, 100)) for i in range(n)]
    table = pa.table(
        {
            "geometry": pa.array(geoms, type=pa.binary()),
            "effective_count": pa.array(list(range(1, n + 1)), type=pa.int32()),
        }
    )
    area_path = tmp_path / "_area_tmp.parquet"
    write_geoparquet(table, area_path)
    conn.execute(
        f"CREATE TABLE area AS SELECT *, ROW_NUMBER() OVER () AS row_id FROM '{area_path}'"
    )


def _make_boundaries_parquet(path: Path) -> Path:
    """Create a boundary parquet that covers the test area."""
    geom = to_wkb(box(-100, -100, 1000, 1000))
    table = pa.table(
        {
            "geometry": pa.array([geom], type=pa.binary()),
            "STATEFIPS": pa.array(["17"], type=pa.string()),
            "STATEASD": pa.array(["1710"], type=pa.string()),
            "ASD": pa.array(["10"], type=pa.string()),
            "CNTY": pa.array(["Cook"], type=pa.string()),
            "CNTYFIPS": pa.array(["031"], type=pa.string()),
        }
    )
    write_geoparquet(table, path)
    return path


def _make_enriched_parquet(
    path: Path, n: int = 5, statefips: str = "17", csbyears: str = "2024"
) -> Path:
    """Create a small enriched parquet (as produced by _enrich_tile)."""
    geoms = [to_wkb(box(i * 100, 0, (i + 1) * 100, 100)) for i in range(n)]
    table = pa.table(
        {
            "geometry": pa.array(geoms, type=pa.binary()),
            "effective_count": pa.array(list(range(1, n + 1)), type=pa.int32()),
            "STATEFIPS": pa.array([statefips] * n, type=pa.string()),
            "STATEASD": pa.array(["1710"] * n, type=pa.string()),
            "ASD": pa.array(["10"] * n, type=pa.string()),
            "CNTY": pa.array(["Cook"] * n, type=pa.string()),
            "CNTYFIPS": pa.array(["031"] * n, type=pa.string()),
            "CSBYEARS": pa.array([csbyears] * n, type=pa.string()),
            "CSBID": pa.array([""] * n, type=pa.string()),
        }
    )
    write_geoparquet(table, path)
    return path


def _make_national_parquet(path: Path, n: int = 3, statefips: str = "17") -> Path:
    """Create a national-like parquet with national_oid and computed fields."""
    geoms = [to_wkb(box(i * 100, 0, (i + 1) * 100, 100)) for i in range(n)]
    table = pa.table(
        {
            "geometry": pa.array(geoms, type=pa.binary()),
            "effective_count": pa.array(list(range(1, n + 1)), type=pa.int32()),
            "STATEFIPS": pa.array([statefips] * n, type=pa.string()),
            "CSBYEARS": pa.array(["2024"] * n, type=pa.string()),
            "CSBID": pa.array(
                [f"{statefips}2024{str(i).zfill(9)}" for i in range(1, n + 1)], type=pa.string()
            ),
            "CSBACRES": pa.array([10.0] * n, type=pa.float64()),
            "INSIDE_X": pa.array([50.0 + i * 100 for i in range(n)], type=pa.float64()),
            "INSIDE_Y": pa.array([50.0] * n, type=pa.float64()),
            "national_oid": pa.array(list(range(1, n + 1)), type=pa.int64()),
        }
    )
    write_geoparquet(table, path)
    return path


# ---------------------------------------------------------------------------
# _spatial_join_boundaries
# ---------------------------------------------------------------------------


def test_spatial_join_boundaries(tmp_path: Path) -> None:
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    _make_area_table(conn, tmp_path, n=3)
    boundary_path = _make_boundaries_parquet(tmp_path / "boundaries.parquet")

    _spatial_join_boundaries(conn, boundary_path)

    count = conn.execute("SELECT COUNT(*) FROM area").fetchone()
    assert count is not None
    assert count[0] == 3

    cols = [row[0] for row in conn.execute("DESCRIBE area").fetchall()]
    assert "STATEFIPS" in cols
    assert "CNTYFIPS" in cols
    conn.close()


def test_spatial_join_no_overlap(tmp_path: Path) -> None:
    """Boundaries far from area polygons → empty result."""
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    _make_area_table(conn, tmp_path, n=2)

    geom = to_wkb(box(10000, 10000, 20000, 20000))
    table = pa.table(
        {
            "geometry": pa.array([geom], type=pa.binary()),
            "STATEFIPS": pa.array(["06"], type=pa.string()),
            "STATEASD": pa.array(["0610"], type=pa.string()),
            "ASD": pa.array(["10"], type=pa.string()),
            "CNTY": pa.array(["LA"], type=pa.string()),
            "CNTYFIPS": pa.array(["037"], type=pa.string()),
        }
    )
    boundary_path = tmp_path / "boundaries_far.parquet"
    write_geoparquet(table, boundary_path)

    _spatial_join_boundaries(conn, boundary_path)

    count = conn.execute("SELECT COUNT(*) FROM area").fetchone()
    assert count is not None
    assert count[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# _build_national / _compute_fields
# ---------------------------------------------------------------------------


def test_build_national(tmp_path: Path) -> None:
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir()
    _make_enriched_parquet(enrich_dir / "area1.parquet", n=3)
    _make_enriched_parquet(enrich_dir / "area2.parquet", n=4)

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    count = _build_national(conn, enrich_dir)
    assert count == 7

    cols = [row[0] for row in conn.execute("DESCRIBE national").fetchall()]
    assert "national_oid" in cols
    conn.close()


def test_build_national_empty(tmp_path: Path) -> None:
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir()

    conn = duckdb.connect()
    with pytest.raises(FileNotFoundError, match="No enriched parquets"):
        _build_national(conn, enrich_dir)
    conn.close()


def test_compute_fields(tmp_path: Path) -> None:
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir()
    _make_enriched_parquet(enrich_dir / "area1.parquet", n=3)

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    _build_national(conn, enrich_dir)
    _compute_fields(conn)

    result = conn.execute(
        "SELECT CSBACRES, INSIDE_X, INSIDE_Y, CSBID FROM national LIMIT 1"
    ).fetchone()
    assert result is not None
    csbacres, inside_x, inside_y, csbid = result
    assert csbacres > 0
    assert inside_x is not None
    assert inside_y is not None
    assert len(csbid) > 0
    conn.close()


def test_compute_fields_csbid_format(tmp_path: Path) -> None:
    """CSBID should be STATEFIPS + CSBYEARS + zero-padded national_oid."""
    enrich_dir = tmp_path / "enrich"
    enrich_dir.mkdir()
    _make_enriched_parquet(enrich_dir / "area1.parquet", n=2, statefips="17", csbyears="2024")

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")

    _build_national(conn, enrich_dir)
    _compute_fields(conn)

    rows = conn.execute(
        "SELECT CSBID, STATEFIPS, CSBYEARS FROM national ORDER BY national_oid"
    ).fetchall()
    for csbid, fips, csbyears in rows:
        assert csbid.startswith(fips)
        assert csbyears in csbid
    conn.close()


# ---------------------------------------------------------------------------
# _export_state
# ---------------------------------------------------------------------------


def test_export_state(tmp_path: Path) -> None:
    """_export_state should produce a GeoParquet file for the state."""
    national_path = tmp_path / "national.parquet"
    _make_national_parquet(national_path, n=3, statefips="17")

    output_dir = tmp_path / "state"
    output_dir.mkdir(parents=True)

    params = {
        "national_parquet": str(national_path),
        "output_dir": str(output_dir),
        "csb_tag": "2024",
    }

    result = _export_state("IL", "17", params)
    assert "Finished" in result
    assert "3 features" in result

    parquet_out = output_dir / "CSBIL2024.parquet"
    assert parquet_out.exists()
    table = pq.read_table(parquet_out)
    assert table.num_rows == 3


def test_export_state_no_data(tmp_path: Path) -> None:
    """_export_state with non-matching FIPS should skip."""
    national_path = tmp_path / "national.parquet"
    _make_national_parquet(national_path, n=3, statefips="17")

    output_dir = tmp_path / "state"
    output_dir.mkdir(parents=True)

    params = {
        "national_parquet": str(national_path),
        "output_dir": str(output_dir),
        "csb_tag": "2024",
    }

    result = _export_state("CA", "06", params)
    assert "Skipped" in result


# ---------------------------------------------------------------------------
# run_postprocess (integration)
# ---------------------------------------------------------------------------


def test_run_postprocess(tmp_path: Path) -> None:
    """Full run_postprocess with pre-enriched tiles (enrich phase skipped)."""
    polygonize_dir = tmp_path / "polygonize"
    polygonize_dir.mkdir()
    output_dir = tmp_path / "output"

    # Pre-populate enrich dir so the enrich phase is skipped
    enrich_dir = output_dir / "enrich"
    enrich_dir.mkdir(parents=True)
    _make_enriched_parquet(enrich_dir / "area1.parquet", n=3, statefips="17", csbyears="2024")

    # Matching stub in polygonize_dir (so done set matches)
    (polygonize_dir / "area1.parquet").touch()

    with (
        patch("csb.postprocess.STATE_FIPS", {"IL": "17"}),
        patch(
            "csb.utils.parallel_starmap",
            side_effect=lambda fn, items, **kw: [fn(*args) for args in items],
        ),
    ):
        result = run_postprocess(
            start_year=2020,
            end_year=2024,
            polygonize_dir=polygonize_dir,
            output_dir=output_dir,
            boundaries_path="/fake",
            cpu_fraction=0.5,
        )

    assert result.exists()
    national = output_dir / "national" / "CSB2024.parquet"
    assert national.exists()


def test_run_postprocess_skips_done_enrich(tmp_path: Path) -> None:
    """When all tiles already enriched, enrich phase is skipped."""
    polygonize_dir = tmp_path / "polygonize"
    polygonize_dir.mkdir()
    output_dir = tmp_path / "output"
    enrich_dir = output_dir / "enrich"
    enrich_dir.mkdir(parents=True)

    _make_enriched_parquet(enrich_dir / "T1.parquet", n=2, statefips="17", csbyears="2021")
    (polygonize_dir / "T1.parquet").touch()

    with (
        patch("csb.postprocess.STATE_FIPS", {"IL": "17"}),
        patch(
            "csb.utils.parallel_starmap",
            side_effect=lambda fn, items, **kw: [fn(*args) for args in items],
        ),
    ):
        result = run_postprocess(
            start_year=2020,
            end_year=2021,
            polygonize_dir=polygonize_dir,
            output_dir=output_dir,
            boundaries_path="/fake",
            cpu_fraction=0.5,
        )

    assert result == output_dir
