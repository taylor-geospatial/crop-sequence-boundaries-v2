"""Object-level matched-polygon correspondence vs USDA CSB1825.

Coverage IoU (see :mod:`csb.parity`) measures whether the two products cover the
same ground, but is blind to how that ground is partitioned into fields. This
module measures partition agreement: for each USDA polygon ``u`` in a bbox, it
finds the generated polygon ``o*`` with the largest intersection and reports
``IoU(u, o*)``. The match is directional — several USDA polygons may pick the
same generated polygon — so it diagnoses splits, merges, and boundary drift
rather than a one-to-one correspondence. Unmatched USDA polygons are reported
separately. This is the §5.3 analysis, extendable to any region, not just I15.

Both inputs must carry ``xmin/ymin/xmax/ymax`` columns (see
:func:`csb.parity.prep_inputs`) so the bbox predicate prunes row groups.
"""

import logging

import duckdb
import numpy as np

logger = logging.getLogger(__name__)


def matched_polygon_iou(
    conn: duckdb.DuckDBPyConnection,
    ours_parquet: str,
    usda_parquet: str,
    bbox: tuple[float, float, float, float],
) -> dict:
    """Directional best-match IoU of each USDA polygon to the generated coverage.

    Returns per-USDA-polygon IoUs plus summary counts. IoU is 0 for USDA
    polygons with no overlapping generated polygon (reported as ``n_no_overlap``).
    """
    bx0, by0, bx1, by1 = bbox
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    bbox_pred = f"xmax >= {bx0} AND xmin <= {bx1} AND ymax >= {by0} AND ymin <= {by1}"

    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE usda AS
        SELECT ROW_NUMBER() OVER () AS uid,
               ST_MakeValid(geometry) AS g,
               ST_Area(ST_Intersection(ST_MakeValid(geometry), {env})) AS ua
        FROM read_parquet('{usda_parquet}')
        WHERE {bbox_pred} AND ST_Intersects(geometry, {env})
    """)
    conn.execute(f"""
        CREATE OR REPLACE TEMP TABLE ours AS
        SELECT ROW_NUMBER() OVER () AS oid,
               ST_MakeValid(geometry) AS g,
               ST_Area(ST_Intersection(ST_MakeValid(geometry), {env})) AS oa
        FROM read_parquet('{ours_parquet}')
        WHERE {bbox_pred} AND ST_Intersects(geometry, {env})
    """)

    row = conn.execute("SELECT COUNT(*) FROM usda").fetchone()
    n_usda = int(row[0]) if row else 0
    if n_usda == 0:
        return {"n_usda": 0, "ious": [], "n_matched": 0, "n_no_overlap": 0}

    # Best (largest-intersection) generated polygon per USDA polygon, clipped to
    # the bbox so partial-edge polygons don't distort the union denominator.
    rows = conn.execute(f"""
        WITH pairs AS (
            SELECT u.uid, u.ua, o.oa,
                   ST_Area(ST_Intersection(ST_Intersection(u.g, o.g), {env})) AS inter
            FROM usda u JOIN ours o ON ST_Intersects(u.g, o.g)
        ),
        ranked AS (
            SELECT uid, ua, oa, inter,
                   ROW_NUMBER() OVER (PARTITION BY uid ORDER BY inter DESC) AS rn
            FROM pairs WHERE inter > 0
        )
        SELECT ua, oa, inter FROM ranked WHERE rn = 1
    """).fetchall()

    ious = [i / (ua + oa - i) for ua, oa, i in rows if (ua + oa - i) > 0]
    n_matched = len(ious)
    return {
        "n_usda": int(n_usda),
        "n_matched": n_matched,
        "n_no_overlap": int(n_usda) - n_matched,
        "ious": ious,
    }


def summarize_matched(result: dict) -> dict:
    """Distribution summary of a :func:`matched_polygon_iou` result."""
    n_usda = result["n_usda"]
    ious = np.asarray(result["ious"], dtype=np.float64)
    out: dict[str, float | int | None] = {
        "n_usda": n_usda,
        "n_matched": result["n_matched"],
        "n_no_overlap": result["n_no_overlap"],
        "frac_no_overlap": (result["n_no_overlap"] / n_usda) if n_usda else None,
    }
    if ious.size == 0:
        out.update({"median_iou": None, "mean_iou": None})
        return out
    # Fractions computed over ALL USDA polygons (unmatched count as IoU 0).
    out.update(
        {
            "median_iou": float(np.median(ious)),
            "mean_iou": float(np.mean(ious)),
            "frac_ge_0.9": float((ious >= 0.9).sum() / n_usda),
            "frac_0.5_0.9": float(((ious >= 0.5) & (ious < 0.9)).sum() / n_usda),
            "frac_lt_0.5_matched": float(((ious > 0) & (ious < 0.5)).sum() / n_usda),
        }
    )
    return out
