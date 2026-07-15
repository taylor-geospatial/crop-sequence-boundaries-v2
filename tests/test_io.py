"""Tests for csb.io."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from shapely import Point, to_wkb

from csb.io import write_geoparquet


def test_write_geoparquet(tmp_path: Path) -> None:
    geom = to_wkb(Point(100, 200))
    table = pa.table(
        {
            "geometry": pa.array([geom], type=pa.binary()),
            "value": pa.array([42], type=pa.int32()),
        }
    )
    path = write_geoparquet(table, tmp_path / "test.parquet")
    assert path.exists()

    # Verify geo metadata
    meta = pq.read_schema(path).metadata
    geo = json.loads(meta[b"geo"])
    assert geo["primary_column"] == "geometry"
    assert geo["columns"]["geometry"]["encoding"] == "WKB"


def test_write_geoparquet_roundtrip(tmp_path: Path) -> None:
    geom = to_wkb(Point(100, 200))
    table = pa.table(
        {
            "geometry": pa.array([geom], type=pa.binary()),
            "name": pa.array(["test"], type=pa.string()),
        }
    )
    path = write_geoparquet(table, tmp_path / "roundtrip.parquet")

    result = pq.read_table(path)
    assert result.num_rows == 1
    assert result.column("name")[0].as_py() == "test"
