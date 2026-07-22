"""Focal-mode noise filter for CDL preprocessing.

Approximates the noise-filtering stage of USDA's (unpublished) GEE
preprocessing: small same-value components are replaced by the most common
nonzero value in their neighborhood, iterated until stable. Parity sweeps show
it recovers under-covered fields in speckle-heavy regions (see
``data/eval/gee_fix3_experiment.json``); enable with ``--focal-radius 1``.

Implementation notes: components are labeled in ONE pass (distinct values never
merge under 4-connectivity because touching pixels of different values start
separate regions), and neighborhood modes are computed only at the small-pixel
locations via sliding windows, not by convolving the full raster per class.
Ties break toward the smallest CDL value, matching the original reference
implementation.
"""

import numpy as np


def _mode_at(arr_padded: np.ndarray, rows: np.ndarray, cols: np.ndarray, radius: int) -> np.ndarray:
    """Most common nonzero value in the (2r+1)² window around each (row, col).

    ``rows``/``cols`` index into the *unpadded* array; ``arr_padded`` must be
    edge-padded by ``radius``. Ties break toward the smaller value. Windows with
    no nonzero values return 0.
    """
    k = 2 * radius + 1
    windows = np.lib.stride_tricks.sliding_window_view(arr_padded, (k, k))
    out = np.empty(rows.size, dtype=arr_padded.dtype)
    # Chunk to bound the (n, k², k²) pairwise-equality tensor.
    chunk = max(1, 8_000_000 // (k * k * k * k))
    for lo in range(0, rows.size, chunk):
        w = windows[rows[lo : lo + chunk], cols[lo : lo + chunk]].reshape(-1, k * k)
        counts = (w[:, :, None] == w[:, None, :]).sum(axis=1).astype(np.float64)
        counts[w == 0] = -np.inf  # zero is never a candidate
        # Strictly-larger count wins; ties toward smaller value (tiny penalty).
        pick = np.argmax(counts - w * 1e-6, axis=1)
        vals = w[np.arange(w.shape[0]), pick]
        vals[~np.isfinite(counts[np.arange(w.shape[0]), pick])] = 0
        out[lo : lo + chunk] = vals
    return out


def _component_sizes(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """4-connected same-value components in one labeling pass.

    Distinct nonzero values cannot join under ``skimage.measure.label`` because
    connectivity requires equal values; background (0) is excluded.
    """
    from skimage.measure import label as cc_label

    labels = cc_label(arr, connectivity=1, background=0).astype(np.int32)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    return labels, sizes


def focal_mode(arr: np.ndarray, radius: int = 1) -> np.ndarray:
    """Most common nonzero value in each square neighborhood (full raster)."""
    if radius < 1:
        return arr
    padded = np.pad(arr, radius, mode="edge")
    rows, cols = np.unravel_index(np.arange(arr.size), arr.shape)
    return _mode_at(padded, rows, cols, radius).reshape(arr.shape)


def apply_focal_mode(
    arr: np.ndarray,
    *,
    radius: int = 2,
    min_patch_size: int = 5,
    iterations: int = 4,
    final_pass_radius: int = 0,
) -> np.ndarray:
    """Two-stage focal-mode filter.

    Stage 1 (``iterations`` rounds): replace pixels of same-value components
    smaller than ``min_patch_size`` with their neighborhood mode. Stage 2
    (optional): one unconditional focal mode at ``final_pass_radius``.
    """
    if radius < 1 and final_pass_radius < 1:
        return arr

    if radius >= 1 and iterations > 0 and min_patch_size > 1:
        for _ in range(iterations):
            labels, sizes = _component_sizes(arr)
            small = sizes[labels] < min_patch_size
            small &= labels != 0
            if not small.any():
                break
            rows, cols = np.nonzero(small)
            padded = np.pad(arr, radius, mode="edge")
            replacements = _mode_at(padded, rows, cols, radius)
            arr = arr.copy()
            arr[rows, cols] = replacements

    if final_pass_radius >= 1:
        arr = focal_mode(arr, radius=final_pass_radius)

    return arr
