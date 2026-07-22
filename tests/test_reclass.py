"""Tests for the USDA reclass LUT and its use in combine."""

import numpy as np

from csb.reclass import TEMP_BARREN, apply_reclass, reclass_lut


def test_lut_spot_values() -> None:
    lut = reclass_lut()
    assert lut[1] == 1  # corn -> 1
    assert lut[61] == TEMP_BARREN  # fallow/idle -> barren
    assert lut[131] == TEMP_BARREN  # barren -> barren
    assert lut[176] == 0  # grass/pasture -> non-crop
    assert lut[37] == 26  # hay stays crop
    assert lut[66] == 46 and lut[74] == 46  # tree fruit consolidated
    assert lut[0] == 0  # nodata stays 0


def test_lut_dtype_and_range() -> None:
    lut = reclass_lut()
    assert lut.dtype == np.uint8
    assert lut.max() <= 63


def test_apply_reclass_shape_preserved() -> None:
    arr = np.array([[1, 61], [176, 66]], dtype=np.uint8)
    out = apply_reclass(arr)
    assert out.shape == arr.shape
    assert out.tolist() == [[1, TEMP_BARREN], [0, 46]]


def test_consolidation_merges_confused_classes() -> None:
    # Two orchard classes that flicker year-to-year land in the same temp code,
    # so a pixel alternating between them is one combo, not many.
    arr = np.array([66, 67, 68, 72, 74, 75, 76, 77], dtype=np.uint8)
    assert (apply_reclass(arr) == 46).all()
