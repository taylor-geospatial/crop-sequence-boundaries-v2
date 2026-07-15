"""Pull example USDA polygons across the IoU spectrum and render our+USDA polys
in each one's neighborhood. Used to see what kind of decomposition mismatch
dominates: over-merge, over-split, boundary drift, or shape-fundamentally-wrong.
"""

import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import shapely
from matplotlib.axes import Axes
from matplotlib.collections import PolyCollection

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from csb.parity import find_bbox_5070  # noqa: E402

OURS = (
    ROOT
    / "data"
    / "output"
    / "conus"
    / "postprocess"
    / "2018_2025"
    / "national"
    / "CSB1825_indexed.parquet"
)
USDA = ROOT / "data" / "CSB1825_indexed.parquet"
OUT_PDF = Path(__file__).resolve().parent / "field_mismatch_examples.pdf"

TX, TY = -100_000, 1_950_000
BBOX = find_bbox_5070(TX, TY)
PAD = 200.0  # 200 m buffer around each focus polygon for context


def _draw_geometries(
    ax: Axes,
    rows: list[tuple[bytes]],
    facecolor: str,
    edgecolor: str,
    alpha: float,
    linewidth: float,
) -> None:
    paths = []
    for (wkb,) in rows:
        geom = shapely.from_wkb(bytes(wkb))
        paths.extend(
            np.asarray(part.exterior.coords)
            for part in shapely.get_parts([geom])
            if shapely.get_type_id(part) == 3 and not part.is_empty
        )
    if paths:
        ax.add_collection(
            PolyCollection(
                paths,
                facecolor=facecolor,
                edgecolor=edgecolor,
                lw=linewidth,
                alpha=alpha,
            )
        )


def main() -> None:
    bx0, by0, bx1, by1 = BBOX
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET threads=32;")

    conn.execute(f"""
        CREATE TABLE ours AS
        SELECT row_number() OVER () AS oid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area
        FROM read_parquet('{OURS}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
    """)
    conn.execute(f"""
        CREATE TABLE usda AS
        SELECT row_number() OVER () AS uid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area
        FROM read_parquet('{USDA}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
    """)
    conn.execute("""
        CREATE TABLE pairs AS
        SELECT u.uid, o.oid, ST_Area(ST_Intersection(u.g, o.g)) AS inter,
               u.area AS u_area, o.area AS o_area
        FROM usda u JOIN ours o ON ST_Intersects(u.g, o.g)
    """)
    conn.execute("""
        CREATE TABLE best AS
        SELECT uid, oid, inter, u_area, o_area,
               (inter / (u_area + o_area - inter)) AS iou
        FROM (
            SELECT *, row_number() OVER (PARTITION BY uid ORDER BY inter DESC) AS rn
            FROM pairs
        ) WHERE rn = 1
    """)

    # Pick 6 representative cases:
    # 2 from worst-IoU 0-0.2
    # 2 from middle 0.4-0.6
    # 2 from near-match 0.8+
    # Stratify on field area to avoid all-tiny examples
    queries = [
        ("worst1", "WHERE iou < 0.2 AND u_area > 50000 ORDER BY u_area DESC LIMIT 1 OFFSET 5"),
        ("worst2", "WHERE iou < 0.2 AND u_area > 50000 ORDER BY u_area DESC LIMIT 1 OFFSET 12"),
        (
            "mid1",
            "WHERE iou BETWEEN 0.4 AND 0.6 AND u_area > 80000 ORDER BY u_area DESC LIMIT 1 OFFSET 5",
        ),
        (
            "mid2",
            "WHERE iou BETWEEN 0.4 AND 0.6 AND u_area > 80000 ORDER BY u_area DESC LIMIT 1 OFFSET 12",
        ),
        ("good1", "WHERE iou > 0.85 AND u_area > 100000 ORDER BY u_area DESC LIMIT 1 OFFSET 5"),
        ("good2", "WHERE iou > 0.85 AND u_area > 100000 ORDER BY u_area DESC LIMIT 1 OFFSET 12"),
    ]
    samples = []
    for tag, where in queries:
        row = conn.execute(f"SELECT uid, iou, u_area, o_area FROM best {where}").fetchone()
        if row is None:
            print(f"warn: no sample for {tag}")
            continue
        uid, iou, u_area, o_area = row
        env_row = conn.execute(f"SELECT ST_AsWKB(g) FROM usda WHERE uid={uid}").fetchone()
        assert env_row is not None
        env_pol = env_row[0]
        u_geom = shapely.from_wkb(bytes(env_pol))
        samples.append((tag, uid, iou, u_area, o_area, u_geom))

    print(f"{len(samples)} samples")
    fig, axes = plt.subplots(2, 3, figsize=(8.5, 6.0))
    axes = axes.flatten()
    for ax, (tag, _uid, iou, u_area, o_area, u_geom) in zip(axes, samples, strict=True):
        minx, miny, maxx, maxy = u_geom.bounds
        bx = (minx - PAD, miny - PAD, maxx + PAD, maxy + PAD)
        env_str = f"ST_MakeEnvelope({bx[0]}, {bx[1]}, {bx[2]}, {bx[3]})"
        ours_geoms = conn.execute(
            f"SELECT ST_AsWKB(g) FROM ours WHERE ST_Intersects(g, {env_str})"
        ).fetchall()
        usda_geoms = conn.execute(
            f"SELECT ST_AsWKB(g) FROM usda WHERE ST_Intersects(g, {env_str})"
        ).fetchall()

        _draw_geometries(ax, ours_geoms, "#3aa8ff", "#1f6bb0", 0.35, 0.6)
        _draw_geometries(ax, usda_geoms, "#e89c2b", "#a86d12", 0.0, 1.0)
        # Highlight focus polygon outline
        ax.add_collection(
            PolyCollection(
                [
                    np.asarray(p.exterior.coords)
                    for p in shapely.get_parts([u_geom])
                    if shapely.get_type_id(p) == 3 and not p.is_empty
                ],
                facecolor="none",
                edgecolor="#c0392b",
                lw=1.6,
                ls="--",
            )
        )
        ax.set_xlim(bx[0], bx[2])
        ax.set_ylim(bx[1], bx[3])
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(
            f"{tag}: IoU {iou:.2f}\n"
            f"USDA {u_area / 4046.86:.0f} ac, ours match {o_area / 4046.86:.0f} ac",
            fontsize=8.5,
        )
        for s in ax.spines.values():
            s.set_edgecolor("0.7")
            s.set_linewidth(0.5)

    legend_handles = [
        plt.Rectangle((0, 0), 1, 1, fc="#3aa8ff", ec="#1f6bb0", alpha=0.5),
        plt.Rectangle((0, 0), 1, 1, fc="none", ec="#a86d12", lw=1.5),
        plt.Rectangle((0, 0), 1, 1, fc="none", ec="#c0392b", ls="--", lw=1.5),
    ]
    fig.legend(
        legend_handles,
        ["ours (filled)", "USDA outline", "focus USDA polygon"],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=3,
        fontsize=9,
        frameon=False,
    )
    fig.suptitle(
        "Iowa I15 — per-field correspondence examples across the IoU spectrum", fontsize=10, y=0.995
    )
    fig.tight_layout()
    fig.savefig(OUT_PDF, bbox_inches="tight")
    print(f"wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
