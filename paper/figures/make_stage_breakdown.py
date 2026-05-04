"""Per-stage wall-clock breakdown as a single horizontal stacked bar."""
import json
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "data" / "profile" / "v2b_iowa_5000.json"
OUT = Path(__file__).resolve().parent / "stage_breakdown.pdf"

p = json.load(PROFILE.open())
load = {e["stage"]: e["sec"] for e in p["load"]}
raster = {e["stage"].split(".", 1)[-1]: e["sec"] for e in p["raster"]}

groups = [
    ("Load + key", "#2c5f8d", [
        ("CDL read I/O", load["load_window"]),
        ("uint64 pack",  load["combine_unique"]),
    ]),
    ("Raster", "#5a9bd4", [
        ("mask",          raster["mask"]),
        ("CC label",      raster["label_cc"]),
        ("label→combo",   raster["label_to_combo"]),
        ("numpy filter",  raster["numpy_filter"]),
        ("eliminate (4-pass)", raster["eliminate_raster"]),
    ]),
    ("Vector emit", "#d4801a", [
        ("polygonize",    raster["polygonize_once"]),
        ("eff lookup",    raster["eff_lookup"]),
        ("coverage simplify", raster["simplify_shapely"]),
    ]),
]

total = sum(s for _, _, items in groups for _, s in items)
fig, ax = plt.subplots(figsize=(6.6, 1.55))
left = 0
for gname, color, items in groups:
    for label, sec in items:
        pct = sec / total * 100
        ax.barh(0, pct, left=left, color=color, edgecolor="white", lw=0.7, height=0.55)
        if pct >= 4.5:
            ax.text(left + pct / 2, 0, label, ha="center", va="center",
                    fontsize=7.5, color="white", weight="semibold")
        left += pct
ax.set_xlim(0, 100)
ax.set_ylim(-0.4, 0.55)
ax.set_yticks([])
ax.set_xlabel("\\% of per-tile wall (5000$\\times$5000\\,px, 24.9\\,s total)", fontsize=9)
ax.tick_params(axis="x", labelsize=8)
for spine in ("top", "right", "left"):
    ax.spines[spine].set_visible(False)

handles = [plt.Rectangle((0, 0), 1, 1, fc=c, ec="white") for _, c, _ in groups]
labels = [g for g, _, _ in groups]
ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.55),
          ncol=3, fontsize=8, frameon=False, handlelength=1.1)

fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print(f"wrote {OUT}  total={total:.2f}s")
