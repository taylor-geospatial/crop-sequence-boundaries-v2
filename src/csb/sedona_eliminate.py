"""Polygon-side ``Eliminate(LENGTH)`` baseline in SedonaDB.

Functionally identical to :mod:`csb.polygon_eliminate` (the DuckDB baseline) —
same longest-shared-edge merge, same union-find resolution — implemented on the
single-node SedonaDB (Arrow/DataFusion) engine instead. Kept so the two engines
can be benchmarked head-to-head (see :mod:`csb.bench`) for speed, peak memory,
and correctness; the pipeline uses whichever wins. Neither engine appears in the
paper: the polygon-side path is only the strawman the raster reformulation
replaces.
"""

import uuid

import numpy as np
import pyarrow as pa

from csb.config import CDL_PIXEL_AREA_SQM
from csb.polygon_eliminate import polygons_from_labels
from csb.raster_eliminate import _uf_find, _uf_union


def _connect():  # noqa: ANN202 — sedonadb context type is version-dependent
    import sedonadb

    return sedonadb.connect()


def _register_geom(ctx, names: dict[str, str], wkb_table: pa.Table) -> None:  # noqa: ANN001
    """Register ``wkb_table`` (id, geometry WKB, area) as geometry view ``polys``.

    The WKB column is renamed to ``wkb`` first: SedonaDB auto-promotes a column
    named ``geometry`` to its geometry type on ingest, which then makes
    ``ST_GeomFromWKB`` reject it ("no kernel"). Under a neutral name it stays
    plain binary and parses cleanly.
    """
    renamed = wkb_table.rename_columns(
        ["wkb" if n == "geometry" else n for n in wkb_table.schema.names]
    )
    ctx.create_data_frame(renamed).to_view(names["raw"], overwrite=True)
    # Once SedonaDB has registered its geoarrow pyarrow extension types (after
    # any prior to_arrow_table in the process), a plain WKB binary column is
    # ingested as a geometry-typed column and ST_GeomFromWKB rejects it. Probe
    # the ingested type with a zero-row query and only decode if still binary.
    wkb_type = ctx.sql(f"SELECT wkb FROM {names['raw']} LIMIT 0").to_arrow_table().schema.field(0).type
    geom_expr = "wkb" if "geoarrow" in str(wkb_type) else "ST_GeomFromWKB(wkb)"
    ctx.sql(
        f"SELECT id, {geom_expr} AS geom, area FROM {names['raw']}"
    ).to_memtable().to_view(names["polys"], overwrite=True)
    ctx.drop_view(names["raw"])


def _pass_merges(ctx, names: dict[str, str], threshold: float) -> list[tuple[int, int]]:  # noqa: ANN001
    """(small_id, target_id) merges for one threshold pass — see the DuckDB twin."""
    polys = names["polys"]
    tbl = ctx.sql(
        f"""
        WITH small AS (SELECT id, geom FROM {polys} WHERE area <= {threshold}),
             big   AS (SELECT id, geom FROM {polys} WHERE area >  {threshold}),
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
        """
    ).to_arrow_table()
    smalls = tbl.column("small_id").to_pylist()
    targets = tbl.column("target_id").to_pylist()
    return [(int(s), int(t)) for s, t in zip(smalls, targets, strict=True)]


def _apply_merges(
    ctx,  # noqa: ANN001
    names: dict[str, str],
    n_labels: int,
    merges: list[tuple[int, int]],
) -> None:
    """Union-find resolve merges, rewrite ``polys`` via ST_Union_Agg."""
    parent = np.arange(n_labels + 1, dtype=np.int64)
    rank = np.zeros(n_labels + 1, dtype=np.int64)
    for small_id, target_id in merges:
        _uf_union(parent, rank, small_id, target_id)
    roots = np.array([_uf_find(parent, i) for i in range(n_labels + 1)], dtype=np.int64)

    remap = pa.table(
        {
            "id": pa.array(np.arange(n_labels + 1), type=pa.int64()),
            "root": pa.array(roots, type=pa.int64()),
        }
    )
    ctx.create_data_frame(remap).to_view(names["remap"], overwrite=True)
    ctx.sql(
        f"""
        SELECT r.root AS id, ST_Union_Agg(p.geom) AS geom, SUM(p.area) AS area
        FROM {names['polys']} p JOIN {names['remap']} r ON p.id = r.id
        GROUP BY r.root
        """
    ).to_memtable().to_view(names["polys"], overwrite=True)
    ctx.drop_view(names["remap"])


def eliminate_polygons_sedona(
    lbl: np.ndarray,
    n_labels: int,
    thresholds: list[float],
    transform: object,
    pixel_area: float = CDL_PIXEL_AREA_SQM,
) -> pa.Table:
    """Polygon-side multi-threshold elimination baseline on SedonaDB.

    Returns surviving polygons as an Arrow table with ``id``, ``geom`` (WKB),
    and ``area`` columns — matching :func:`csb.polygon_eliminate.eliminate_polygons_duckdb`.
    """
    polys = polygons_from_labels(lbl, n_labels, transform, pixel_area)
    ctx = _connect()
    # SedonaDB contexts share a process-global catalog, so use per-call unique
    # view names to avoid collisions when the function is invoked more than once.
    tag = uuid.uuid4().hex[:12]
    names = {"raw": f"raw_{tag}", "polys": f"polys_{tag}", "remap": f"remap_{tag}"}
    try:
        _register_geom(ctx, names, polys)
        for threshold in thresholds:
            merges = _pass_merges(ctx, names, threshold)
            if not merges:
                continue
            _apply_merges(ctx, names, n_labels, merges)
        return ctx.sql(
            f"SELECT id, ST_AsBinary(geom) AS geom, area FROM {names['polys']}"
        ).to_arrow_table()
    finally:
        ctx.drop_view(names["polys"])
