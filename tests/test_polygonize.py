"""Tests for csb.polygonize — windowed reads and combine logic."""

from typing import TYPE_CHECKING

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.windows import Window

from csb.config import BARREN_CODE
from csb.polygonize import _combine_years_windowed, _tile_windows

if TYPE_CHECKING:
    from pathlib import Path


def _make_national_cdl(base_dir: "Path", years: list[int], size: int = 20) -> None:
    """Create national CDL rasters for testing."""
    transform = from_bounds(0, 0, size * 30, size * 30, size, size)
    rng = np.random.default_rng(42)
    for year in years:
        year_dir = base_dir / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        data = rng.choice([0, 1, 5, 45, 61, 176], size=(size, size)).astype(np.uint8)
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "uint8",
            "width": size,
            "height": size,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)


def test_combine_years_windowed(tmp_path: "Path") -> None:
    """Stack windows across years and return combo_raster + effective_per_combo + cdl_per_combo_year."""
    years = [2020, 2021, 2022]
    _make_national_cdl(tmp_path, years, size=20)
    window = Window(0, 0, 20, 20)  # type: ignore[call-arg]

    combo_raster, effective_per_combo, cdl_per_combo_year, _transform = (
        _combine_years_windowed(tmp_path, years, window)
    )

    assert combo_raster.shape == (20, 20)
    assert combo_raster.dtype == np.int32
    assert effective_per_combo.ndim == 1
    assert effective_per_combo.dtype == np.int16
    assert combo_raster.min() >= 0
    assert combo_raster.max() < len(effective_per_combo)
    assert cdl_per_combo_year.shape == (len(effective_per_combo), len(years))
    assert cdl_per_combo_year.dtype == np.uint8


def test_combine_years_counts_correctly(tmp_path: "Path") -> None:
    """Verify effective_count per combo with deterministic single-combo data.

    All cells are the same N-year sequence: corn (1), barren-sentinel, then 0.
    Expected: 1 combo, effective_count = COUNT0 - COUNT_BARREN = 2 - 1 = 1.
    """
    transform = from_bounds(0, 0, 150, 150, 5, 5)
    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "width": 5,
        "height": 5,
        "count": 1,
        "crs": "EPSG:5070",
        "transform": transform,
        "nodata": 0,
    }

    year1_dir = tmp_path / "2020"
    year1_dir.mkdir()
    with rasterio.open(year1_dir / "2020_30m_cdls.tif", "w", **profile) as dst:
        dst.write(np.ones((5, 5), dtype=np.uint8), 1)

    year2_dir = tmp_path / "2021"
    year2_dir.mkdir()
    with rasterio.open(year2_dir / "2021_30m_cdls.tif", "w", **profile) as dst:
        dst.write(np.full((5, 5), BARREN_CODE, dtype=np.uint8), 1)

    year3_dir = tmp_path / "2022"
    year3_dir.mkdir()
    with rasterio.open(year3_dir / "2022_30m_cdls.tif", "w", **profile) as dst:
        dst.write(np.zeros((5, 5), dtype=np.uint8), 1)

    window = Window(0, 0, 5, 5)  # type: ignore[call-arg]
    combo_raster, effective_per_combo, cdl_per_combo_year, _transform = (
        _combine_years_windowed(tmp_path, [2020, 2021, 2022], window)
    )

    assert len(effective_per_combo) == 1
    assert effective_per_combo[0] == 1
    np.testing.assert_array_equal(combo_raster, np.zeros((5, 5), dtype=np.int32))
    # Per-combo CDL: year1=1 (corn), year2=BARREN, year3=0 (no data).
    assert cdl_per_combo_year[0, 0] == 1
    assert cdl_per_combo_year[0, 1] == BARREN_CODE
    assert cdl_per_combo_year[0, 2] == 0


def test_tile_windows_names() -> None:
    """Tile names follow A0, A1, ..., B0, ... pattern."""
    tiles = _tile_windows(100, 100, 50)
    names = [name for name, _ in tiles]
    assert names == ["A0", "A1", "B0", "B1"]
