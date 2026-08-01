"""Tests for the pure logic in csb.tiger (no network)."""

import zipfile
from pathlib import Path

from csb.tiger import DEFAULT_MTFCC, tiger_url, unzip, valid_zip


def test_tiger_url_construction() -> None:
    assert (
        tiger_url("ROADS", "tl_2025_19001_roads")
        == "https://www2.census.gov/geo/tiger/TIGER2025/ROADS/tl_2025_19001_roads.zip"
    )
    assert (
        tiger_url("COUNTY", "tl_2020_us_county", 2020)
        == "https://www2.census.gov/geo/tiger/TIGER2020/COUNTY/tl_2020_us_county.zip"
    )


def test_valid_zip_accepts_intact_archive(tmp_path: Path) -> None:
    z = tmp_path / "ok.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "hello")
    assert valid_zip(z)


def test_valid_zip_rejects_html_stub(tmp_path: Path) -> None:
    # Census www2 can return a 200 with an HTML error page; not a zip at all.
    stub = tmp_path / "stub.zip"
    stub.write_text("<html><body>520</body></html>")
    assert not valid_zip(stub)


def test_valid_zip_rejects_truncated_archive(tmp_path: Path) -> None:
    z = tmp_path / "full.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "x" * 10_000)
    truncated = tmp_path / "trunc.zip"
    truncated.write_bytes(z.read_bytes()[:100])
    assert not valid_zip(truncated)


def test_unzip_extracts_and_returns_shp(tmp_path: Path) -> None:
    z = tmp_path / "tl_2025_19001_roads.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("tl_2025_19001_roads.shp", b"\x00")
        zf.writestr("tl_2025_19001_roads.dbf", b"\x00")
    shp = unzip(z)
    assert shp.name == "tl_2025_19001_roads.shp"
    assert shp.parent == tmp_path / "tl_2025_19001_roads"


def test_default_mtfcc_excludes_field_access_tracks() -> None:
    # S1500 (4WD trails) would split fields USDA keeps whole.
    assert "S1500" not in DEFAULT_MTFCC
    assert DEFAULT_MTFCC == ("S1100", "S1200", "S1400")
