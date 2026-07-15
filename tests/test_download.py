"""Tests for csb.download."""

from pathlib import Path

import pytest

from csb.download import CDL_BASE_URL, cdl_url, download_cdl


def test_cdl_url_30m() -> None:
    url = cdl_url(2022, 30)
    assert url == f"{CDL_BASE_URL}/2022_30m_cdls.zip"


def test_cdl_url_10m() -> None:
    url = cdl_url(2025, 10)
    assert url == f"{CDL_BASE_URL}/2025_10m_cdls.zip"


def test_cdl_url_10m_unsupported_year() -> None:
    with pytest.raises(ValueError, match="10m CDL only available"):
        cdl_url(2020, 10)


def test_cdl_url_too_old() -> None:
    with pytest.raises(ValueError, match="not available before"):
        cdl_url(2005)


def test_download_cdl_skips_existing(tmp_path: Path) -> None:
    """Already-extracted TIF should be skipped."""
    year_dir = tmp_path / "2022"
    year_dir.mkdir()
    tif = year_dir / "2022_30m_cdls.tif"
    tif.write_bytes(b"fake tif")

    paths = download_cdl([2022], tmp_path, resolution=30)
    assert len(paths) == 1
    assert paths[0] == tif
