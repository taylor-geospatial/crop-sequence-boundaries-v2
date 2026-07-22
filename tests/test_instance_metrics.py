"""Instance metrics (PQ/SQ/RQ, F1@t, chamfer) must reproduce known values.

Constructed cases: an exact match (IoU 1.0), a half-overlap split (IoU 0.5 —
below the >0.5 gate), and an unmatched prediction. These pin the counting,
uniqueness-at-0.5 matching, and the vector chamfer.
"""

import numpy as np
import shapely

from csb.instance_metrics import instance_metrics, pairwise_iou, symmetric_chamfer_m


def test_exact_and_split_and_fp() -> None:
    # GT: two 20x20 fields. Pred: one exact copy, one half of the second field,
    # and one far-away spurious polygon.
    gt = np.array([shapely.box(0, 0, 20, 20), shapely.box(100, 0, 120, 20)])
    pred = np.array(
        [
            shapely.box(0, 0, 20, 20),        # exact match, IoU 1.0
            shapely.box(100, 0, 110, 20),     # half of gt[1], IoU 0.5 (not > 0.5)
            shapely.box(500, 500, 510, 510),  # false positive
        ]
    )
    res = instance_metrics(gt, pred)

    assert res["tp_05"] == 1
    assert res["fp_05"] == 2
    assert res["fn_05"] == 1
    assert abs(res["object_precision_05"] - 1 / 3) < 1e-9
    assert abs(res["object_recall_05"] - 1 / 2) < 1e-9
    assert abs(res["sq"] - 1.0) < 1e-9          # only the exact pair matched
    assert abs(res["pq"] - res["rq"]) < 1e-9    # PQ = SQ * RQ with SQ = 1
    assert res["count_delta"] == 1
    # exact pair -> zero boundary error
    assert res["boundary_error_m_mean"] is not None
    assert res["boundary_error_m_mean"] < 1e-9


def test_pairwise_iou_sparse() -> None:
    gt = np.array([shapely.box(0, 0, 10, 10)])
    pred = np.array([shapely.box(5, 0, 15, 10), shapely.box(50, 50, 60, 60)])
    gi, pj, iou = pairwise_iou(gt, pred)
    assert list(gi) == [0]
    assert list(pj) == [0]
    assert abs(iou[0] - 5 / 15) < 1e-9  # inter 50, union 150


def test_chamfer_translated_square() -> None:
    # Two unit-offset 20x20 squares: every boundary point of one is within
    # [0, 1] m of the other; symmetric mean must be positive and <= 1.
    g = shapely.box(0, 0, 20, 20)
    p = shapely.box(1, 0, 21, 20)
    c = symmetric_chamfer_m(g, p, pitch=1.0)
    assert c is not None
    assert 0.3 < c <= 1.0
