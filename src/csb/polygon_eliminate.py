"""Polygon-side ``Eliminate(LENGTH)`` baseline in DuckDB-spatial.

This is the *legacy* formulation the raster-side method in
:mod:`csb.raster_eliminate` replaces. It polygonizes the labeled combine
raster first, then resolves each elimination pass on the vector polygons via a
spatial cross-join: for every below-threshold polygon it measures the shared
boundary length to each adjacent polygon with ``ST_Length(ST_Intersection())``
and merges the small polygon into the longest-shared-edge neighbor with
``ST_Union_Agg``.

It exists so the paper can report a same-machine controlled comparison against
the raster method (see :mod:`csb.bench`). It is intentionally *not* wired into
the production pipeline — it is slow and, at the production ``5000**2`` tile
size, the intermediate WKB blob array overflows Arrow's ``int32`` offsets. The
raster method computes the identical longest-shared-edge merges from
pixel-edge counts before a single polygonization.
"""

import numpy as np
import pyarrow as pa

from csb.config import CDL_PIXEL_AREA_SQM
from csb.raster_eliminate import _uf_find, _uf_union, label_areas
from csb.utils import polygonize


def _connect():  # noqa: ANN202 — duckdb connection type is version-dependent
    """Open an in-memory DuckDB connection with the spatial extension loaded."""
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    return conn


def polygons_from_labels(
    lbl: np.ndarray,
    n_labels: int,
    transform: object,
    pixel_area: float = CDL_PIXEL_AREA_SQM,
) -> pa.Table:
    """Polygonize a label raster into one row per label.

    Returns an Arrow table with ``id`` (int64 label), ``geometry`` (WKB), and
    ``area`` (float64, m**2). Background (label 0) is excluded.
    """
    areas = label_areas(lbl, n_labels, pixel_area)
    table = polygonize(lbl.astype(np.int32), mask=lbl > 0, transform=transform, nodata=0)
    ids = np.asarray(table["value"]).astype(np.int64)
    return pa.table(
        {
            "id": pa.array(ids, type=pa.int64()),
            "geometry": table["geometry"],
            "area": pa.array(areas[ids].astype(np.float64), type=pa.float64()),
        }
    )


def _pass_merges(conn, threshold: float) -> list[tuple[int, int]]:  # noqa: ANN001
    """Return (small_id, target_id) merges for one threshold pass.

    For each polygon with ``area <= threshold`` that has at least one neighbor
    above the threshold, selects the above-threshold neighbor with the longest
    shared boundary. Polygons with no above-threshold neighbor are left for a
    later pass, matching :func:`csb.raster_eliminate.eliminate_pass`.
    """
    rows = conn.execute(
        """
        WITH small AS (SELECT id, geom FROM polys WHERE area <= ?),
             big   AS (SELECT id, geom FROM polys WHERE area >  ?),
             adj AS (
                 SELECT s.id AS small_id, b.id AS target_id,
                        ST_Length(ST_Intersection(s.geom, b.geom)) AS shared_len
                 FROM small s JOIN big b
                   ON ST_Intersects(s.geom, b.geom)
                 WHERE ST_Dimension(ST_Intersection(s.geom, b.geom)) >= 1
             ),
             ranked AS (
                 SELECT small_id, target_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY small_id ORDER BY shared_len DESC, target_id
                        ) AS rn
                 FROM adj
                 WHERE shared_len > 0
             )
        SELECT small_id, target_id FROM ranked WHERE rn = 1
        """,
        [threshold, threshold],
    ).fetchall()
    return [(int(s), int(t)) for s, t in rows]


def _apply_merges(conn, n_labels: int, merges: list[tuple[int, int]]) -> None:  # noqa: ANN001
    """Union-find resolve merges, rewrite the ``polys`` table via ST_Union_Agg."""
    parent = np.arange(n_labels + 1, dtype=np.int64)
    rank = np.zeros(n_labels + 1, dtype=np.int64)
    for small_id, target_id in merges:
        _uf_union(parent, rank, small_id, target_id)
    roots = np.array([_uf_find(parent, i) for i in range(n_labels + 1)], dtype=np.int64)

    remap = pa.table({"id": pa.array(np.arange(n_labels + 1), type=pa.int64()),
                      "root": pa.array(roots, type=pa.int64())})
    conn.register("remap", remap)
    conn.execute(
        """
        CREATE OR REPLACE TABLE polys AS
        SELECT r.root AS id,
               ST_Union_Agg(p.geom) AS geom,
               SUM(p.area) AS area
        FROM polys p JOIN remap r ON p.id = r.id
        GROUP BY r.root
        """
    )
    conn.unregister("remap")


def eliminate_polygons_duckdb(
    lbl: np.ndarray,
    n_labels: int,
    thresholds: list[float],
    transform: object,
    pixel_area: float = CDL_PIXEL_AREA_SQM,
) -> pa.Table:
    """Polygon-side multi-threshold elimination baseline.

    Polygonizes ``lbl`` once, then applies each threshold pass on the vector
    polygons in DuckDB. Returns the surviving polygons as an Arrow table with
    ``id``, ``geom`` (WKB), and ``area`` columns.
    """
    polys = polygons_from_labels(lbl, n_labels, transform, pixel_area)
    conn = _connect()
    try:
        conn.register("polys_arrow", polys)
        conn.execute(
            "CREATE TABLE polys AS "
            "SELECT id, ST_GeomFromWKB(geometry) AS geom, area FROM polys_arrow"
        )
        conn.unregister("polys_arrow")
        for threshold in thresholds:
            merges = _pass_merges(conn, threshold)
            if not merges:
                continue
            _apply_merges(conn, n_labels, merges)
        return conn.execute(
            "SELECT id, ST_AsWKB(geom) AS geom, area FROM polys"
        ).to_arrow_table()
    finally:
        conn.close()
