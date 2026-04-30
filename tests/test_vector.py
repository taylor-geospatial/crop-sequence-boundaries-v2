"""Tests for csb.utils — vector operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
from rasterio.transform import from_bounds

if TYPE_CHECKING:
    from affine import Affine

from csb.utils import polygonize


def test_polygonize_basic(sample_raster: np.ndarray, sample_transform: Affine) -> None:
    table = polygonize(sample_raster, transform=sample_transform, nodata=0)
    assert isinstance(table, pa.Table)
    assert "geometry" in table.column_names
    assert "value" in table.column_names
    # 4 zones should produce 4 polygons
    assert table.num_rows == 4


def test_polygonize_with_mask(sample_raster: np.ndarray, sample_transform: Affine) -> None:
    mask = np.ones_like(sample_raster, dtype=np.bool_)
    mask[5:, :] = False  # mask out bottom half
    table = polygonize(sample_raster, mask=mask, transform=sample_transform, nodata=0)
    # Only top-half zones (1, 2)
    assert table.num_rows == 2


def test_polygonize_empty() -> None:
    data = np.zeros((5, 5), dtype=np.int32)
    transform = from_bounds(0, 0, 150, 150, 5, 5)
    table = polygonize(data, transform=transform, nodata=0)
    assert table.num_rows == 0
