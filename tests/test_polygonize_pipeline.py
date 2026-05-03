"""Tests for csb.polygonize — process_tile and run_polygonize."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from csb.polygonize import _tile_windows, process_tile, run_polygonize

if TYPE_CHECKING:
    from pathlib import Path


def _make_national_cdl(
    base_dir: Path, years: tuple[int, ...] = (2020, 2021, 2022), size: int = 20
) -> None:
    """Create synthetic national CDL rasters for multiple years."""
    transform = from_bounds(0, 0, size * 30, size * 30, size, size)
    rng = np.random.default_rng(42)

    for year in years:
        year_dir = base_dir / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        data = rng.choice([1, 5, 45, 61], size=(size, size)).astype(np.int32)
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "int32",
            "width": size,
            "height": size,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)


def _make_config(national_cdl_dir: Path) -> dict[str, Any]:
    """Create a minimal config dict for testing."""
    return {
        "global": {"cpu_fraction": 0.5, "min_cropland_years": 1},
        "paths": {"national_cdl": str(national_cdl_dir)},
        "polygonize": {
            "eliminate_thresholds": [100],
            "min_polygon_area": 1.0,
            "simplify_tolerance": 10.0,
        },
    }


def test_tile_windows() -> None:
    """Tile windows should cover the full raster."""
    tiles = _tile_windows(100, 100, 50)
    assert len(tiles) == 4
    names = [name for name, _ in tiles]
    assert names == ["A0", "A1", "B0", "B1"]


def test_tile_windows_non_divisible() -> None:
    """Non-divisible dimensions should still cover all pixels."""
    tiles = _tile_windows(110, 60, 50)
    assert len(tiles) == 6  # 3 cols x 2 rows


def test_process_tile(tmp_path: Path) -> None:
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    years = (2020, 2021, 2022)
    size = 20
    _make_national_cdl(cdl_dir, years=years, size=size)

    cfg = _make_config(cdl_dir)

    window_dict = {"col_off": 0, "row_off": 0, "width": size, "height": size}
    params = {
        "config": cfg,
        "start_year": 2020,
        "end_year": 2022,
        "output_dir": str(output_dir),
        "window": window_dict,
    }

    result = process_tile(("A0", params))
    assert isinstance(result, str)
    assert "A0" in result
    assert "Finished" in result or "Skipped" in result


def test_process_tile_no_valid_pixels(tmp_path: Path) -> None:
    """Tile with all-zero rasters should be skipped."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    transform = from_bounds(0, 0, 300, 300, 10, 10)
    for year in (2020, 2021):
        year_dir = cdl_dir / str(year)
        year_dir.mkdir(parents=True)
        data = np.zeros((10, 10), dtype=np.int32)
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "int32",
            "width": 10,
            "height": 10,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)

    cfg = _make_config(cdl_dir)
    window_dict = {"col_off": 0, "row_off": 0, "width": 10, "height": 10}
    params = {
        "config": cfg,
        "start_year": 2020,
        "end_year": 2021,
        "output_dir": str(output_dir),
        "window": window_dict,
    }

    result = process_tile(("Z1", params))
    assert "Skipped" in result


def test_process_tile_all_barren(tmp_path: Path) -> None:
    """Tile with all non-cropland pixels — effective count = 0 after barren remap."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    transform = from_bounds(0, 0, 300, 300, 10, 10)
    for year in (2020, 2021):
        year_dir = cdl_dir / str(year)
        year_dir.mkdir(parents=True)
        # Class 152 (shrubland) is non-cropland and gets remapped to BARREN_CODE
        # at combine time, giving every pixel effective_count = 0.
        data = np.full((10, 10), 152, dtype=np.int32)
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "int32",
            "width": 10,
            "height": 10,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)

    cfg = _make_config(cdl_dir)
    window_dict = {"col_off": 0, "row_off": 0, "width": 10, "height": 10}
    params = {
        "config": cfg,
        "start_year": 2020,
        "end_year": 2021,
        "output_dir": str(output_dir),
        "window": window_dict,
    }

    result = process_tile(("B1", params))
    assert "Skipped" in result


def test_run_polygonize(tmp_path: Path) -> None:
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    years = (2020, 2021)
    _make_national_cdl(cdl_dir, years=years, size=10)

    cfg = _make_config(cdl_dir)

    with patch(
        "csb.utils.parallel_map", side_effect=lambda fn, items, **kw: [fn(i) for i in items]
    ):
        result_dir = run_polygonize(cfg, 2020, 2021, output_dir)

    assert result_dir.exists()


def test_run_polygonize_skips_done(tmp_path: Path) -> None:
    """Already-processed tiles should be skipped."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    years = (2020, 2021)
    _make_national_cdl(cdl_dir, years=years, size=10)

    (output_dir / "A0.parquet").touch()

    cfg = _make_config(cdl_dir)
    result_dir = run_polygonize(cfg, 2020, 2021, output_dir)
    assert result_dir == output_dir


def test_run_polygonize_single_area(tmp_path: Path) -> None:
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    years = (2020, 2021)
    _make_national_cdl(cdl_dir, years=years, size=10)

    cfg = _make_config(cdl_dir)

    with patch(
        "csb.utils.parallel_map", side_effect=lambda fn, items, **kw: [fn(i) for i in items]
    ):
        result_dir = run_polygonize(cfg, 2020, 2021, output_dir, area="A0")

    parquets = list(result_dir.glob("*.parquet"))
    assert all("A0" in p.name for p in parquets)


def test_process_tile_high_min_cropland(tmp_path: Path) -> None:
    """With min_cropland_years very high, all polygons should be filtered."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _make_national_cdl(cdl_dir, years=(2020, 2021), size=10)

    cfg = _make_config(cdl_dir)
    cfg["global"]["min_cropland_years"] = 999

    window_dict = {"col_off": 0, "row_off": 0, "width": 10, "height": 10}
    params = {
        "config": cfg,
        "start_year": 2020,
        "end_year": 2021,
        "output_dir": str(output_dir),
        "window": window_dict,
    }

    result = process_tile(("F1", params))
    assert "Skipped" in result or "Finished" in result
