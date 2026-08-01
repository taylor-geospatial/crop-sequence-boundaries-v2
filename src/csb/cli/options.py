"""Shared Click option decorators for the ``csb`` CLI."""

import click

from csb.cli._group import _DEFAULT_THRESHOLDS_STR, _parse_thresholds
from csb.config import (
    DEFAULT_CPU_FRACTION,
    DEFAULT_MIN_CROPLAND_YEARS,
    DEFAULT_MIN_POLYGON_AREA,
    DEFAULT_NATIONAL_CDL_DIR,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_TILE_SIZE,
)


def _polygonize_options(f):  # noqa: ANN001, ANN202 — Click decorator factory
    """Shared --option flags for polygonize / run-all (so both stay in sync)."""
    flags = [
        click.option(
            "--national-cdl-dir",
            type=click.Path(exists=True, file_okay=False),
            default=DEFAULT_NATIONAL_CDL_DIR,
            show_default=True,
            help="Directory containing per-year CDL TIFs.",
        ),
        click.option(
            "--tile-size",
            type=int,
            default=DEFAULT_TILE_SIZE,
            show_default=True,
            help="Side length (px) of each processing tile.",
        ),
        click.option(
            "--min-cropland-years",
            type=int,
            default=DEFAULT_MIN_CROPLAND_YEARS,
            show_default=True,
            help="Minimum number of cropland years to keep a pixel.",
        ),
        click.option(
            "--eliminate-thresholds",
            default=_DEFAULT_THRESHOLDS_STR,
            show_default=True,
            callback=_parse_thresholds,
            help="Comma-separated area thresholds (m²) for the eliminate passes.",
        ),
        click.option(
            "--min-polygon-area",
            type=float,
            default=DEFAULT_MIN_POLYGON_AREA,
            show_default=True,
            help="Drop polygons smaller than this (m²).",
        ),
        click.option(
            "--simplify-tolerance",
            type=float,
            default=DEFAULT_SIMPLIFY_TOLERANCE,
            show_default=True,
            help="coverage_simplify tolerance in meters.",
        ),
        click.option(
            "--cpu-fraction",
            type=float,
            default=DEFAULT_CPU_FRACTION,
            show_default=True,
            help="Fraction of CPUs to use for the worker pool.",
        ),
        click.option(
            "--phase1-workers",
            type=int,
            default=None,
            help="Phase-1 (raster-side) workers. Defaults to ~1/4 of cpu_fraction *cpu_count.",
        ),
        click.option(
            "--phase2-workers",
            type=int,
            default=None,
            help="Phase-2 (simplify) workers. Defaults to cpu_fraction *cpu_count.",
        ),
        click.option(
            "--roads-mask",
            type=click.Path(exists=True, dir_okay=False),
            default=None,
            help="Optional GeoParquet from `csb roads-prep`. When set, road/rail "
            "buffers are excluded from the cropland mask before connected-components "
            "labeling so adjacent fields don't merge across roads.",
        ),
        click.option(
            "--edges-mask",
            type=click.Path(exists=True, dir_okay=False),
            default=None,
            help="USDA's exact Edges_10M road/rail GeoTIFF (value 1 = edge). "
            "Warped onto each tile grid and excluded from the cropland mask. "
            "Use instead of --roads-mask for the production edge raster.",
        ),
        click.option(
            "--same-combo-dissolve/--no-same-combo-dissolve",
            "same_combo_dissolve",
            default=True,
            help="Toggle the same-combo dissolve pass (default on; off for ablation).",
        ),
        click.option(
            "--usda-retention/--no-usda-retention",
            "usda_retention",
            default=False,
            help="Use USDA's polygon-level retention (effective >= 2 OR area >= "
            "min-polygon-area with effective >= 1) instead of the pixel-level "
            "min-cropland-years mask.",
        ),
        click.option(
            "--exclude-low-noncrop/--no-exclude-low-noncrop",
            "exclude_low_noncrop",
            default=False,
            help="Treat CDL 61-65 (fallow/idle, pasture, forest, shrub, barren) "
            "as non-crop, matching USDA's GEE category grouping.",
        ),
        click.option(
            "--reclass/--no-reclass",
            "use_reclass",
            default=False,
            help="Reclassify raw CDL to USDA's temp general codes before "
            "combine (data/CDL_tempGeneralCode.csv): defines crop/non-crop "
            "(OUT=0) and merges commonly-confused classes. Supersedes "
            "--exclude-low-noncrop.",
        ),
        click.option(
            "--upsample",
            type=int,
            default=1,
            show_default=True,
            help="Upsample CDL by this integer factor before combine (after "
            "reclass+filter, matching USDA's resample-to-10m step). Use 3 for "
            "10m. Areas/thresholds stay in m²; memory and time grow ~factor².",
        ),
        click.option(
            "--usda-noise-filter",
            "usda_noise_px",
            type=int,
            default=0,
            show_default=True,
            help="Erase same-value components of <= N px per CDL year before "
            "combine (USDA's production RegionGroup/Con/Shrink filter; USDA "
            "uses 2). 0 disables.",
        ),
        click.option(
            "--focal-radius",
            type=int,
            default=0,
            show_default=True,
            help="Focal-mode noise filter radius (px) applied per CDL year "
            "before combine; 0 disables. Emulates USDA's GEE noise filtering.",
        ),
    ]
    for opt in reversed(flags):
        f = opt(f)
    return f
