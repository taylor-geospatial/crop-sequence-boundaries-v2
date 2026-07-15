"""Shared test fixtures."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.transform import from_bounds


@pytest.fixture
def sample_raster() -> np.ndarray:
    """A small 10x10 integer raster with four distinct zones."""
    raster = np.zeros((10, 10), dtype=np.int32)
    raster[0:5, 0:5] = 1
    raster[0:5, 5:10] = 2
    raster[5:10, 0:5] = 3
    raster[5:10, 5:10] = 4
    return raster


@pytest.fixture
def sample_transform() -> Affine:
    """Affine transform for a 10x10 raster at 30m resolution."""
    return from_bounds(0, 0, 300, 300, 10, 10)


@pytest.fixture
def sample_raster_path(tmp_path: Path, sample_raster: np.ndarray, sample_transform: Affine) -> Path:
    """Write sample_raster to a GeoTIFF and return the path."""
    path = tmp_path / "sample.tif"
    profile = {
        "driver": "GTiff",
        "dtype": sample_raster.dtype,
        "width": 10,
        "height": 10,
        "count": 1,
        "crs": "EPSG:5070",
        "transform": sample_transform,
        "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(sample_raster, 1)
    return path


@pytest.fixture
def multi_year_rasters(tmp_path: Path) -> tuple[Path, list[int]]:
    """Create three years of CDL-like rasters at ``base/{year}/{year}_30m_cdls.tif``."""
    years = [2020, 2021, 2022]
    rng = np.random.default_rng(42)
    for year in years:
        year_dir = tmp_path / str(year)
        year_dir.mkdir()
        data = rng.choice([0, 1, 5, 45, 61, 176], size=(20, 20)).astype(np.int32)
        path = year_dir / f"{year}_30m_cdls.tif"
        transform = from_bounds(0, 0, 600, 600, 20, 20)
        profile = {
            "driver": "GTiff",
            "dtype": "int32",
            "width": 20,
            "height": 20,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)
    return tmp_path, years
