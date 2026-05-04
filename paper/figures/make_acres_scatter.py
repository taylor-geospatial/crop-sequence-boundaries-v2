"""Acreage parity scatter (Bland-Altman + 1:1) over 16 CONUS tiles."""
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "data" / "output" / "conus" / "parity_16regions.json"
OUT = Path(__file__).resolve().parent / "acres_scatter.pdf"

results = json.load(PARITY.open())["results"]
ours = np.array([r["ours_acres"] for r in results]) / 1e6
usda = np.array([r["usda_acres"] for r in results]) / 1e6

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 3.2))

lim = max(ours.max(), usda.max()) * 1.05
ax1.plot([0, lim], [0, lim], color="0.6", lw=0.8, ls="--", zorder=1)
ax1.scatter(usda, ours, s=22, c="#1f4e79", edgecolor="white", lw=0.5, zorder=2)
ax1.set_xlabel("USDA acres ($\\times 10^6$)")
ax1.set_ylabel("Ours acres ($\\times 10^6$)")
ax1.set_xlim(0, lim)
ax1.set_ylim(0, lim)
ax1.set_aspect("equal")
ax1.set_title("(a) per-tile acreage", fontsize=10)
ax1.grid(alpha=0.25, lw=0.4)

mean = 0.5 * (ours + usda)
diff_pct = 100.0 * (ours - usda) / usda
md = float(np.mean(diff_pct))
sd = float(np.std(diff_pct, ddof=1))
ax2.axhline(0, color="0.6", lw=0.8, ls="--")
ax2.axhline(md, color="#c0392b", lw=1.0)
ax2.axhline(md + 1.96 * sd, color="#c0392b", lw=0.7, ls=":")
ax2.axhline(md - 1.96 * sd, color="#c0392b", lw=0.7, ls=":")
ax2.scatter(mean, diff_pct, s=22, c="#1f4e79", edgecolor="white", lw=0.5)
ax2.set_xlabel("mean acres ($\\times 10^6$)")
ax2.set_ylabel("(ours $-$ USDA) / USDA  [\\%]")
ax2.set_title(f"(b) Bland--Altman, mean {md:+.1f}\\%, $\\pm$1.96$\\sigma$ = {1.96*sd:.1f}\\%", fontsize=10)
ax2.grid(alpha=0.25, lw=0.4)

fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print(f"wrote {OUT}")
print(f"mean diff: {md:+.2f}%   sd: {sd:.2f}%   median diff: {np.median(diff_pct):+.2f}%")
