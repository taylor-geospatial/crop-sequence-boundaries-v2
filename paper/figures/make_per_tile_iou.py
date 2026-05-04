"""Per-tile IoU + acres-ratio ranked bar chart for the 16 CONUS parity tiles."""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "data" / "output" / "conus" / "parity_16regions.json"
OUT = Path(__file__).resolve().parent / "per_tile_iou.pdf"

results = json.load(PARITY.open())["results"]
results = sorted(results, key=lambda r: r["iou"], reverse=True)

names = [r["region"].replace("_", " ").title() for r in results]
ious = np.array([r["iou"] for r in results])
acres = np.array([r["ratio_acres"] for r in results])

fig, ax = plt.subplots(figsize=(6.6, 3.4))
y = np.arange(len(results))[::-1]

ax.barh(y, ious, color="#2c5f8d", edgecolor="white", lw=0.4, height=0.7, label="IoU")
ax.scatter(acres, y, s=22, c="#d4801a", edgecolor="white", lw=0.5, zorder=3,
           label="acres ratio")
ax.axvline(1.0, color="0.5", lw=0.7, ls="--", zorder=1)
ax.axvline(np.mean(ious), color="#2c5f8d", lw=0.7, ls=":", alpha=0.7, zorder=1)

ax.set_yticks(y)
ax.set_yticklabels(names, fontsize=8)
ax.set_xlabel("IoU (bar)  /  acres ratio (point)", fontsize=9)
ax.set_xlim(0, 1.15)
ax.tick_params(axis="x", labelsize=8)
ax.grid(axis="x", alpha=0.25, lw=0.4)
ax.legend(loc="lower right", fontsize=8, framealpha=0.95)
ax.set_axisbelow(True)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)

fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print(f"wrote {OUT}")
print(f"mean IoU {ious.mean():.3f}  median {np.median(ious):.3f}  min {ious.min():.3f}")
