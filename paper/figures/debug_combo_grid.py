"""For one over-split USDA polygon: rasterize the per-pixel combo (8-byte uint64),
overlay USDA's polygons + ours to see the actual pixel-level decomposition.
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
    ROOT / "data" / "output" / "conus" / "postprocess" / "2018_2025"
    / "national" / "CSB1825_indexed.parquet"
)
USDA = ROOT / "data" / "CSB1825_indexed.parquet"
NATIONAL_CDL = ROOT / "data" / "input" / "national_cdl"
OUT_PDF = Path(__file__).resolve().parent / "debug_combo_grid.pdf"

TX, TY = -100_000, 1_950_000
TILE_BBOX = find_bbox_5070(TX, TY)
UID_TARGET = 96496  # the 195-ac field from prior diagnostic


def main() -> None:
    bx0, by0, bx1, by1 = TILE_BBOX
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET threads=32;")

    conn.execute(f"""
        CREATE TABLE usda AS
        SELECT row_number() OVER () AS uid, ST_MakeValid(geometry) AS g, ST_Area(geometry) AS area
        FROM read_parquet('{USDA}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
    """)
    conn.execute(f"""
        CREATE TABLE ours AS
        SELECT row_number() OVER () AS oid, ST_MakeValid(geometry) AS g
        FROM read_parquet('{OURS}')
        WHERE xmax >= {bx0} AND xmin <= {bx1}
          AND ymax >= {by0} AND ymin <= {by1}
          AND ST_Intersects(geometry, {env})
    """)

    u_row = conn.execute(f"SELECT ST_AsWKB(g), area FROM usda WHERE uid={UID_TARGET}").fetchone()
    u_geom = shapely.from_wkb(bytes(u_row[0]))
    u_area = u_row[1]
    minx, miny, maxx, maxy = u_geom.bounds
    pad = 200.0
    bx = (minx - pad, miny - pad, maxx + pad, maxy + pad)

    # Read 8 years of CDL for this bbox, build per-pixel combo as uint64
    years = list(range(2018, 2026))
    combo = None
    transform = None
    for i, year in enumerate(years):
        cdl_path = NATIONAL_CDL / str(year) / f"{year}_30m_cdls.tif"
        with rasterio.open(cdl_path) as src:
            window = rasterio.windows.from_bounds(*bx, transform=src.transform)
            arr = src.read(1, window=window).astype(np.uint64)
            if transform is None:
                transform = src.window_transform(window)
        # Match our combine: remap non-crop classes (>61, !=0) to 254=BARREN
        non_crop = (arr > 61) & (arr != 0)
        arr[non_crop] = 254
        if combo is None:
            combo = arr << np.uint64(8 * i)
        else:
            combo |= arr << np.uint64(8 * i)
    H, W = combo.shape
    print(f"raster shape: {H}x{W}")

    # Compact unique combos to small ints for visualization
    unique, inv = np.unique(combo.ravel(), return_inverse=True)
    combo_compact = inv.astype(np.int32).reshape(H, W)
    print(f"unique combos in patch: {len(unique)}")

    # Decode each unique combo
    print(f"\nTop 10 combos by pixel count:")
    counts = np.bincount(combo_compact.ravel())
    top = np.argsort(counts)[::-1][:10]
    for i in top:
        c = unique[i]
        years_vals = tuple(int((c >> (8 * j)) & 0xFF) for j in range(8))
        print(f"  {counts[i]:>6}px  ({counts[i]*900/4046.86:.1f} ac)  {years_vals}")

    # Render: 2 panels — combo grid and polygon overlay
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))

    extent = (transform.c, transform.c + W * transform.a,
              transform.f + H * transform.e, transform.f)

    # Panel 1: combo raster, colored by combo ID (each unique combo = unique color)
    ax = axes[0]
    # Pick a color for the "USDA combo" 5,1,5,1,5,1,5,1
    target_combo_uint64 = sum(v << (8 * i) for i, v in enumerate([5, 1, 5, 1, 5, 1, 5, 1]))
    target_idx = np.searchsorted(unique, target_combo_uint64)
    is_target = combo_compact == target_idx
    print(f"\n[5,1,5,1,5,1,5,1] pixels: {is_target.sum()} of {H*W} = {is_target.sum()*900/4046.86:.1f} ac")

    # Colorize: show target combo green, all others by some hash
    rng = np.random.default_rng(seed=42)
    palette = rng.uniform(0, 1, size=(len(unique), 3))
    palette[target_idx] = [0.0, 0.7, 0.2]  # green for the USDA target combo
    rgb = palette[combo_compact]
    ax.imshow(rgb, extent=extent, interpolation="nearest")

    # Overlay USDA focus polygon outline (red)
    u_paths = [np.asarray(p.exterior.coords) for p in shapely.get_parts([u_geom])
               if shapely.get_type_id(p) == 3 and not p.is_empty]
    ax.add_collection(PolyCollection(u_paths, facecolor="none", edgecolor="#c0392b", lw=2.0))
    ax.set_xlim(bx[0], bx[2]); ax.set_ylim(bx[1], bx[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"per-pixel combo (green = USDA's combo {[5,1,5,1,5,1,5,1]})\n"
                 f"USDA polygon (red) covers {u_area/4046.86:.0f} ac, "
                 f"green pixels in patch = {is_target.sum()*900/4046.86:.0f} ac",
                 fontsize=9)

    # Panel 2: ours and USDA polygons overlay
    ax = axes[1]
    env_b = f"ST_MakeEnvelope({bx[0]}, {bx[1]}, {bx[2]}, {bx[3]})"
    ours_geoms = conn.execute(
        f"SELECT ST_AsWKB(g) FROM ours WHERE ST_Intersects(g, {env_b})"
    ).fetchall()
    ours_paths = []
    for (wkb,) in ours_geoms:
        g = shapely.from_wkb(bytes(wkb))
        for part in shapely.get_parts([g]):
            if shapely.get_type_id(part) == 3 and not part.is_empty:
                ours_paths.append(np.asarray(part.exterior.coords))
    ax.add_collection(PolyCollection(ours_paths, facecolor="#3aa8ff",
                                      edgecolor="#1f6bb0", lw=0.7, alpha=0.4))
    usda_geoms = conn.execute(
        f"SELECT ST_AsWKB(g) FROM usda WHERE ST_Intersects(g, {env_b})"
    ).fetchall()
    usda_paths = []
    for (wkb,) in usda_geoms:
        g = shapely.from_wkb(bytes(wkb))
        for part in shapely.get_parts([g]):
            if shapely.get_type_id(part) == 3 and not part.is_empty:
                usda_paths.append(np.asarray(part.exterior.coords))
    ax.add_collection(PolyCollection(usda_paths, facecolor="none",
                                      edgecolor="#a86d12", lw=1.2))
    ax.add_collection(PolyCollection(u_paths, facecolor="none", edgecolor="#c0392b",
                                      lw=2.0, ls="--"))
    ax.set_xlim(bx[0], bx[2]); ax.set_ylim(bx[1], bx[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"polygons: ours (blue, {len(ours_geoms)} polys), "
                 f"USDA (orange, {len(usda_geoms)} polys),\nfocus USDA (red dashed)",
                 fontsize=9)

    fig.suptitle(f"Combo-grid debug for USDA uid={UID_TARGET} (Iowa I15)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT_PDF, bbox_inches="tight", dpi=200)
    print(f"\nwrote {OUT_PDF}")


if __name__ == "__main__":
    main()
