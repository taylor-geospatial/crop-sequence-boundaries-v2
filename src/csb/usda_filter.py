"""Port of USDA's production CDL noise filter.

The USDA CSB team's favored preprocessing (shared 2026-07-21, verbatim in
``docs/usda_smoothing_reference.md``) filters each reclassified CDL year with
``RegionGroup(FOUR, WITHIN)`` → ``Con(Count <= 2 → 99)`` → ``Shrink(2, [99])``:
4-connected same-value components of at most 2 pixels are erased and the
surrounding zones grow inward to fill them. Enable with ``--usda-noise-filter``.

This port replaces each noise pixel with the value of its nearest non-noise
pixel (Euclidean), which is what the morphological shrink resolves to for
zones this small. One divergence: in ArcPy, *touching* noise components of
different values merge into a single 99 zone that could survive a 2-cell
shrink if wider than ~4 px; such clusters are erased here too — rare, and
healing them is the filter's intent.
"""

import numpy as np


def remove_small_components(arr: np.ndarray, max_noise_px: int = 2) -> np.ndarray:
    """Erase 4-connected same-value components of ``<= max_noise_px`` pixels.

    Value 0 is background (excluded, like ``RegionGroup(excluded_value=0)``):
    zero pixels are never treated as noise, but they *can* grow into erased
    noise, matching ArcPy where 0 is a zone in the ``Shrink`` input. USDA's
    production value is ``max_noise_px=2`` (≤ 0.18 ha at 30 m).
    """
    from scipy.ndimage import distance_transform_edt
    from skimage.measure import label as cc_label

    labels = cc_label(arr, connectivity=1, background=0)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    noise = sizes[labels] <= max_noise_px
    noise &= labels != 0
    if not noise.any() or noise.all():
        return arr  # noise.all(): no surviving pixel to grow from
    idx = distance_transform_edt(noise, return_distances=False, return_indices=True)
    return arr[tuple(idx)]
