"""Utilities — vector ops, zonal stats, and parallelism helpers."""

from __future__ import annotations

import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Any

from contourrs import shapes_arrow
from exactextract import exact_extract

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import geopandas as gpd
    import numpy as np
    import pyarrow as pa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Vector geometry operations
# ---------------------------------------------------------------------------


def polygonize(
    data: np.ndarray,
    mask: np.ndarray | None = None,
    transform: object | None = None,
    connectivity: int = 4,
    nodata: int | None = 0,
) -> pa.Table:
    """Convert a raster to polygon geometries as an Arrow table.

    Uses contourrs.shapes_arrow for zero-copy Rust→Arrow polygonization.

    Args:
        data: 2D integer array of raster values.
        mask: Optional boolean mask (True = valid pixels).
        transform: Affine transform for georeferencing.
        connectivity: Pixel connectivity (4 or 8).
        nodata: Value to exclude from polygonization.

    Returns:
        PyArrow Table with 'geometry' (WKB) and 'value' (float64) columns.
    """
    return shapes_arrow(
        data,
        mask=mask,
        connectivity=connectivity,
        transform=transform,
        nodata=nodata,
    )


# ---------------------------------------------------------------------------
# Zonal statistics
# ---------------------------------------------------------------------------


def zonal_majority(
    zones: gpd.GeoDataFrame,
    zone_id_field: str,
    value_raster_path: str | Path,
) -> dict[int, int]:
    """Compute the majority value from a raster within each zone polygon.

    Args:
        zones: GeoDataFrame with polygon geometries and a zone ID column.
        zone_id_field: Column name in zones containing integer zone IDs.
        value_raster_path: Path to the raster whose values are summarized.

    Returns:
        Dict mapping zone_id -> majority value.
    """
    results: Any = exact_extract(
        str(value_raster_path),
        zones,
        ["majority"],
        include_cols=[zone_id_field],
        output="pandas",
    )

    # Vectorized: direct column access instead of iterrows
    valid = results["majority"].notna()
    ids = results.loc[valid, zone_id_field].astype(int)
    vals = results.loc[valid, "majority"].astype(int)
    return dict(zip(ids, vals, strict=True))


# ---------------------------------------------------------------------------
# Parallelism helpers
# ---------------------------------------------------------------------------


def worker_count(cpu_fraction: float = 0.90) -> int:
    """Return number of worker processes based on CPU fraction."""
    total = multiprocessing.cpu_count()
    return max(1, round(cpu_fraction * total))


def parallel_map(
    fn: Callable[..., Any],
    items: list[Any],
    max_workers: int | None = None,
    desc: str = "Processing",
    show_progress: bool = True,
) -> list[Any]:
    """Map fn over items using ProcessPoolExecutor with Rich progress bar.

    Results are returned in submission order.
    """
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    max_workers = max_workers or worker_count()
    logger.info("Running %s tasks across %s workers", len(items), max_workers)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        disable=not show_progress,
    )

    from concurrent.futures import as_completed

    results: list[Any] = [None] * len(items)
    with progress:
        task_id = progress.add_task(desc, total=len(items))
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
                progress.advance(task_id)

    return results


def parallel_starmap(
    fn: Callable[..., Any],
    items: list[tuple[Any, ...]],
    max_workers: int | None = None,
    desc: str = "Processing",
    show_progress: bool = True,
) -> list[Any]:
    """Like parallel_map but unpacks tuple args via starmap."""
    from concurrent.futures import as_completed

    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    max_workers = max_workers or worker_count()
    logger.info("Running %s tasks across %s workers", len(items), max_workers)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        disable=not show_progress,
    )

    results: list[Any] = [None] * len(items)
    with progress:
        task_id = progress.add_task(desc, total=len(items))
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fn, *args): i for i, args in enumerate(items)}
            for future in as_completed(futures):
                idx = futures[future]
                results[idx] = future.result()
                progress.advance(task_id)

    return results
