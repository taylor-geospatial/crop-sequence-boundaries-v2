"""Per-class within-mask agreement bar chart, top 15 CDL classes by USDA acreage."""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent / "per_class.pdf"

# Same numbers as Tab. tab:per-class. (Code, label, USDA Mac, within-mask agree)
rows = [
    (1,  "Corn",                 88.39, 0.896, "commodity"),
    (5,  "Soybeans",             82.14, 0.901, "commodity"),
    (24, "Winter Wheat",         23.05, 0.895, "commodity"),
    (2,  "Cotton",               11.12, 0.869, "commodity"),
    (23, "Spring Wheat",         10.91, 0.861, "commodity"),
    (61, "Fallow / Idle",        10.58, 0.855, "fallow"),
    (36, "Alfalfa",              10.18, 0.841, "forage"),
    (4,  "Sorghum",               6.21, 0.836, "commodity"),
    (26, "Dbl Crop W.Wht/Soy",    4.02, 0.828, "double"),
    (3,  "Rice",                  2.87, 0.787, "specialty"),
    (31, "Canola",                2.62, 0.828, "commodity"),
    (21, "Barley",                1.83, 0.754, "commodity"),
    (75, "Almonds",               1.70, 0.860, "specialty"),
    (37, "Other Hay",             1.70, 0.796, "forage"),
    (10, "Peanuts",               1.64, 0.596, "specialty"),
]

palette = {
    "commodity": "#2c5f8d",
    "specialty": "#d4801a",
    "forage":    "#7b9a4a",
    "fallow":    "#888888",
    "double":    "#a06aa1",
}

rows = sorted(rows, key=lambda r: r[3])

labels = [f"{c}: {n}" for (c, n, _, _, _) in rows]
agree = np.array([r[3] for r in rows])
fams  = [r[4] for r in rows]
mac   = np.array([r[2] for r in rows])

fig, ax = plt.subplots(figsize=(6.6, 3.6))
y = np.arange(len(rows))
colors = [palette[f] for f in fams]
ax.barh(y, agree, color=colors, edgecolor="white", lw=0.4, height=0.7)
ax.axvline(0.846, color="0.4", lw=0.7, ls="--", zorder=1)  # mean IoU baseline

for yi, a, m in zip(y, agree, mac):
    ax.text(a + 0.005, yi, f"{m:.1f} Mac", va="center", fontsize=7, color="0.3")

ax.set_yticks(y)
ax.set_yticklabels(labels, fontsize=8)
ax.set_xlim(0.5, 1.0)
ax.set_xlabel("Within-mask agreement with USDA CSB1825 (2024)", fontsize=9)
ax.tick_params(axis="x", labelsize=8)
ax.grid(axis="x", alpha=0.25, lw=0.4)
ax.set_axisbelow(True)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)

handles = [plt.Rectangle((0, 0), 1, 1, fc=palette[k]) for k in
           ("commodity", "specialty", "forage", "fallow", "double")]
ax.legend(handles,
          ["commodity", "specialty", "forage", "fallow", "double-crop"],
          loc="lower right", fontsize=7.5, framealpha=0.95, handlelength=1.0)

fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight")
print(f"wrote {OUT}")
