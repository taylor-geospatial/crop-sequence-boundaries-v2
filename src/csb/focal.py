"""Experimental focal-mode filters used by the paper diagnostics.

These filters approximate the Google Earth Engine preprocessing described by
Hunt et al. (2024). The parameter sweep in ``paper/figures`` found that they
reduced agreement with USDA field boundaries, so the production CLI does not
expose them and the polygonize defaults leave them disabled.
"""

import numpy as np


def focal_mode(arr: np.ndarray, radius: int = 1) -> np.ndarray:
    """Return the most common nonzero value in each square neighborhood."""
    from scipy.signal import fftconvolve

    if radius < 1:
        return arr

    size = 2 * radius + 1
    height, width = arr.shape
    padded = np.pad(arr, radius, mode="edge")
    kernel = np.ones((size, size), dtype=np.float32)
    counts = np.zeros((height, width), dtype=np.float32)
    best = np.zeros((height, width), dtype=np.uint8)

    for value in np.unique(padded):
        if value == 0:
            continue
        value_counts = fftconvolve((padded == value).astype(np.float32), kernel, mode="valid")
        better = value_counts > counts
        counts[better] = value_counts[better]
        best[better] = value

    return best


def _components_by_value(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Label four-connected components without joining unlike values."""
    from scipy.ndimage import label

    height, width = arr.shape
    labels = np.zeros((height, width), dtype=np.int32)
    structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8)
    next_id = 1
    sizes = [0]

    for value in np.unique(arr):
        if value == 0:
            continue
        mask = arr == value
        value_labels, count = label(mask, structure=structure)
        if count == 0:
            continue
        labels[mask] = value_labels[mask] + next_id - 1
        sizes.extend(np.bincount(value_labels.ravel(), minlength=count + 1)[1:].tolist())
        next_id += count

    return labels, np.asarray(sizes, dtype=np.int32)


def apply_focal_mode(
    arr: np.ndarray,
    *,
    radius: int = 2,
    min_patch_size: int = 5,
    iterations: int = 4,
    final_pass_radius: int = 0,
) -> np.ndarray:
    """Apply the experimental two-stage focal-mode approximation.

    The first stage replaces only components smaller than ``min_patch_size``.
    The optional second stage applies an unconditional focal mode.
    """
    if radius < 1 and final_pass_radius < 1:
        return arr

    if radius >= 1 and iterations > 0 and min_patch_size > 1:
        for _ in range(iterations):
            labels, sizes = _components_by_value(arr)
            small = sizes[labels] < min_patch_size
            small[labels == 0] = False
            if not small.any():
                break
            arr = np.where(small, focal_mode(arr, radius=radius), arr)

    if final_pass_radius >= 1:
        arr = focal_mode(arr, radius=final_pass_radius)

    return arr
