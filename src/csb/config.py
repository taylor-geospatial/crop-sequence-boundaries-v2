"""Configuration loading and defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# CDL classes 1-81 are crop/agriculture (annual crops, perennial crops, fallow/idle, misc ag).
# Classes >= 82 are non-cropland (water=111, developed=121-124, forest=141-143,
# grassland=176, wetlands=190, etc.).  These are remapped to BARREN_CODE before
# computing crop-year counts so they don't inflate effective_count.
CDL_CROP_MAX = 81  # inclusive: CDL in [1, CDL_CROP_MAX] treated as cropland

# Sentinel value assigned to non-cropland pixels before packing sequences.
# Must be distinct from all valid CDL crop classes (1-81). Old value of 45
# collided with CDL 45 (sugarcane), causing real sugarcane fields in
# Florida/Louisiana to be silently dropped from CSB output.
BARREN_CODE = 254

# 30m CDL pixel area in sq metres (EPSG:5070 Albers Equal Area, so exact).
CDL_PIXEL_AREA_SQM = 900

# Albers Equal Area Conic (USGS version) — default CRS
DEFAULT_CRS = "EPSG:5070"

# Conversion factor
ACRES_PER_SQM = 1.0 / 4046.86

# CONUS state abbreviation → FIPS code (excludes AK, HI, territories)
STATE_FIPS: dict[str, str] = {
    "AL": "01",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "FL": "12",
    "GA": "13",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file and return as dict."""
    with Path(path).open() as f:
        return yaml.safe_load(f)


def bundled_config_path() -> Path:
    """Return path to the default config bundled with the package."""
    return Path(__file__).parent.parent.parent / "configs" / "default.yaml"
