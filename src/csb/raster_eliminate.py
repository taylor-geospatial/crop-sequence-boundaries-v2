"""Raster-side polygon elimination, equivalent to arcpy `Eliminate(LENGTH)`.

Operates on the label raster: count adjacent-pixel-edge crossings between
labels for shared-boundary length, merge labels with area below threshold
into the longest-shared-boundary neighbor via union-find, remap the raster.
"""

import numpy as np
from skimage.measure import label as cc_label

from csb.config import CDL_PIXEL_AREA_SQM


def label_raster(combo_raster: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, int]:
    """Connected-components label the masked combo raster.

    Each connected region of equal combo_id (4-connectivity) gets a unique
    label in [1..n]. Background (mask==False) gets 0.

    Args:
        combo_raster: HxW int32 of compact combo IDs.
        mask: HxW bool — True where pixels are valid.

    Returns:
        (lbl, n_labels) where lbl is HxW int32, lbl[~mask] == 0.
    """
    masked = np.where(mask, combo_raster.astype(np.int32) + 1, 0).astype(np.int32)
    lbl = cc_label(masked, connectivity=1, background=0).astype(np.int32)
    return lbl, int(lbl.max())


def label_areas(
    lbl: np.ndarray, n_labels: int, pixel_area: float = CDL_PIXEL_AREA_SQM
) -> np.ndarray:
    """Polygon area in sq m for each label id (index 0 = background = 0)."""
    counts = np.bincount(lbl.ravel(), minlength=n_labels + 1).astype(np.int64)
    return (counts * pixel_area).astype(np.float64)


def neighbor_edges(
    lbl: np.ndarray, n_labels: int, pixel_size: float = 30.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute shared-boundary length between every pair of adjacent labels.

    Counts the number of axis-aligned pixel edges between cells with different
    labels (both > 0), per (lo_label, hi_label) pair, multiplied by pixel_size.

    Returns:
        a: int32, smaller label of each pair
        b: int32, larger label of each pair
        length: float64, shared boundary length in meters

    Pairs are unique and a < b.
    """
    h_left = lbl[:, :-1]
    h_right = lbl[:, 1:]
    h_keep = (h_left != h_right) & (h_left > 0) & (h_right > 0)
    ha = np.minimum(h_left[h_keep], h_right[h_keep]).astype(np.int64)
    hb = np.maximum(h_left[h_keep], h_right[h_keep]).astype(np.int64)

    v_top = lbl[:-1, :]
    v_bot = lbl[1:, :]
    v_keep = (v_top != v_bot) & (v_top > 0) & (v_bot > 0)
    va = np.minimum(v_top[v_keep], v_bot[v_keep]).astype(np.int64)
    vb = np.maximum(v_top[v_keep], v_bot[v_keep]).astype(np.int64)

    a = np.concatenate([ha, va])
    b = np.concatenate([hb, vb])

    # Encode (a, b) into a single int64 key for unique+count.
    stride = np.int64(n_labels) + 1
    key = a * stride + b
    uniq, counts = np.unique(key, return_counts=True)
    lo = (uniq // stride).astype(np.int32)
    hi = (uniq % stride).astype(np.int32)
    length = (counts.astype(np.float64) * pixel_size).astype(np.float64)
    return lo, hi, length


# ---------------------------------------------------------------------------
# Union-find for transitive merge resolution
# ---------------------------------------------------------------------------


def _uf_find(parent: np.ndarray, x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _uf_union(parent: np.ndarray, rank: np.ndarray, a: int, b: int) -> None:
    ra = _uf_find(parent, a)
    rb = _uf_find(parent, b)
    if ra == rb:
        return
    # Always merge SMALL into LARGE — caller picks order via (a=small, b=target).
    # We want b's root to win to preserve target identity. Force it.
    parent[ra] = rb
    if rank[ra] == rank[rb]:
        rank[rb] += 1


def eliminate_pass(
    lbl: np.ndarray,
    n_labels: int,
    threshold: float,
    pixel_area: float = CDL_PIXEL_AREA_SQM,
    pixel_size: float = 30.0,
) -> tuple[np.ndarray, int]:
    """Single elimination pass: merge labels with area <= threshold into the
    non-small neighbor with the longest shared boundary.

    Returns (new_lbl, new_n_labels).

    A small label with no non-small neighbor is left untouched (carries to
    next pass). Mirrors arcpy.Eliminate(LENGTH) selection semantics.
    """
    areas = label_areas(lbl, n_labels, pixel_area)
    small = areas <= threshold
    small[0] = False  # background
    if not small.any():
        return lbl, n_labels

    a, b, length = neighbor_edges(lbl, n_labels, pixel_size)
    if a.size == 0:
        return lbl, n_labels

    # Build pairs (small, large) where exactly one side is small.
    a_small = small[a]
    b_small = small[b]
    one_small = a_small ^ b_small
    if not one_small.any():
        return lbl, n_labels

    a_o = a[one_small]
    b_o = b[one_small]
    a_o_small = a_small[one_small]

    # Canonicalize: small_id, target_id.
    small_id = np.where(a_o_small, a_o, b_o)
    target_id = np.where(a_o_small, b_o, a_o)
    edge_len = length[one_small]

    # For each small_id, pick the target with max edge_len.
    # Sort by (small_id, -edge_len) and take first occurrence per small_id.
    order = np.lexsort((-edge_len, small_id))
    small_sorted = small_id[order]
    target_sorted = target_id[order]
    first_mask = np.empty(small_sorted.size, dtype=bool)
    first_mask[0] = True
    first_mask[1:] = small_sorted[1:] != small_sorted[:-1]
    pick_small = small_sorted[first_mask]
    pick_target = target_sorted[first_mask]

    # Resolve transitive merges via union-find. If a target is itself small
    # (shouldn't happen given one_small filter, but be safe) we skip.
    parent = np.arange(n_labels + 1, dtype=np.int32)
    rank = np.zeros(n_labels + 1, dtype=np.int32)

    # Process in ascending small_id (order doesn't matter much here).
    for s, t in zip(pick_small, pick_target, strict=True):
        _uf_union(parent, rank, int(s), int(t))

    # Path-compress: for each label, find its root.
    remap = np.arange(n_labels + 1, dtype=np.int32)
    for i in range(1, n_labels + 1):
        remap[i] = _uf_find(parent, i)

    # Apply remap.
    new_lbl = remap[lbl]
    # Compact label IDs to 1..K.
    uniq = np.unique(new_lbl)
    compact = np.zeros(int(uniq.max()) + 1, dtype=np.int32)
    # background stays 0
    next_id = 1
    for u in uniq:
        if u == 0:
            continue
        compact[u] = next_id
        next_id += 1
    return compact[new_lbl].astype(np.int32), next_id - 1


def eliminate_label_raster(
    lbl: np.ndarray,
    n_labels: int,
    thresholds: list[float],
    pixel_area: float = CDL_PIXEL_AREA_SQM,
    pixel_size: float = 30.0,
) -> tuple[np.ndarray, int]:
    """Run the full multi-threshold elimination on a label raster."""
    cur_lbl = lbl
    cur_n = n_labels
    for t in thresholds:
        cur_lbl, cur_n = eliminate_pass(cur_lbl, cur_n, t, pixel_area, pixel_size)
        if cur_n == 0:
            break
    return cur_lbl, cur_n


def dissolve_same_combo(
    lbl: np.ndarray,
    n_labels: int,
    combo_per_label: np.ndarray,
) -> tuple[np.ndarray, int, np.ndarray]:
    """Dissolve adjacent labels that share the same combo (CDL sequence).

    After elimination, slivers absorbed into one large polygon may leave that
    large polygon touching another large polygon with the SAME N-year CDL
    sequence — they were originally one connected region, separated by the
    sliver, and the sliver is now gone. arcpy's pipeline doesn't have an
    explicit Dissolve step but the geometry-based Eliminate produces a similar
    effect because absorbed slivers physically merge two regions into one
    feature. We replicate that on the raster side by union-finding adjacent
    labels with equal combo_id.

    Returns:
        new_lbl: HxW int32, dissolved labels compacted to 1..K.
        new_n: int, number of distinct labels.
        new_combo_per_label: 1D int32 of length new_n+1, combo_id per new label.
    """
    if n_labels <= 1:
        return lbl, n_labels, combo_per_label

    a, b, _length = neighbor_edges(lbl, n_labels)
    if a.size == 0:
        return lbl, n_labels, combo_per_label

    # Pairs where both sides share the same combo are merge candidates.
    same = combo_per_label[a] == combo_per_label[b]
    if not same.any():
        return lbl, n_labels, combo_per_label

    pa_, pb_ = a[same], b[same]

    parent = np.arange(n_labels + 1, dtype=np.int32)
    rank = np.zeros(n_labels + 1, dtype=np.int32)
    for x, y in zip(pa_, pb_, strict=True):
        _uf_union(parent, rank, int(x), int(y))

    remap = np.arange(n_labels + 1, dtype=np.int32)
    for i in range(1, n_labels + 1):
        remap[i] = _uf_find(parent, i)

    new_lbl_raw = remap[lbl]
    uniq = np.unique(new_lbl_raw)
    compact = np.zeros(int(uniq.max()) + 1, dtype=np.int32)
    next_id = 1
    for u in uniq:
        if u == 0:
            continue
        compact[u] = next_id
        next_id += 1
    new_lbl = compact[new_lbl_raw].astype(np.int32, copy=False)
    new_n = next_id - 1

    # Carry combo_id through. compact[u] gives the new label id for old
    # union-find root u. combo_per_label[u] is the combo of any member of u
    # (all members share it by construction).
    new_combo = np.zeros(new_n + 1, dtype=np.int32)
    for old_root, new_id in enumerate(compact):
        if new_id > 0:
            new_combo[new_id] = combo_per_label[old_root]
    return new_lbl, new_n, new_combo
