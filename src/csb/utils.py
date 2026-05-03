"""Polygonization wrapper + parallelism helpers."""

from __future__ import annotations

import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

from contourrs import shapes_arrow
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    import pyarrow as pa

logger = logging.getLogger(__name__)


def polygonize(
    data: np.ndarray,
    mask: np.ndarray | None = None,
    transform: object | None = None,
    connectivity: int = 4,
    nodata: int | None = 0,
) -> pa.Table:
    """Convert a raster to polygon geometries as an Arrow table.

    Args:
        data: 2D integer array of raster values.
        mask: Optional boolean mask (True = valid pixels).
        transform: Affine transform for georeferencing.
        connectivity: Pixel connectivity (4 or 8).
        nodata: Value to exclude from polygonization.

    Returns:
        PyArrow Table with 'geometry' (WKB) and 'value' columns.
    """
    return shapes_arrow(
        data,
        mask=mask,
        connectivity=connectivity,
        transform=transform,
        nodata=nodata,
    )


def worker_count(cpu_fraction: float = 0.90) -> int:
    """Number of worker processes for the given CPU fraction."""
    total = multiprocessing.cpu_count()
    return max(1, round(cpu_fraction * total))


def _make_progress(show: bool) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        disable=not show,
    )


def _parallel(
    fn: Callable[..., Any],
    items: list[Any],
    *,
    starmap: bool,
    max_workers: int | None,
    desc: str,
    show_progress: bool,
) -> list[Any]:
    max_workers = max_workers or worker_count()
    logger.info("Running %s tasks across %s workers", len(items), max_workers)
    progress = _make_progress(show_progress)
    results: list[Any] = [None] * len(items)
    with progress:
        task_id = progress.add_task(desc, total=len(items))
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            if starmap:
                futures = {pool.submit(fn, *args): i for i, args in enumerate(items)}
            else:
                futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                progress.advance(task_id)
    return results


def parallel_map(
    fn: Callable[..., Any],
    items: list[Any],
    max_workers: int | None = None,
    desc: str = "Processing",
    show_progress: bool = True,
) -> list[Any]:
    """Map ``fn`` over ``items`` in a process pool with a progress bar."""
    return _parallel(
        fn, items, starmap=False, max_workers=max_workers, desc=desc, show_progress=show_progress
    )


def parallel_starmap(
    fn: Callable[..., Any],
    items: list[tuple[Any, ...]],
    max_workers: int | None = None,
    desc: str = "Processing",
    show_progress: bool = True,
) -> list[Any]:
    """Like :func:`parallel_map` but unpacks tuple args via starmap."""
    return _parallel(
        fn, items, starmap=True, max_workers=max_workers, desc=desc, show_progress=show_progress
    )
