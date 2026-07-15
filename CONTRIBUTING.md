# Contributing

Thanks for considering a contribution to `csb`. This project is an
open-source rewrite of the USDA Crop Sequence Boundaries ArcPy pipeline.
The goal is parity with USDA output, no ArcGIS license required, and a
codebase small enough to audit in an afternoon.

Keep contributions focused. Small PRs land fast. Large PRs stall.

## Quick dev setup

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/isaaccorley/crop-sequence-boundaries-v2
cd crop-sequence-boundaries-v2
make install     # uv sync --all-extras
make check       # pre-commit (ruff format, ruff lint, ty, mdformat, pyproject-fmt)
make test        # pytest --cov=src tests/
```

`make check` runs the full pre-commit gate. Run it before every push.
It includes `ruff format`, `ruff` lint with autofix, `ty` for type
checks, `mdformat` for Markdown, and `pyproject-fmt`.

If `pre-commit` rewraps Markdown or sorts YAML on you, accept it and
re-stage. Don't fight the formatters.

## Repo layout

```
src/csb/             # the package
  cli.py             # Click command group (csb download, polygonize, ...)
  polygonize.py      # tiled raster -> polygon driver
  raster_eliminate.py# connected components + neighbor adjacency merge
  postprocess.py     # boundary join, CSBID/CSBACRES, state split
  download.py        # parallel CDL fetch from NASS
  boundaries.py      # TIGER + NASS county/ASD crosswalk
  parity.py          # USDA ground-truth IoU validation
  pmtiles.py         # GeoParquet -> FlatGeobuf -> tippecanoe
  io.py              # GeoParquet 1.1 writer (full PROJJSON CRS)
  config.py          # constants + CLI flag defaults
  utils.py           # parallel_map, polygonize wrapper

tests/               # pytest suite; small synthetic fixtures only
examples/cluster/    # SLURM scripts for the HPC reference run
```

See `AGENTS.md` for module-level notes if you want more context.

## Code style

- Line length 100 (`ruff` enforced).
- `ruff format` is the formatter. Don't hand-format.
- Type hints required on public functions. `ty` checks them.
- Never `from __future__ import annotations` — Python 3.12+ only.
- Prefer pathlib over `os.path`.
- Logging: `logging.getLogger(__name__)`. No bare `print` in library code.
- Keep modules under ~500 LOC. Split when they grow.
- CRS is fixed to `EPSG:5070` throughout the pipeline. Don't reproject
    silently.
- GeoParquet output uses the full-PROJJSON CRS form. The short
    `{id: {authority, code}}` form breaks pyproj 3.x, geopandas, pyogrio,
    and GDAL readers — don't reintroduce it.

`make check` runs:

```
ruff (lint + format)
ty (type check)
mdformat (markdown)
pyproject-fmt (pyproject.toml)
check-yaml, check-json, trailing-whitespace, ...
uv lock
```

## Test conventions

- `pytest` from `tests/`.
- Small synthetic fixtures. Build a 32x32 numpy array, write it to a
    tmp_path GeoTIFF, run the stage. Don't commit large rasters.
- One test per behavior. Name tests after the behavior, not the
    function (`test_eliminate_drops_small_polygons`, not `test_eliminate_1`).
- Use `tmp_path` for any filesystem output.
- Use `pytest.approx` for float compares.
- `pytest-xdist` is available; mark slow integration tests so they
    can be excluded with `-m "not slow"`.
- Add a regression test when fixing a bug. Name it after the bug
    (`test_regression_eliminate_loses_2ha_polygons`).

Run a single test:

```bash
uv run pytest tests/test_polygonize.py::test_basic -x
```

## Conventional Commits

All commits follow [Conventional Commits](https://www.conventionalcommits.org/).

Allowed types: `feat`, `fix`, `refactor`, `perf`, `chore`, `docs`,
`style`, `test`, `build`, `ci`.

Format:

```
<type>(<optional scope>): <imperative summary, lower case, no period>

<optional body>

<optional footer, e.g. Closes #42>
```

Examples:

```
feat(polygonize): stream tile results through a process pool
fix(eliminate): drop unreachable neighbors before union-find
refactor: split postprocess.py into join + split modules
perf(io): write GeoParquet with row-group size 100k
test(parity): add regression for Iowa 2024 IoU drop
docs: document EPSG:5070 invariant in AGENTS.md
chore: bump ruff to 0.15.5
```

Keep the summary under 72 characters. The body explains *why*, not
*what* — the diff already shows what changed.

## PR checklist

Before opening a PR:

- [ ] Tests added or updated for the change.
- [ ] `make check` passes locally (pre-commit clean).
- [ ] `make test` passes locally.
- [ ] Commit messages follow Conventional Commits.
- [ ] Docs updated when behavior or CLI changed (`README.md`,
    `AGENTS.md`, or relevant module docstring).
- [ ] No large binary fixtures added (>1 MB blocked by pre-commit).
- [ ] Public API changes called out in the PR description.

CI runs the same gate (`make check` + `make test`) on every push.
A red CI is a blocker.

## Filing a bug

There are no issue templates yet. Use this skeleton in a new issue:

```
### Summary
One sentence. What broke.

### Reproduction
Minimal command or snippet. Include input shape (state, year, tile size)
when relevant.

### Expected
What should have happened.

### Actual
What happened. Paste the exact error and traceback.

### Environment
- csb version (`uv run csb --version` or commit SHA)
- Python version
- OS
- relevant deps if you suspect a version skew (rasterio, pyogrio,
  duckdb, shapely)
```

For parity regressions against USDA ground truth, include the IoU
delta and the affected region id.

## Triage expectations

- Bug reports: I aim to acknowledge within a few days. A fix lands when
    there's a regression test and a clear repro.
- Feature requests: open an issue first to discuss scope before
    writing code. Drive-by feature PRs without prior discussion may be
    closed.
- Security issues: email `isaac.corley@taylorgeospatial.org` instead
    of opening a public issue.

## License

By contributing you agree your contributions are licensed under
Apache-2.0, the same license as the project (`LICENSE`).
