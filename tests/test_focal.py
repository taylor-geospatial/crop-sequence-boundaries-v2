"""Tests for the experimental focal-mode preprocessing."""

import numpy as np

from csb.focal import apply_focal_mode, focal_mode


def test_focal_mode_replaces_isolated_value() -> None:
    arr = np.ones((5, 5), dtype=np.uint8)
    arr[2, 2] = 2

    result = focal_mode(arr)

    assert result[2, 2] == 1


def test_apply_focal_mode_preserves_large_component() -> None:
    arr = np.ones((5, 5), dtype=np.uint8)
    arr[1:4, 1:4] = 2

    result = apply_focal_mode(arr, radius=1, min_patch_size=5, iterations=2)

    np.testing.assert_array_equal(result, arr)
