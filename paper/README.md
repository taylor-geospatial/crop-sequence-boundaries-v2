# Paper: CSB v2 vs USDA ArcGIS reference

LaTeX source for the *Computers and Electronics in Agriculture* (Elsevier)
submission documenting the open-source Crop Sequence Boundaries pipeline
in this repo.

## Contents

```
paper/
  csb_cea.tex          # main manuscript (elsarticle preprint mode)
  csb.bib              # BibTeX entries
  Makefile             # latexmk-based build
  README.md            # this file
  elsarticle.cls       # vendored Elsevier document class
  elsarticle-num.bst   # vendored numeric bibliography style
  figures/             # paper figures + matplotlib generators
```

`elsarticle.cls` / `elsarticle-num.bst` come from the Elsevier author kit
([assets.ctfassets.net](https://assets.ctfassets.net/o78em1y1w4i4/4MpsJHO0MOJ2xZuwGTAbOZ/7bc64af36477c5d6cfce335a1f872363/elsarticle.zip)).
A pristine copy of the upstream zip is unpacked under `elsevier/`
(gitignored) for reference.

## Build

```sh
make pdf       # build csb_cea.pdf via latexmk
make clean     # drop intermediates
make distclean # also drop the PDF
```

Requires `latexmk` and a TeX Live distribution with `microtype`,
`hyperref`, `cleveref`, `booktabs`, `siunitx`, `natbib`, `algorithm`,
and `algpseudocode`.

## Submission modes

The manuscript is built in `preprint` mode by default (single column,
12 pt, generous margins — the format reviewers prefer for markup).
To estimate the published two-column production length, edit
`csb_cea.tex` line 3:

| Mode             | Document class options                  |
| ---------------- | --------------------------------------- |
| Review (now)     | `[preprint,12pt]`                       |
| Two-column 3p    | `[final,3p,times,twocolumn]`            |
| Two-column 5p    | `[final,5p,times,twocolumn]`            |

## Figures

Generators live under `figures/make_*.py`. Run them from the repo root
to (re)build the corresponding PDFs:

```sh
uv run python paper/figures/make_acres_scatter.py
uv run python paper/figures/make_per_tile_iou.py
uv run python paper/figures/make_per_class.py
uv run python paper/figures/make_stage_breakdown.py
uv run python paper/figures/make_bottom3_montage.py
```

`hero_conus.pdf` and `pipeline.pdf` are produced separately.
