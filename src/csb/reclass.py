"""USDA CDL reclassification (the ``reclass_table`` from CSB-create.py).

USDA remaps raw CDL to a compact "temp general code" before Combine (shared by
the CSB team 2026-07-21; verbatim table in ``data/CDL_tempGeneralCode.csv``).
This does two jobs at once:

1. **Crop / non-crop definition.** ``OUT == 0`` is non-crop and dropped from
   field delineation. This supersedes our coarse ``1..CDL_CROP_MAX`` rule and
   the ``--exclude-low-noncrop`` grouping — e.g. 58/59/60 (clover, sod/grass
   seed, switchgrass), 63/64/65 (forest/shrub/barren), 71 (other tree crops),
   176 (grass/pasture) all → non-crop, while hay (37) and many specialty crops
   stay crop.
2. **Class consolidation.** Commonly-confused classes collapse to a shared
   temp code so a pixel that flickers between them across years does not
   fracture into spurious combos — e.g. the tree-fruit classes 66/67/68/72/74-77
   and berry/orchard 204/210-212/215/217/218/220/223 all map to temp 46. This
   directly attacks our over-segmentation in specialty regions.

Barren (CDL 61 fallow/idle, 131 barren) maps to temp ``TEMP_BARREN`` (45),
which is how USDA's ``COUNT45`` retention term works. The USDA team notes the
table is ideally state- and year-specific; this national version is what they
shared. Original CDL is preserved separately for output attributes — only the
delineation runs on temp codes.
"""

import csv
from functools import lru_cache
from importlib.resources import files

import numpy as np

# Temp code for barren / fallow (CDL 61 and 131). Mirrors USDA COUNT45.
TEMP_BARREN = 45


@lru_cache(maxsize=1)
def reclass_lut() -> np.ndarray:
    """256-entry uint8 LUT mapping raw CDL value -> temp general code.

    Values absent from the table map to 0 (non-crop), matching ArcPy
    ``ReclassByTable`` with ``missing_values='DATA'`` where unlisted values
    fall through to NoData in the CSB context.
    """
    lut = np.zeros(256, dtype=np.uint8)
    path = files("csb.data").joinpath("CDL_tempGeneralCode.csv")
    with path.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            lo, hi, out = int(row["FROM"]), int(row["TO"]), int(row["OUT"])
            lut[lo : hi + 1] = out
    return lut


def apply_reclass(arr: np.ndarray) -> np.ndarray:
    """Map a raw-CDL uint8 array to temp general codes."""
    return reclass_lut()[arr]
