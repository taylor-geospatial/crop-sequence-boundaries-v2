"""Constants and defaults for the CSB pipeline.

CDL semantics, output schema constants, and the default values that the CLI
exposes as flags. There is no YAML config file — every parameter is a
keyword argument on the public Python API and a corresponding ``--option``
on the CLI.
"""

# ---------------------------------------------------------------------------
# CDL semantics
# ---------------------------------------------------------------------------

# CDL crop classes are 1..CDL_CROP_MAX inclusive; >= 82 are non-cropland
# (water, developed, forest, grassland, wetlands, etc.).
CDL_CROP_MAX = 81

# Sentinel for non-cropland pixels in the packed sequence. Must not collide
# with any cropland class in [1, CDL_CROP_MAX].
BARREN_CODE = 254

# 30m CDL pixel area in m² (exact in EPSG:5070 Albers Equal Area).
CDL_PIXEL_AREA_SQM = 900

DEFAULT_CRS = "EPSG:5070"
ACRES_PER_SQM = 1.0 / 4046.86


# ---------------------------------------------------------------------------
# Pipeline defaults (mirror the CLI flag defaults so the Python API and the
# CLI agree).
# ---------------------------------------------------------------------------

# Filesystem layout assumed by the bundled defaults. Override per command.
DEFAULT_NATIONAL_CDL_DIR = "data/input/national_cdl"
DEFAULT_BOUNDARIES_PATH = "data/input/boundaries/US48_ASD_CNTY_Albers.parquet"
DEFAULT_OUTPUT_DIR = "data/output"

# Polygonize tuning. Defaults match USDA's CSBElimination 4-pass schedule.
# Simplification tolerance is 30 m (one CDL pixel) — the ablation in the
# paper shows higher IoU than USDA's BEND_SIMPLIFY 60 m at +12% polygons.
DEFAULT_TILE_SIZE = 5000
DEFAULT_MIN_CROPLAND_YEARS = 2
DEFAULT_ELIMINATE_THRESHOLDS: tuple[float, ...] = (100, 1000, 10000, 10000)
DEFAULT_MIN_POLYGON_AREA = 10000
DEFAULT_SIMPLIFY_TOLERANCE = 30

# Parallelism.
DEFAULT_CPU_FRACTION = 0.95


# ---------------------------------------------------------------------------
# CONUS state abbreviation -> FIPS code (excludes AK, HI, territories).
# ---------------------------------------------------------------------------

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
