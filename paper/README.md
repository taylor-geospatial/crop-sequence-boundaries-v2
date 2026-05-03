# Paper: CSB v2 vs USDA ArcGIS reference

LaTeX source for the ICML 2026 submission documenting the open-source
Crop Sequence Boundaries pipeline in this repo.

## Contents

```
paper/
  csb_icml2026.tex   # main manuscript (preprint mode)
  csb.bib            # BibTeX entries
  Makefile           # latexmk-based build
  README.md          # this file
  sty/               # vendored ICML 2026 author-kit styles
    icml2026.sty
    icml2026.bst
    fancyhdr.sty
    algorithm.sty
    algorithmic.sty
  figures/           # paper figures (.gitkeep placeholder)
```

The styles in `sty/` are taken from the official ICML 2026 author kit
(`https://media.icml.cc/Conferences/ICML2026/Styles/icml2026.zip`).

## Build

```sh
make pdf       # build csb_icml2026.pdf via latexmk
make clean     # drop intermediates
make distclean # also drop the PDF
```

Requires `latexmk` and a TeX Live distribution with `microtype`,
`hyperref`, `cleveref`, `booktabs`, `siunitx`, and `natbib`. The
Makefile sets `TEXINPUTS`/`BSTINPUTS` so the ICML class and
bibliography style are picked up from `sty/` without a system install.

## Submission modes

The manuscript is built in `preprint` mode by default. To switch:

| Mode             | Edit `csb_icml2026.tex` line                         |
|------------------|------------------------------------------------------|
| Anonymous review | `\usepackage{sty/icml2026}`                          |
| Preprint (now)   | `\usepackage[preprint]{sty/icml2026}`                |
| Camera-ready     | `\usepackage[accepted]{sty/icml2026}`                |

## Figures

`figures/hero_conus.pdf` and `figures/pipeline.pdf` are referenced by
the manuscript. Drop the rendered PDFs into `figures/` once the visual
diff renderer has produced them; the build will pick them up
automatically.
