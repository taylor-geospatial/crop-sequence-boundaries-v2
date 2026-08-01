"""Tests for the USDA production noise-filter port."""

import numpy as np

from csb.usda_filter import remove_small_components


def test_single_pixel_speckle_healed() -> None:
    arr = np.full((7, 7), 5, dtype=np.uint8)
    arr[3, 3] = 9
    out = remove_small_components(arr)
    assert (out == 5).all()


def test_two_pixel_speckle_healed() -> None:
    arr = np.full((7, 7), 5, dtype=np.uint8)
    arr[3, 3] = arr[3, 4] = 9
    out = remove_small_components(arr)
    assert (out == 5).all()


def test_three_pixel_component_kept() -> None:
    arr = np.full((7, 7), 5, dtype=np.uint8)
    arr[3, 2:5] = 9
    out = remove_small_components(arr)
    assert (out[3, 2:5] == 9).all()


def test_diagonal_pixels_are_separate_components() -> None:
    # 4-connectivity: three diagonal px = three 1-px components, all noise.
    arr = np.full((7, 7), 5, dtype=np.uint8)
    arr[2, 2] = arr[3, 3] = arr[4, 4] = 9
    out = remove_small_components(arr)
    assert (out == 5).all()


def test_zero_background_never_noise_but_grows_in() -> None:
    arr = np.zeros((7, 7), dtype=np.uint8)
    arr[3, 3] = 9  # lone crop pixel surrounded by nodata
    out = remove_small_components(arr)
    assert (out == 0).all()  # zero grows into erased noise
    # and zero itself is untouched even when tiny
    arr2 = np.full((7, 7), 5, dtype=np.uint8)
    arr2[3, 3] = 0
    out2 = remove_small_components(arr2)
    assert out2[3, 3] == 0


def test_fill_takes_nearest_value() -> None:
    arr = np.full((6, 6), 5, dtype=np.uint8)
    arr[:, 3:] = 7
    arr[2, 4] = 9  # speckle inside the 7-field
    out = remove_small_components(arr)
    assert out[2, 4] == 7


def test_no_noise_returns_input_unchanged() -> None:
    arr = np.full((5, 5), 3, dtype=np.uint8)
    out = remove_small_components(arr)
    assert out is arr


def test_all_noise_returns_input() -> None:
    arr = np.array([[1, 2], [3, 4]], dtype=np.uint8)  # four 1-px components
    out = remove_small_components(arr)
    assert (out == arr).all()
