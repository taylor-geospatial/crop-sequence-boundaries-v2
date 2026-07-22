"""Polygon-instance comparison metrics vs USDA CSB (PQ/SQ/RQ, F1@t, chamfer).

Ports the field-instance metric suite used in ftw-planet's
``polygon_metrics_eval.py`` to the CSB setting: USDA CSB1825 polygons act as
ground truth and our generated polygons as predictions, evaluated per region
tile. Beyond coverage IoU (blind to partitioning) and the directional
best-match diagnostic (:mod:`csb.object_eval`), these are *symmetric* instance
metrics:

* **PQ / SQ / RQ** — panoptic quality at IoU>=0.5. RQ is object F1; SQ is the
  mean IoU of matched pairs; PQ = SQ * RQ.
* **F1@[0.5:0.05:0.95]** — mean object F1 over IoU thresholds.
* **Count delta** — n_ours - n_usda (signed) and |delta|/n_usda.
* **Boundary error (m)** — symmetric chamfer between matched-pair boundaries,
  computed in vector space by sampling points along each boundary (no raster).

Scale notes vs the ftw-planet implementation: tiles here hold ~1e5 polygons per
side, so pairwise IoU uses an STRtree sparse query rather than a dense double
loop. For IoU thresholds >= 0.5 a (gt, pred) match is mathematically unique, so
greedy match order cannot change tps/fps/fns and results remain comparable.
"""

import logging

import numpy as np
import shapely
from shapely.strtree import STRtree

logger = logging.getLogger(__name__)

# Same threshold ladder as ftw-planet's F1@[0.5:0.05:0.95].
IOU_THRESHOLDS: list[float] = np.round(np.arange(0.5, 0.96, 0.05), 2).tolist()

# Boundary sampling pitch (m) for the vector chamfer; half a CDL pixel.
CHAMFER_SAMPLE_M = 15.0
# Cap matched pairs used for chamfer so dense tiles stay tractable.
CHAMFER_MAX_PAIRS = 20_000


def pairwise_iou(gt: np.ndarray, pred: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sparse IoU over intersecting (gt, pred) pairs via STRtree.

    Returns (gt_idx, pred_idx, iou) arrays for pairs with iou > 0.
    """
    tree = STRtree(pred)
    gi, pj = tree.query(gt, predicate="intersects")
    if gi.size == 0:
        return gi, pj, np.zeros(0)
    inter = shapely.area(shapely.intersection(gt[gi], pred[pj]))
    union = shapely.area(gt[gi]) + shapely.area(pred[pj]) - inter
    keep = (inter > 0) & (union > 0)
    return gi[keep], pj[keep], (inter[keep] / union[keep])


def match_at_thresholds(
    n_gt: int,
    n_pred: int,
    gi: np.ndarray,
    pj: np.ndarray,
    iou: np.ndarray,
    thresholds: list[float] = IOU_THRESHOLDS,
) -> dict:
    """tps/fps/fns per threshold + matched pairs at the lowest threshold.

    For t >= 0.5 the IoU>t match is unique per gt and per pred, so no greedy
    ordering is needed; we simply take pairs above each threshold.
    """
    per_t: dict[float, tuple[int, int, int]] = {}
    pairs_low: list[tuple[int, int, float]] = []
    for t in thresholds:
        sel = iou > t
        tp = int(sel.sum())
        per_t[t] = (tp, n_pred - tp, n_gt - tp)
        if t == thresholds[0]:
            pairs_low = list(zip(gi[sel].tolist(), pj[sel].tolist(), iou[sel].tolist(), strict=True))
    return {"per_t": per_t, "matched_pairs_low": pairs_low}


def _sample_boundary(geom: shapely.Geometry, pitch: float) -> np.ndarray | None:
    """Points sampled every ``pitch`` meters along a polygon boundary."""
    boundary = shapely.boundary(geom)
    length = shapely.length(boundary)
    if not np.isfinite(length) or length <= 0:
        return None
    n = max(4, int(length / pitch))
    dists = np.linspace(0.0, length, n, endpoint=False)
    return shapely.line_interpolate_point(boundary, dists)


def symmetric_chamfer_m(
    g: shapely.Geometry, p: shapely.Geometry, pitch: float = CHAMFER_SAMPLE_M
) -> float | None:
    """Mean symmetric boundary distance (m) between two polygons."""
    gp = _sample_boundary(g, pitch)
    pp = _sample_boundary(p, pitch)
    if gp is None or pp is None:
        return None
    p_boundary = shapely.boundary(p)
    g_boundary = shapely.boundary(g)
    d1 = float(np.mean(shapely.distance(gp, p_boundary)))
    d2 = float(np.mean(shapely.distance(pp, g_boundary)))
    return 0.5 * (d1 + d2)


def instance_metrics(
    gt: np.ndarray,
    pred: np.ndarray,
    thresholds: list[float] = IOU_THRESHOLDS,
    chamfer_max_pairs: int = CHAMFER_MAX_PAIRS,
    rng_seed: int = 0,
) -> dict:
    """Full instance-metric record for one tile (gt = USDA, pred = ours)."""
    from csb.metrics_counts import object_metrics_from_counts

    n_gt, n_pred = len(gt), len(pred)
    if n_gt == 0 or n_pred == 0:
        return {"n_usda": n_gt, "n_ours": n_pred, "error": "empty side"}

    gi, pj, iou = pairwise_iou(gt, pred)
    m = match_at_thresholds(n_gt, n_pred, gi, pj, iou, thresholds)

    t05 = thresholds[0]
    tp, fp, fn = m["per_t"][t05]
    precision, recall, rq = object_metrics_from_counts(tp, fp, fn)
    matched = m["matched_pairs_low"]
    sq = float(np.mean([x[2] for x in matched])) if matched else 0.0
    f1_per_t = {
        t: object_metrics_from_counts(*m["per_t"][t])[2] for t in thresholds
    }

    # Boundary error on IoU>=0.5 matched pairs (sampled if very many).
    pairs = matched
    if len(pairs) > chamfer_max_pairs:
        rng = np.random.default_rng(rng_seed)
        idx = rng.choice(len(pairs), size=chamfer_max_pairs, replace=False)
        pairs = [pairs[i] for i in idx]
    chamfers = []
    for i, j, _ in pairs:
        c = symmetric_chamfer_m(gt[i], pred[j])
        if c is not None:
            chamfers.append(c)
    ch = np.asarray(chamfers, dtype=np.float64)

    return {
        "n_usda": n_gt,
        "n_ours": n_pred,
        "count_delta": n_pred - n_gt,
        "count_delta_frac": (n_pred - n_gt) / n_gt,
        "tp_05": tp,
        "fp_05": fp,
        "fn_05": fn,
        "object_precision_05": precision,
        "object_recall_05": recall,
        "rq": rq,
        "sq": sq,
        "pq": sq * rq,
        "f1_mean_50_95": float(np.mean(list(f1_per_t.values()))),
        "f1_per_threshold": {str(t): v for t, v in f1_per_t.items()},
        "boundary_error_m_mean": float(ch.mean()) if ch.size else None,
        "boundary_error_m_p95": float(np.percentile(ch, 95)) if ch.size else None,
        "boundary_pairs_scored": int(ch.size),
    }


def load_tile_geoms(
    parquet: str,
    bbox: tuple[float, float, float, float],
    threads: int = 16,
    has_bbox_cols: bool = True,
) -> np.ndarray:
    """Load polygons intersecting ``bbox`` from a (prepped) GeoParquet."""
    import duckdb

    bx0, by0, bx1, by1 = bbox
    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute(f"PRAGMA threads={threads}")
    pred = (
        f"xmax >= {bx0} AND xmin <= {bx1} AND ymax >= {by0} AND ymin <= {by1} AND "
        if has_bbox_cols
        else ""
    )
    rows = conn.execute(f"""
        SELECT ST_AsWKB(ST_MakeValid(geometry)) FROM read_parquet('{parquet}')
        WHERE {pred} ST_Intersects(geometry, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))
    """).fetchall()
    conn.close()
    return shapely.from_wkb([bytes(r[0]) for r in rows])
