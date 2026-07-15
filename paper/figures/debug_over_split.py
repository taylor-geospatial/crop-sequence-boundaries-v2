"""For one over-split case on Iowa I15: pull USDA's combo and our polygons'
combos, plus the per-year CDL pixel raster underneath.

Tells us whether our small polygons share USDA's combo (= eliminate /
simplification difference) or have different combos (= mask/combine
upstream difference).
"""

import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import rasterio.windows
import shapely
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
NATIONAL_CDL = ROOT / "data" / "input" / "national_cdl"
OUT_PDF = Path(__file__).resolve().parent / "debug_over_split.pdf"

TX, TY = -100_000, 1_950_000
TILE_BBOX = find_bbox_5070(TX, TY)


def main() -> None:
    bx0, by0, bx1, by1 = TILE_BBOX
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET threads=32;")

    conn.execute(f"""
        CREATE TABLE ours AS
        SELECT row_number() OVER () AS oid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area,
               CDL2018, CDL2019, CDL2020, CDL2021, CDL2022, CDL2023, CDL2024, CDL2025
        FROM read_parquet('{OURS}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
    """)
    conn.execute(f"""
        CREATE TABLE usda AS
        SELECT row_number() OVER () AS uid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area,
               CDL2018, CDL2019, CDL2020, CDL2021, CDL2022, CDL2023, CDL2024, CDL2025, CSBID
        FROM read_parquet('{USDA}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
    """)

    # Find one specific over-split USDA polygon: large area, many of our
    # polygons intersect it, low IoU
    conn.execute("""
        CREATE TABLE pairs AS
        SELECT u.uid, o.oid, ST_Area(ST_Intersection(u.g, o.g)) AS inter,
               u.area AS u_area, o.area AS o_area
        FROM usda u JOIN ours o ON ST_Intersects(u.g, o.g)
    """)
    conn.execute("""
        CREATE TABLE per_usda AS
        SELECT u.uid, u.area AS u_area,
               COUNT(o.oid) AS n_ours,
               SUM(CASE WHEN o.area / u.area < 1 THEN 1 ELSE 0 END) AS n_ours_smaller
        FROM usda u JOIN ours o ON ST_Intersects(u.g, o.g)
        GROUP BY u.uid, u.area
    """)
    target = conn.execute("""
        SELECT uid, u_area, n_ours
        FROM per_usda
        WHERE u_area BETWEEN 200000 AND 800000  -- 50-200 acre fields
          AND n_ours >= 5
        ORDER BY n_ours DESC
        LIMIT 1
    """).fetchone()
    if target is None:
        print("no over-split sample found")
        return
    uid, u_area, n_ours = target
    print(
        f"target: uid={uid}  u_area={u_area:.0f} m² ({u_area / 4046.86:.1f} ac)  ours_count={n_ours}"
    )

    # Pull the focus polygon + CSBID + combo
    u_row = conn.execute(f"""
        SELECT ST_AsWKB(g), CDL2018, CDL2019, CDL2020, CDL2021,
               CDL2022, CDL2023, CDL2024, CDL2025, CSBID
        FROM usda WHERE uid={uid}
    """).fetchone()
    assert u_row is not None
    u_geom = shapely.from_wkb(bytes(u_row[0]))
    usda_combo = list(u_row[1:9])
    csbid = u_row[9]
    print(f"USDA combo:  {usda_combo}  CSBID={csbid}")

    # Pull our intersecting polygons + their combos
    minx, miny, maxx, maxy = u_geom.bounds
    pad = 100.0
    bx = (minx - pad, miny - pad, maxx + pad, maxy + pad)
    env_b = f"ST_MakeEnvelope({bx[0]}, {bx[1]}, {bx[2]}, {bx[3]})"
    # Polygons that actually intersect the focus USDA polygon (not just bbox)
    u_wkb_hex = u_geom.wkb.hex()
    rows_inside = conn.execute(f"""
        SELECT ST_AsWKB(g), CDL2018, CDL2019, CDL2020, CDL2021,
               CDL2022, CDL2023, CDL2024, CDL2025, area,
               ST_Area(ST_Intersection(g, ST_GeomFromHEXWKB('{u_wkb_hex}'))) AS inter
        FROM ours
        WHERE ST_Intersects(g, ST_GeomFromHEXWKB('{u_wkb_hex}'))
        ORDER BY inter DESC
    """).fetchall()
    print(f"\nours polygons intersecting focus USDA: {len(rows_inside)}")
    print(f"{'oid':>4}  {'inter(ac)':>10}  {'area(ac)':>10}  combo")
    combo_counts_inside: dict[tuple, float] = {}
    for i, r in enumerate(rows_inside):
        combo = tuple(r[1:9])
        area = r[9]
        inter = r[10]
        combo_counts_inside[combo] = combo_counts_inside.get(combo, 0) + inter
        if i < 12:
            match = "= USDA" if combo == tuple(usda_combo) else " "
            print(
                f"  {i:>2}   {inter / 4046.86:>10.2f}   {area / 4046.86:>10.2f}   {combo}  {match}"
            )

    print("\nintersection-area share by combo (top 8, fraction of focus polygon):")
    total_u = u_area
    for c, ia in sorted(combo_counts_inside.items(), key=lambda x: -x[1])[:8]:
        match = " == USDA" if c == tuple(usda_combo) else ""
        print(f"  {ia / total_u * 100:>5.1f}%  {c}{match}")

    # Also show all polygons in the bbox for the visualization (but report
    # combo distribution only for ones inside the USDA polygon)
    rows = conn.execute(f"""
        SELECT ST_AsWKB(g), CDL2018, CDL2019, CDL2020, CDL2021,
               CDL2022, CDL2023, CDL2024, CDL2025, area
        FROM ours
        WHERE ST_Intersects(g, {env_b})
        ORDER BY area DESC
    """).fetchall()

    # Pull per-year CDL underneath the focus bbox
    fig, axes = plt.subplots(2, 5, figsize=(11.0, 5.0))
    axes_flat = axes.flatten()

    # First panel: USDA polygon outlined + ours filled
    ax = axes_flat[0]
    ours_paths = []
    for r in rows:
        g = shapely.from_wkb(bytes(r[0]))
        ours_paths.extend(
            np.asarray(part.exterior.coords)
            for part in shapely.get_parts([g])
            if shapely.get_type_id(part) == 3 and not part.is_empty
        )
    ax.add_collection(
        PolyCollection(ours_paths, facecolor="#3aa8ff", edgecolor="#1f6bb0", lw=0.6, alpha=0.45)
    )
    u_paths = [
        np.asarray(p.exterior.coords)
        for p in shapely.get_parts([u_geom])
        if shapely.get_type_id(p) == 3 and not p.is_empty
    ]
    ax.add_collection(PolyCollection(u_paths, facecolor="none", edgecolor="#c0392b", lw=1.6))
    ax.set_xlim(bx[0], bx[2])
    ax.set_ylim(bx[1], bx[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(f"polygons (1x USDA, {n_ours} ours)\nUSDA combo {usda_combo}", fontsize=7.5)

    # 8 CDL year panels
    for i, year in enumerate(range(2018, 2026)):
        ax = axes_flat[i + 1]
        cdl_path = NATIONAL_CDL / str(year) / f"{year}_30m_cdls.tif"
        if not cdl_path.exists():
            ax.set_title(f"CDL {year} (missing)", fontsize=7.5)
            ax.set_xticks([])
            ax.set_yticks([])
            continue
        with rasterio.open(cdl_path) as src:
            window = rasterio.windows.from_bounds(*bx, transform=src.transform)
            data = src.read(1, window=window).astype(np.int16)
            wt = src.window_transform(window)
        ax.imshow(
            data,
            extent=(wt.c, wt.c + data.shape[1] * wt.a, wt.f + data.shape[0] * wt.e, wt.f),
            interpolation="nearest",
            cmap="tab20",
            vmin=0,
            vmax=255,
        )
        ax.add_collection(PolyCollection(u_paths, facecolor="none", edgecolor="#c0392b", lw=1.0))
        ax.set_xlim(bx[0], bx[2])
        ax.set_ylim(bx[1], bx[3])
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"CDL {year}\nUSDA={usda_combo[i]}", fontsize=7.5)

    # Last panel left blank if only 8 CDL years
    axes_flat[9].axis("off")

    fig.suptitle(
        f"Over-split debug: USDA uid={uid} ({u_area / 4046.86:.0f} ac) → {n_ours} ours polys",
        fontsize=10,
    )
    fig.tight_layout()
    fig.savefig(OUT_PDF, bbox_inches="tight")
    print(f"\nwrote {OUT_PDF}")


if __name__ == "__main__":
    main()
