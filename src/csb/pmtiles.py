"""Build a CONUS PMTiles archive from a CSB GeoParquet output.

Two stages, both shell out to disk-resident files:

1. :func:`parquet_to_fgb` — load the GeoParquet via geopandas, drop
   NULL/empty/invalid geometries (mandatory for ``pyogrio.write_dataframe``
   with the FlatGeobuf spatial index), reproject to EPSG:4326, write FGB.
2. :func:`fgb_to_pmtiles` — invoke ``tippecanoe`` with CSB-tuned flags
   (z=4..12, simplification=1, 2 MB tile cap, drop-densest-as-needed).
   Carries CSBID + CDL{year} attributes only by default.

The full :func:`build_pmtiles` pipeline can use a working directory like
``$TMPDIR`` (NVMe scratch) for the FGB intermediate so the multi-GB
serialization doesn't go over NFS.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger(__name__)

# Columns to drop from the GeoParquet before writing FGB / pmtiles.
# These are duplicates (area_sqm vs Shape_area), internal IDs (national_oid),
# or derivable post-hoc.
_DROP_COLUMNS = ("national_oid", "Shape_area", "Shape_Length", "area_sqm")

# Default attributes to carry into the pmtiles. Keep the carry list small —
# every ``-y`` adds bytes to every tile.
DEFAULT_ATTRIBUTES: tuple[str, ...] = (
    "CSBID",
    "CDL2018",
    "CDL2019",
    "CDL2020",
    "CDL2021",
    "CDL2022",
    "CDL2023",
    "CDL2024",
    "CDL2025",
)


def parquet_to_fgb(
    parquet: Path,
    fgb: Path,
    *,
    drop_columns: Sequence[str] = _DROP_COLUMNS,
) -> None:
    """Convert CSB GeoParquet to FlatGeobuf, reprojecting to EPSG:4326."""
    import geopandas as gpd
    import pyogrio
    import shapely

    t0 = time.perf_counter()
    gdf = gpd.read_parquet(parquet)
    logger.info("loaded %s features in %.1fs", f"{len(gdf):,}", time.perf_counter() - t0)

    keep = gdf.geometry.notna() & ~gdf.geometry.is_empty & shapely.is_valid(gdf.geometry.values)
    n_drop = int((~keep).sum())
    if n_drop:
        logger.info("dropped %s invalid/empty/null geoms", f"{n_drop:,}")
        gdf = gdf.loc[keep].reset_index(drop=True)

    if str(gdf.crs).split(":")[-1] != "4326":
        logger.info("reprojecting %s -> EPSG:4326", gdf.crs)
        gdf = gdf.to_crs("EPSG:4326")

    drop = [c for c in drop_columns if c in gdf.columns]
    if drop:
        gdf = gdf.drop(columns=drop)

    t0 = time.perf_counter()
    pyogrio.write_dataframe(gdf, fgb, driver="FlatGeobuf")
    logger.info(
        "wrote %s in %.1fs (%.2f GB)",
        fgb,
        time.perf_counter() - t0,
        fgb.stat().st_size / 1e9,
    )


def fgb_to_pmtiles(
    fgb: Path,
    pmtiles: Path,
    *,
    layer: str = "csb",
    minimum_zoom: int = 4,
    maximum_zoom: int = 12,
    full_detail: int | None = None,
    simplification: int = 1,
    buffer: int = 8,
    max_tile_bytes: int = 2_000_000,
    max_tile_features: int = 1_000_000,
    attributes: Sequence[str] = DEFAULT_ATTRIBUTES,
    tippecanoe: str = "tippecanoe",
    extra_args: Sequence[str] = (),
) -> None:
    """Run ``tippecanoe`` to convert a FlatGeobuf into a single PMTiles archive."""
    full_detail = full_detail if full_detail is not None else maximum_zoom
    cmd: list[str] = [
        tippecanoe,
        "-o",
        str(pmtiles),
        "-l",
        layer,
        f"--minimum-zoom={minimum_zoom}",
        f"--maximum-zoom={maximum_zoom}",
        f"--base-zoom={maximum_zoom}",
        f"--full-detail={full_detail}",
        f"--simplification={simplification}",
        f"--buffer={buffer}",
        f"--maximum-tile-bytes={max_tile_bytes}",
        f"--maximum-tile-features={max_tile_features}",
        "--drop-densest-as-needed",
        "--coalesce-smallest-as-needed",
        "--detect-shared-borders",
        "--read-parallel",
        "--force",
    ]
    for attr in attributes:
        cmd.extend(["-y", attr])
    cmd.extend(extra_args)
    cmd.append(str(fgb))
    logger.info("tippecanoe %s -> %s", fgb, pmtiles)
    subprocess.run(cmd, check=True)


def build_pmtiles(
    parquet: Path,
    pmtiles: Path,
    *,
    workdir: Path | None = None,
    keep_fgb: bool = False,
    **tippecanoe_kwargs: Any,
) -> Path:
    """Convert a CSB GeoParquet to a PMTiles archive in one call.

    Stages the FlatGeobuf intermediate in ``workdir`` (defaults to the parent
    of ``pmtiles``) and removes it after the build unless ``keep_fgb=True``.
    """
    workdir = workdir or pmtiles.parent
    workdir.mkdir(parents=True, exist_ok=True)
    fgb = workdir / (pmtiles.stem + ".fgb")
    try:
        parquet_to_fgb(parquet, fgb)
        fgb_to_pmtiles(fgb, pmtiles, **tippecanoe_kwargs)
    finally:
        if not keep_fgb and fgb.exists():
            fgb.unlink()
    if shutil.which(str(pmtiles)):
        logger.info("pmtiles ready: %s", pmtiles)
    return pmtiles
