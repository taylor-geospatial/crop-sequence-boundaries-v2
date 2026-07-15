"""4-panel visual diff: ours, USDA, intersection, symmetric difference.

Used to create comparison figures and spot-check parity regressions. Reads
from the indexed parquets emitted by ``csb parity-prep`` so the bbox query
benefits from row-group pruning.
"""

import logging
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import shapely
from matplotlib.collections import PolyCollection

logger = logging.getLogger(__name__)


def _query_bbox(
    parquet: Path, bbox_5070: tuple[float, float, float, float]
) -> list[shapely.Geometry]:
    bx0, by0, bx1, by1 = bbox_5070
    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    rows = conn.execute(f"""
        SELECT ST_AsWKB(ST_MakeValid(geometry))
        FROM read_parquet('{parquet}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1}))
    """).fetchall()
    conn.close()
    return [g for g in shapely.from_wkb([bytes(r[0]) for r in rows]) if g is not None]


def _polys_to_paths(geoms: list[shapely.Geometry]) -> list[np.ndarray]:
    """Flatten Polygon / MultiPolygon to a list of (N, 2) exterior-ring arrays."""
    # shapely.get_parts flattens (Multi)Polygon and GeometryCollection in one
    # pass; non-polygon parts (which make_valid can emit) are filtered.
    parts = shapely.get_parts(geoms)
    parts = parts[shapely.get_type_id(parts) == 3]  # 3 = Polygon
    return [np.asarray(p.exterior.coords) for p in parts if not p.is_empty]


def _draw(
    ax: plt.Axes,  # type: ignore[name-defined]
    paths: list[np.ndarray],
    *,
    facecolor: str,
    edgecolor: str,
    alpha: float,
    title: str,
    bbox: tuple[float, float, float, float],
) -> None:
    coll = PolyCollection(
        paths, facecolors=facecolor, edgecolors=edgecolor, alpha=alpha, linewidths=0.2
    )
    ax.add_collection(coll)
    ax.set_xlim(bbox[0], bbox[2])
    ax.set_ylim(bbox[1], bbox[3])
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=10)
    ax.set_xticks([])
    ax.set_yticks([])


def render_comparison(
    ours_parquet: Path,
    usda_parquet: Path,
    bbox_5070: tuple[float, float, float, float],
    output_png: Path,
    *,
    title: str = "",
    dpi: int = 200,
) -> Path:
    """Render a 4-panel PNG: ours / USDA / intersection / symmetric-diff.

    All panels share the same bbox extent. The intersection panel shades
    where ours and USDA agree spatially; the symmetric-diff panel shades
    where exactly one side claims cropland.
    """
    logger.info("loading ours from %s", ours_parquet)
    ours = _query_bbox(ours_parquet, bbox_5070)
    logger.info("loading USDA from %s", usda_parquet)
    usda = _query_bbox(usda_parquet, bbox_5070)
    if not ours and not usda:
        msg = f"both inputs empty in bbox {bbox_5070}"
        raise RuntimeError(msg)

    # CSB polygons within a tile are non-overlapping coverages, so
    # ``coverage_union`` (linear time, parallel-friendly) handily beats
    # ``unary_union`` (cascaded GEOS union, serial-ish on large inputs).
    logger.info("computing intersection / symmetric difference")
    empty = shapely.from_wkt("POLYGON EMPTY")
    try:
        ours_union = shapely.coverage_union_all(ours) if ours else empty
        usda_union = shapely.coverage_union_all(usda) if usda else empty
    except shapely.errors.GEOSException:
        # Fall back if either side has overlaps (shouldn't happen post-dissolve
        # but guards against test fixtures or edge cases).
        ours_union = shapely.unary_union(ours) if ours else empty
        usda_union = shapely.unary_union(usda) if usda else empty
    ours_union = shapely.make_valid(ours_union)
    usda_union = shapely.make_valid(usda_union)
    intersection = ours_union.intersection(usda_union)
    sym_diff = ours_union.symmetric_difference(usda_union)

    fig, axes = plt.subplots(1, 4, figsize=(16, 4.5))
    if title:
        fig.suptitle(title, fontsize=11)
    _draw(
        axes[0],
        _polys_to_paths(ours),
        facecolor="#1f77b4",
        edgecolor="#0d4f8b",
        alpha=0.6,
        title=f"ours ({len(ours):,} polys)",
        bbox=bbox_5070,
    )
    _draw(
        axes[1],
        _polys_to_paths(usda),
        facecolor="#ff7f0e",
        edgecolor="#a04500",
        alpha=0.6,
        title=f"USDA ({len(usda):,} polys)",
        bbox=bbox_5070,
    )
    inter_paths = _polys_to_paths([intersection]) if not intersection.is_empty else []
    _draw(
        axes[2],
        inter_paths,
        facecolor="#2ca02c",
        edgecolor="#1a6e1a",
        alpha=0.6,
        title=f"intersection ({intersection.area / 1e6:.1f} km²)",
        bbox=bbox_5070,
    )
    sym_paths = _polys_to_paths([sym_diff]) if not sym_diff.is_empty else []
    _draw(
        axes[3],
        sym_paths,
        facecolor="#d62728",
        edgecolor="#7c1818",
        alpha=0.55,
        title=f"sym-diff ({sym_diff.area / 1e6:.1f} km²)",
        bbox=bbox_5070,
    )
    fig.tight_layout()
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    logger.info("wrote %s", output_png)
    return output_png
