"""Per-field IoU on Iowa I15: 1-to-1 best-match correspondence vs USDA CSB1825.

For each USDA polygon u in the I15 bbox, finds the ours polygon o* with
max ST_Area(ST_Intersection(o, u)) and computes the pair IoU. Reports
the distribution and writes a histogram.
"""

import json
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np

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
OUT_PDF = Path(__file__).resolve().parent / "per_field_iou.pdf"
OUT_JSON = Path(__file__).resolve().parent / "per_field_iou.json"

# Iowa corn belt I15
TX, TY = -100_000, 1_950_000
BBOX = find_bbox_5070(TX, TY)
print(f"I15 bbox: {BBOX}")


def main() -> None:
    bx0, by0, bx1, by1 = BBOX
    env = f"ST_MakeEnvelope({bx0}, {by0}, {bx1}, {by1})"
    conn = duckdb.connect(":memory:")
    conn.execute("INSTALL spatial; LOAD spatial;")
    conn.execute("SET threads=32;")

    # Clip both sides to bbox; assign row IDs so we can group later.
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
    ours_count = conn.execute("SELECT COUNT(*) FROM ours").fetchone()
    usda_count = conn.execute("SELECT COUNT(*) FROM usda").fetchone()
    assert ours_count is not None
    assert usda_count is not None
    n_ours = ours_count[0]
    n_usda = usda_count[0]
    print(f"  ours={n_ours}  usda={n_usda}")

    # Pairwise intersections via spatial join. Keep top-1 ours match per usda.
    print("computing pairwise intersections...")
    conn.execute("""
        CREATE TABLE pairs AS
        SELECT
            u.uid,
            o.oid,
            ST_Area(ST_Intersection(u.g, o.g)) AS inter_area,
            u.area AS u_area,
            o.area AS o_area
        FROM usda u JOIN ours o ON ST_Intersects(u.g, o.g)
    """)
    pair_count = conn.execute("SELECT COUNT(*) FROM pairs").fetchone()
    assert pair_count is not None
    n_pairs = pair_count[0]
    print(f"  candidate pairs: {n_pairs}")

    # Best-match: for each usda u, take the ours o with the largest intersection.
    conn.execute("""
        CREATE TABLE best AS
        SELECT uid, oid, inter_area, u_area, o_area,
               (inter_area / (u_area + o_area - inter_area)) AS iou,
               (inter_area / u_area) AS coverage_u
        FROM (
            SELECT *, row_number() OVER (PARTITION BY uid ORDER BY inter_area DESC) AS rn
            FROM pairs
        ) WHERE rn = 1
    """)

    # Pull the IoU and coverage distributions.
    rows = conn.execute("""
        SELECT iou, coverage_u, u_area, o_area, inter_area FROM best
    """).fetchall()
    iou = np.array([r[0] for r in rows], dtype=np.float64)
    u_area = np.array([r[2] for r in rows], dtype=np.float64)
    o_area = np.array([r[3] for r in rows], dtype=np.float64)

    # Buckets:
    near = (iou >= 0.9).sum()  # essentially-same field
    partial = ((iou >= 0.5) & (iou < 0.9)).sum()  # split / merged / boundary drift
    poor = (iou < 0.5).sum()  # fundamentally wrong decomposition
    none = n_usda - len(iou)  # USDA polygon had no matching ours overlap at all

    summary = {
        "tile": "I15",
        "bbox": list(BBOX),
        "n_ours": int(n_ours),
        "n_usda": int(n_usda),
        "n_matched": len(iou),
        "iou_mean": float(np.mean(iou)) if len(iou) else None,
        "iou_median": float(np.median(iou)) if len(iou) else None,
        "iou_p10": float(np.percentile(iou, 10)) if len(iou) else None,
        "iou_p90": float(np.percentile(iou, 90)) if len(iou) else None,
        "n_near": int(near),
        "n_partial": int(partial),
        "n_poor": int(poor),
        "n_unmatched": int(none),
        "share_near": float(near / n_usda),
        "share_partial": float(partial / n_usda),
        "share_poor": float(poor / n_usda),
        "share_unmatched": float(none / n_usda),
        "size_ratio_mean": float(np.mean(o_area / u_area)) if len(iou) else None,
        "size_ratio_median": float(np.median(o_area / u_area)) if len(iou) else None,
    }
    print(json.dumps(summary, indent=2))
    OUT_JSON.write_text(json.dumps(summary, indent=2))

    # Plot histogram
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.0))
    ax1.hist(iou, bins=50, range=(0, 1), color="#2c5f8d", edgecolor="white", lw=0.4)
    ax1.axvline(
        np.median(iou), color="#d4801a", lw=1.2, ls="--", label=f"median {np.median(iou):.2f}"
    )
    ax1.set_xlabel("Per-field IoU vs USDA best-match", fontsize=9)
    ax1.set_ylabel("USDA polygons", fontsize=9)
    ax1.set_xlim(0, 1)
    ax1.tick_params(labelsize=8)
    ax1.legend(fontsize=8, frameon=False)
    ax1.grid(axis="y", alpha=0.25, lw=0.4)
    for s in ("top", "right"):
        ax1.spines[s].set_visible(False)

    # Stacked bar of bucket shares
    sizes = [
        summary["share_near"],
        summary["share_partial"],
        summary["share_poor"],
        summary["share_unmatched"],
    ]
    labels = ["IoU≥0.9", "0.5≤IoU<0.9", "IoU<0.5", "no match"]
    colors = ["#2c5f8d", "#5a9bd4", "#d4801a", "#c0392b"]
    left = 0
    for s, lab, c in zip(sizes, labels, colors, strict=True):
        ax2.barh(0, s, left=left, color=c, edgecolor="white", lw=0.7, height=0.55)
        if s > 0.04:
            ax2.text(
                left + s / 2,
                0,
                f"{lab}\n{s * 100:.0f}%",
                ha="center",
                va="center",
                fontsize=8,
                color="white",
                weight="semibold",
            )
        left += s
    ax2.set_xlim(0, 1)
    ax2.set_ylim(-0.5, 0.5)
    ax2.set_yticks([])
    ax2.set_xlabel("share of USDA polygons", fontsize=9)
    ax2.tick_params(labelsize=8)
    for s in ("top", "right", "left"):
        ax2.spines[s].set_visible(False)

    fig.suptitle(
        f"Per-field correspondence on Iowa I15: ours={n_ours:,} polys, USDA={n_usda:,}", fontsize=10
    )
    fig.tight_layout()
    fig.savefig(OUT_PDF, bbox_inches="tight")
    print(f"wrote {OUT_PDF}")


if __name__ == "__main__":
    main()
