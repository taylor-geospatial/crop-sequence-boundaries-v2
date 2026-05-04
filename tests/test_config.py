"""Tests for csb.config."""

from __future__ import annotations

from csb.config import (
    ACRES_PER_SQM,
    BARREN_CODE,
    DEFAULT_CRS,
    DEFAULT_ELIMINATE_THRESHOLDS,
    DEFAULT_MIN_CROPLAND_YEARS,
    DEFAULT_MIN_POLYGON_AREA,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_TILE_SIZE,
    STATE_FIPS,
)


def test_state_fips() -> None:
    assert len(STATE_FIPS) == 48  # CONUS
    assert STATE_FIPS["AL"] == "01"
    assert STATE_FIPS["WY"] == "56"


def test_constants() -> None:
    import pytest

    # Sentinel must lie outside the cropland range [1, 81] to avoid colliding
    # with real CDL crop classes (e.g. CDL 45 = sugarcane).
    assert BARREN_CODE > 81
    assert BARREN_CODE <= 254
    assert DEFAULT_CRS == "EPSG:5070"
    assert pytest.approx(1.0 / 4046.86) == ACRES_PER_SQM


def test_pipeline_defaults() -> None:
    """Defaults match the 4-pass USDA elimination schedule + 30m simplify."""
    assert DEFAULT_TILE_SIZE == 5000
    assert DEFAULT_MIN_CROPLAND_YEARS == 2
    assert DEFAULT_ELIMINATE_THRESHOLDS == (100, 1000, 10000, 10000)
    assert DEFAULT_MIN_POLYGON_AREA == 10000
    assert DEFAULT_SIMPLIFY_TOLERANCE == 30
