"""Controlled elimination benchmark: raster-side vs polygon-side engines.

On one Iowa tile, at a range of tile sizes, this times raster-side elimination
(:func:`csb.raster_eliminate.eliminate_label_raster`) against the polygon-side
baseline on two engines — DuckDB (:mod:`csb.polygon_eliminate`) and SedonaDB
(:mod:`csb.sedona_eliminate`) — on the identical labeled combine raster.

Each (implementation, size, repeat) runs in a fresh subprocess so peak memory
(``ru_maxrss``) is isolated and a crash in one engine cannot take down the run.
For every run it records wall time, peak RSS, and a correctness signature
(survivor count + total area) so the engines can be compared on speed, memory,
and result equivalence. Output is machine-readable JSON — the paper table and
the engine choice are made from the file, never from logs.

The polygon-side timing includes the polygonization it requires as input: that
is the point of the comparison — the raster method eliminates on cheap integer
labels *before* one polygonization, while the polygon method must polygonize the
un-eliminated raster first and then merge geometry.
"""

import json
import logging
import multiprocessing as mp
import platform
import resource
import time
from pathlib import Path

import numpy as np

from csb.config import DEFAULT_ELIMINATE_THRESHOLDS, DEFAULT_NATIONAL_CDL_DIR

logger = logging.getLogger(__name__)

IMPLEMENTATIONS = ("raster", "duckdb", "sedona")


def _run_one(impl: str, lbl: np.ndarray, n_labels: int, thresholds: list[float], transform: object):  # noqa: ANN202
    """Run one elimination and return (n_survivors, total_area_m2)."""
    if impl == "raster":
        from csb.raster_eliminate import eliminate_label_raster, label_areas

        out_lbl, out_n = eliminate_label_raster(lbl.copy(), n_labels, thresholds)
        return out_n, float(label_areas(out_lbl, out_n)[1:].sum())

    if impl == "duckdb":
        from csb.polygon_eliminate import eliminate_polygons_duckdb

        table = eliminate_polygons_duckdb(lbl.copy(), n_labels, thresholds, transform)
        return table.num_rows, float(np.asarray(table["area"]).sum())

    if impl == "sedona":
        from csb.sedona_eliminate import eliminate_polygons_sedona

        table = eliminate_polygons_sedona(lbl.copy(), n_labels, thresholds, transform)
        return table.num_rows, float(np.asarray(table["area"]).sum())

    msg = f"unknown implementation {impl!r}"
    raise ValueError(msg)


def _child(impl, lbl, n_labels, thresholds, transform, q) -> None:  # noqa: ANN001
    """Subprocess entry: time one run, report (elapsed, peak_rss_kb, n, area)."""
    try:
        t0 = time.perf_counter()
        n_surv, area = _run_one(impl, lbl, n_labels, thresholds, transform)
        elapsed = time.perf_counter() - t0
        peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        q.put({"status": "ok", "elapsed": elapsed, "peak_rss_kb": peak_kb,
               "n_survivors": n_surv, "area_m2": area})
    except (MemoryError, OverflowError, ValueError, RuntimeError, ImportError) as exc:
        q.put({"status": "crash", "error_type": type(exc).__name__, "error": str(exc)[:300]})


def _quartiles(xs: list[float]) -> dict[str, float]:
    a = np.asarray(xs, dtype=np.float64)
    return {
        "median": float(np.median(a)),
        "q1": float(np.percentile(a, 25)),
        "q3": float(np.percentile(a, 75)),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def _bench_impl(
    ctx: "mp.context.SpawnContext",
    impl: str,
    lbl: np.ndarray,
    n_labels: int,
    thresholds: list[float],
    transform: object,
    repeats: int,
) -> dict:
    """Run one implementation ``repeats`` times in fresh subprocesses."""
    runs: list[dict] = []
    for _ in range(repeats):
        q: mp.Queue = ctx.Queue()
        p = ctx.Process(target=_child, args=(impl, lbl, n_labels, thresholds, transform, q))
        p.start()
        p.join()
        if p.exitcode != 0 and q.empty():
            runs.append({"status": "crash", "error_type": "ProcessDied",
                         "error": f"exitcode={p.exitcode}"})
        else:
            runs.append(q.get())

    ok = [r for r in runs if r["status"] == "ok"]
    if not ok:
        return {"status": "crash", "repeats": repeats, "sample_error": runs[0]}
    return {
        "status": "ok",
        "repeats": repeats,
        "n_ok": len(ok),
        "time_s": _quartiles([r["elapsed"] for r in ok]),
        "peak_rss_mb": _quartiles([r["peak_rss_kb"] / 1024 for r in ok]),
        "n_survivors": ok[0]["n_survivors"],
        "area_m2": ok[0]["area_m2"],
    }


def bench_tile(
    *,
    start_year: int,
    end_year: int,
    col_off: int,
    row_off: int,
    sizes: list[int],
    repeats: int,
    implementations: tuple[str, ...] = IMPLEMENTATIONS,
    national_cdl_dir: str | Path = DEFAULT_NATIONAL_CDL_DIR,
    min_cropland_years: int = 2,
    thresholds: tuple[float, ...] = DEFAULT_ELIMINATE_THRESHOLDS,
    output: str | Path | None = None,
) -> dict:
    """Benchmark elimination implementations across tile sizes on one tile."""
    from rasterio.windows import Window

    from csb.polygonize import _combine_years_windowed
    from csb.raster_eliminate import label_raster

    ctx = mp.get_context("spawn")
    national_cdl = Path(national_cdl_dir)
    years = list(range(start_year, end_year + 1))
    thr = list(thresholds)

    results: list[dict] = []
    payload = {
        "experiment": "eliminate_bench",
        "start_year": start_year,
        "end_year": end_year,
        "col_off": col_off,
        "row_off": row_off,
        "repeats": repeats,
        "thresholds": thr,
        "min_cropland_years": min_cropland_years,
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "results": results,
    }

    def _flush() -> None:
        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(json.dumps(payload, indent=2))

    for size in sizes:
        window = Window(col_off=col_off, row_off=row_off, width=size, height=size)  # ty: ignore[unknown-argument]
        combo, eff_per_combo, _cdl, transform = _combine_years_windowed(
            national_cdl, years, window
        )
        mask = eff_per_combo[combo] >= min_cropland_years
        lbl, n_lbl = label_raster(combo, mask)
        logger.info("size=%d: %d labels", size, n_lbl)

        per_impl = {
            impl: _bench_impl(ctx, impl, lbl, n_lbl, thr, transform, repeats)
            for impl in implementations
        }
        results.append({"size": size, "n_labels": int(n_lbl), "implementations": per_impl})
        del lbl, combo, mask
        # Write after every size so a walltime kill keeps completed sizes.
        _flush()
        logger.info("flushed results through size=%d", size)

    return payload
