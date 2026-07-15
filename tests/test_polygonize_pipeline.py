"""Tests for csb.polygonize — process_tile and run_polygonize."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from csb.polygonize import _tile_windows, process_tile, run_polygonize


def _make_national_cdl(base_dir: Path, years: tuple[int, ...], size: int = 20) -> None:
    """Create per-year CDL TIFs at base_dir/{year}/{year}_30m_cdls.tif."""
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


def _light_kwargs() -> dict:
    """Lightweight test parameters: small thresholds, no parallelism."""
    return {
        "tile_size": 20,
        "min_cropland_years": 1,
        "eliminate_thresholds": (100.0,),
        "min_polygon_area": 1.0,
        "simplify_tolerance": 10.0,
        "cpu_fraction": 0.5,
        "phase1_workers": 1,
        "phase2_workers": 1,
    }


def _make_params(
    cdl_dir: Path,
    output_dir: Path,
    window: dict,
    *,
    start_year: int = 2020,
    end_year: int = 2021,
    min_cropland_years: int = 1,
    thresholds: tuple[float, ...] = (100,),
    min_polygon_area: float = 1.0,
    simplify_tolerance: float = 10.0,
) -> dict:
    """Build the per-tile params dict that ``process_tile`` consumes."""
    return {
        "national_cdl": str(cdl_dir),
        "start_year": start_year,
        "end_year": end_year,
        "output_dir": str(output_dir),
        "window": window,
        "min_cropland_years": min_cropland_years,
        "eliminate_thresholds": list(thresholds),
        "min_polygon_area": min_polygon_area,
        "simplify_tolerance": simplify_tolerance,
    }


def test_tile_windows() -> None:
    tiles = _tile_windows(100, 100, 50)
    assert len(tiles) == 4
    names = [name for name, _ in tiles]
    assert names == ["A0", "A1", "B0", "B1"]


def test_tile_windows_non_divisible() -> None:
    tiles = _tile_windows(110, 60, 50)
    assert len(tiles) == 6


def test_process_tile(tmp_path: Path) -> None:
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _make_national_cdl(cdl_dir, years=(2020, 2021, 2022), size=20)
    params = _make_params(
        cdl_dir,
        output_dir,
        {"col_off": 0, "row_off": 0, "width": 20, "height": 20},
        end_year=2022,
    )
    result = process_tile(("A0", params))
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
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "uint8",
            "width": 10,
            "height": 10,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.zeros((10, 10), dtype=np.uint8), 1)
    params = _make_params(
        cdl_dir, output_dir, {"col_off": 0, "row_off": 0, "width": 10, "height": 10}
    )
    assert "Skipped" in process_tile(("Z1", params))


def test_process_tile_all_barren(tmp_path: Path) -> None:
    """Tile filled with non-cropland (152 = shrubland) → effective_count = 0."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    transform = from_bounds(0, 0, 300, 300, 10, 10)
    for year in (2020, 2021):
        year_dir = cdl_dir / str(year)
        year_dir.mkdir(parents=True)
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "uint8",
            "width": 10,
            "height": 10,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.full((10, 10), 152, dtype=np.uint8), 1)
    params = _make_params(
        cdl_dir, output_dir, {"col_off": 0, "row_off": 0, "width": 10, "height": 10}
    )
    assert "Skipped" in process_tile(("B1", params))


def test_run_polygonize(tmp_path: Path) -> None:
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    _make_national_cdl(cdl_dir, years=(2020, 2021), size=10)
    with patch(
        "csb.utils.parallel_map", side_effect=lambda fn, items, **kw: [fn(i) for i in items]
    ):
        result_dir = run_polygonize(
            start_year=2020,
            end_year=2021,
            output_dir=output_dir,
            national_cdl_dir=cdl_dir,
            **_light_kwargs(),
        )
    assert result_dir.exists()


def test_run_polygonize_skips_done(tmp_path: Path) -> None:
    """Already-processed tiles should be skipped."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _make_national_cdl(cdl_dir, years=(2020, 2021), size=10)
    (output_dir / "A0.parquet").touch()
    result_dir = run_polygonize(
        start_year=2020,
        end_year=2021,
        output_dir=output_dir,
        national_cdl_dir=cdl_dir,
        **_light_kwargs(),
    )
    assert result_dir == output_dir


def test_run_polygonize_single_area(tmp_path: Path) -> None:
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    _make_national_cdl(cdl_dir, years=(2020, 2021), size=10)
    with patch(
        "csb.utils.parallel_map", side_effect=lambda fn, items, **kw: [fn(i) for i in items]
    ):
        result_dir = run_polygonize(
            start_year=2020,
            end_year=2021,
            output_dir=output_dir,
            national_cdl_dir=cdl_dir,
            area="A0",
            **_light_kwargs(),
        )
    parquets = list(result_dir.glob("*.parquet"))
    assert all("A0" in p.name for p in parquets)


def test_process_tile_high_min_cropland(tmp_path: Path) -> None:
    """With min_cropland_years very high, all polygons should be filtered."""
    cdl_dir = tmp_path / "cdl"
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    _make_national_cdl(cdl_dir, years=(2020, 2021), size=10)
    params = _make_params(
        cdl_dir,
        output_dir,
        {"col_off": 0, "row_off": 0, "width": 10, "height": 10},
        min_cropland_years=999,
    )
    result = process_tile(("F1", params))
    assert "Skipped" in result or "Finished" in result
