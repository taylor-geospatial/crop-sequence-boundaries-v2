"""The DuckDB polygon-side elimination baseline must match the raster method.

Both implement ``Eliminate(LENGTH)``: below-threshold regions merge into the
longest-shared-edge neighbor. On the same input they should produce the same
surviving coverage (same total area, same number of survivors), so the
benchmark in :mod:`csb.bench` is comparing two implementations of one
algorithm, not two different algorithms.
"""

import numpy as np
import pytest
import shapely
from affine import Affine

from csb.config import CDL_PIXEL_AREA_SQM
from csb.polygon_eliminate import eliminate_polygons_duckdb
from csb.raster_eliminate import eliminate_label_raster, label_areas, label_raster


def _synthetic_combo(seed: int, size: int = 40) -> np.ndarray:
    """Blocky combo raster with slivers: a few large fields plus 1-2 px specks."""
    rng = np.random.default_rng(seed)
    combo = np.zeros((size, size), dtype=np.int32)
    # Large blocks.
    combo[: size // 2, : size // 2] = 1
    combo[: size // 2, size // 2 :] = 2
    combo[size // 2 :, : size // 2] = 3
    combo[size // 2 :, size // 2 :] = 4
    # Scatter slivers of distinct combos so elimination has work to do.
    for _ in range(15):
        r, c = int(rng.integers(1, size - 1)), int(rng.integers(1, size - 1))
        combo[r, c] = int(rng.integers(5, 12))
    return combo


def test_duckdb_matches_raster_coverage() -> None:
    transform = Affine(30.0, 0.0, 0.0, 0.0, -30.0, 0.0)
    thresholds = [CDL_PIXEL_AREA_SQM * 1.5, CDL_PIXEL_AREA_SQM * 4]

    for seed in range(4):
        combo = _synthetic_combo(seed)
        mask = np.ones_like(combo, dtype=bool)
        lbl, n = label_raster(combo, mask)

        # Raster-side.
        r_lbl, r_n = eliminate_label_raster(lbl.copy(), n, thresholds)
        r_area = label_areas(r_lbl, r_n).sum()

        # Polygon-side (DuckDB).
        table = eliminate_polygons_duckdb(lbl.copy(), n, thresholds, transform)
        geoms = shapely.from_wkb(np.asarray(table["geom"]))
        d_area = float(shapely.area(geoms).sum())

        # Total covered area is conserved by both (elimination only merges).
        assert abs(r_area - d_area) < 1e-6, f"seed {seed}: {r_area} vs {d_area}"
        # Same number of surviving regions above the largest threshold.
        assert r_n == table.num_rows, f"seed {seed}: {r_n} vs {table.num_rows}"


def test_sedona_matches_raster_coverage() -> None:
    pytest.importorskip("sedonadb")
    from csb.sedona_eliminate import eliminate_polygons_sedona

    transform = Affine(30.0, 0.0, 0.0, 0.0, -30.0, 0.0)
    thresholds = [CDL_PIXEL_AREA_SQM * 1.5, CDL_PIXEL_AREA_SQM * 4]

    for seed in range(4):
        combo = _synthetic_combo(seed)
        mask = np.ones_like(combo, dtype=bool)
        lbl, n = label_raster(combo, mask)

        r_lbl, r_n = eliminate_label_raster(lbl.copy(), n, thresholds)
        r_area = label_areas(r_lbl, r_n).sum()

        table = eliminate_polygons_sedona(lbl.copy(), n, thresholds, transform)
        geoms = shapely.from_wkb(np.asarray(table["geom"]))
        s_area = float(shapely.area(geoms).sum())

        assert abs(r_area - s_area) < 1e-6, f"seed {seed}: {r_area} vs {s_area}"
        assert r_n == table.num_rows, f"seed {seed}: {r_n} vs {table.num_rows}"
