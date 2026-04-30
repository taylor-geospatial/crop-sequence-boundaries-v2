"""Stage 1: POLYGONIZE — Combine multi-year CDL rasters into crop sequence polygons.

Open-source port of USDA CSB `CSB-create.py` (arcpy.gp.Combine_sa →
RasterToPolygon → JoinField → CSBElimination → SimplifyPolygon).

Per window tile, two phases:

Phase 1 (memory-heavy, raster-side):
1. Windowed-read multi-year CDL rasters from national files.
2. Encode each pixel's N-year CDL sequence as a packed int64; assign compact
   combo IDs.
3. Connected-components label the masked combo raster (one label per
   connected polygon region).
4. Compute polygon areas analytically (pixel count × pixel_area).
5. Run multi-pass elimination on the LABEL RASTER: count adjacent-pixel-edge
   pairs as shared boundary length, merge small labels into the non-small
   neighbor with the longest shared boundary, union-find resolve transitive
   merges, remap labels.
6. Drop labels below `min_polygon_area` (so we don't simplify slivers that get
   discarded anyway).
7. Polygonize ONCE -> intermediate GeoParquet of pre-simplify polygons.

Phase 2 (CPU-bound, polygon-side):
8. shapely.coverage_simplify (preserves shared boundaries between neighbors —
   the OSS analogue of arcpy `cartography.SimplifyPolygon(BEND_SIMPLIFY)`).
9. min_area filter, write final GeoParquet.

No SedonaDB cross-join, no per-pair ST_Intersection(ST_Boundary, ST_Boundary).
"""

import gc
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import rasterio.windows
import shapely
from rasterio.windows import Window

from csb.config import BARREN_CODE, CDL_CROP_MAX, CDL_PIXEL_AREA_SQM
from csb.io import write_geoparquet
from csb.raster_eliminate import (
    dissolve_same_combo,
    eliminate_label_raster,
    label_areas,
    label_raster,
)
from csb.utils import polygonize

logger = logging.getLogger(__name__)

DEFAULT_TILE_SIZE = 5000


def _tile_windows(width: int, height: int, tile_size: int) -> list[tuple[str, Window]]:
    """Generate named tile windows covering the full raster extent."""
    tiles = []
    for row_idx, y_off in enumerate(range(0, height, tile_size)):
        h = min(tile_size, height - y_off)
        if row_idx < 26:
            row_label = chr(65 + row_idx)
        else:
            row_label = chr(65 + row_idx // 26 - 1) + chr(65 + row_idx % 26)
        for col_idx, x_off in enumerate(range(0, width, tile_size)):
            w = min(tile_size, width - x_off)
            name = f"{row_label}{col_idx}"
            window = Window(x_off, y_off, w, h)  # type: ignore[call-arg]
            tiles.append((name, window))
    return tiles


def _combine_years_windowed(
    national_cdl: Path, years: list[int], window: Window
) -> tuple[np.ndarray, np.ndarray, np.ndarray, Any]:
    """Read window from each year's CDL, pack sequences.

    Mirrors arcpy.gp.Combine_sa: groups pixels by their full N-year CDL
    sequence. Each unique sequence gets a compact integer ID.

    Returns:
        combo_raster: HxW int32 of compact combo IDs (0..n_combos-1).
        effective_per_combo: 1D int16, COUNT0-COUNT_BARREN per combo.
        cdl_per_combo_year: 2D uint8, shape (n_combos, n_years), CDL value per
            (combo, year) — non-cropland already remapped to BARREN_CODE.
        transform: rasterio Affine for this window.
    """
    # Memory budget: only seq_ids is materialized as int64 (one HxW buffer).
    # Per-year CDL reads stay uint8 → cast to int64 only inline during the
    # multiply-add. This avoids an 8× transient copy at peak.
    base = np.int64(300)
    seq_ids: np.ndarray | None = None
    transform = None
    barren = np.uint8(BARREN_CODE)
    for i, year in enumerate(years):
        cdl_path = national_cdl / str(year) / f"{year}_30m_cdls.tif"
        with rasterio.open(cdl_path) as src:
            arr = src.read(1, window=window, out_dtype=np.uint8)
            if transform is None:
                transform = rasterio.windows.transform(window, src.transform)
        # Remap non-cropland in place (uint8 stays uint8).
        non_crop = (arr > CDL_CROP_MAX) & (arr != 0)
        if non_crop.any():
            arr = arr.copy()
            arr[non_crop] = barren
        if seq_ids is None:
            seq_ids = arr.astype(np.int64)
        else:
            np.add(seq_ids, arr.astype(np.int64) * (base**i), out=seq_ids)

    assert seq_ids is not None
    shape = seq_ids.shape
    unique_seqs, flat_ids = np.unique(seq_ids.ravel(), return_inverse=True)
    del seq_ids
    # return_inverse is platform intp (int64 on 64-bit). Drop to int32 since
    # n_combos < 2^31 always for any tile we'd run.
    combo_raster = flat_ids.astype(np.int32, copy=False).reshape(shape)
    del flat_ids

    n_combos = len(unique_seqs)
    n_years = len(years)
    cdl_per_combo_year = np.zeros((n_combos, n_years), dtype=np.uint8)
    count0 = np.zeros(n_combos, dtype=np.int16)
    count_barren = np.zeros(n_combos, dtype=np.int16)
    for i in range(n_years):
        yr_vals = (unique_seqs // int(base**i)) % int(base)
        cdl_per_combo_year[:, i] = yr_vals.astype(np.uint8)
        count0 += (yr_vals > 0).astype(np.int16)
        count_barren += (yr_vals == BARREN_CODE).astype(np.int16)
    effective_per_combo = (count0 - count_barren).astype(np.int16)
    return combo_raster, effective_per_combo, cdl_per_combo_year, transform


# ---------------------------------------------------------------------------
# Phase 1: combine + label + eliminate + polygonize (memory-heavy)
# ---------------------------------------------------------------------------


def _phase1_polygonize(args: tuple[str, dict[str, Any]]) -> str:
    """Phase 1: read CDL, combine, label-eliminate, polygonize -> intermediate parquet."""
    area, params = args
    cfg = params["config"]
    start_year: int = params["start_year"]
    end_year: int = params["end_year"]
    intermediate_dir = Path(params["intermediate_dir"])
    window_dict = params["window"]
    window = Window(**window_dict)

    national_cdl = Path(cfg["paths"]["national_cdl"])
    min_cropland = cfg["global"]["min_cropland_years"]
    pcfg = cfg["polygonize"]
    thresholds = pcfg["eliminate_thresholds"]
    min_area_keep = pcfg["min_polygon_area"]

    years = list(range(start_year, end_year + 1))
    t0 = time.perf_counter()

    logger.info("%s: Phase 1 — read+combine %s years", area, len(years))
    combo_raster, effective_per_combo, cdl_per_combo_year, transform = _combine_years_windowed(
        national_cdl, years, window
    )

    # USDA-strict keep filter: effective_count >= min_cropland_years.
    effective_map = effective_per_combo[combo_raster]
    mask = effective_map >= min_cropland
    if not mask.any():
        return f"Skipped {area} (no valid pixels)"

    logger.info("%s: connected-components label", area)
    lbl, n_lbl = label_raster(combo_raster, mask)
    if n_lbl == 0:
        return f"Skipped {area} (no labels)"

    logger.info("%s: eliminate (%s passes, thresholds=%s)", area, len(thresholds), thresholds)
    lbl, n_lbl = eliminate_label_raster(lbl, n_lbl, thresholds)
    if n_lbl == 0:
        return f"Skipped {area} (all eliminated)"

    # Recompute combo_per_label against the post-eliminate label space
    # (eliminate compacts label IDs so the pre-eliminate map is stale).
    flat_lbl = lbl.ravel()
    flat_combo = combo_raster.ravel()
    order = np.argsort(flat_lbl, kind="stable")
    sorted_lbl = flat_lbl[order]
    first = np.r_[True, sorted_lbl[1:] != sorted_lbl[:-1]]
    first_idx = order[first]
    first_lbl = flat_lbl[first_idx]
    combo_per_label = np.zeros(n_lbl + 1, dtype=np.int32)
    combo_per_label[first_lbl] = flat_combo[first_idx]
    eff_per_label = np.zeros(n_lbl + 1, dtype=np.int16)
    eff_per_label[first_lbl] = effective_per_combo[flat_combo[first_idx]]
    del order, sorted_lbl, first_idx, first_lbl, flat_combo, flat_lbl

    # Same-combo dissolve: merge adjacent labels sharing the same combo. After
    # elimination, slivers absorbed into a large polygon often leave that
    # polygon touching another large polygon with identical CDL sequence; this
    # collapses them into one. Major contributor to USDA polygon-count parity.
    pre_n = n_lbl
    lbl, n_lbl, combo_per_label = dissolve_same_combo(lbl, n_lbl, combo_per_label)
    if n_lbl < pre_n:
        logger.info("%s: same-combo dissolve %s → %s labels", area, pre_n, n_lbl)
        # eff_per_label needs to follow; cheap to rebuild from combo_per_label.
        eff_per_label = effective_per_combo[combo_per_label].astype(np.int16, copy=False)
    gc.collect()

    # Drop labels below min_area BEFORE polygonizing — saves ~2-3x simplify
    # work later. Pixels in dropped labels become background; they were already
    # carried through eliminate so they'll have been merged into a neighbor if
    # one was eligible.
    areas_per_label = label_areas(lbl, n_lbl)
    keep_lbl = areas_per_label >= min_area_keep
    keep_lbl[0] = False
    if not keep_lbl.any():
        return f"Skipped {area} (all below min_area)"
    drop_remap = np.where(keep_lbl, np.arange(n_lbl + 1, dtype=np.int32), np.int32(0))
    lbl = drop_remap[lbl].astype(np.int32)
    del drop_remap
    gc.collect()

    out_mask = lbl > 0
    logger.info("%s: polygonize once", area)
    table = polygonize(lbl, mask=out_mask, transform=transform, nodata=0)
    if table.num_rows == 0:
        return f"Skipped {area} (no polygons after polygonize)"

    # Map polygon "value" (label id) -> effective_count and CDL{year} via lookup.
    label_ids = np.asarray(table["value"]).astype(np.int64)
    max_lbl = int(label_ids.max())
    if max_lbl >= len(eff_per_label):
        pad = np.zeros(max_lbl + 1 - len(eff_per_label), dtype=np.int16)
        eff_per_label = np.concatenate([eff_per_label, pad])
        combo_per_label = np.concatenate(
            [combo_per_label, np.zeros(max_lbl + 1 - len(combo_per_label), dtype=np.int32)]
        )
    eff_arr = eff_per_label[label_ids].astype(np.int32)
    combo_arr = combo_per_label[label_ids].astype(np.int32)

    out_cols = {
        "geometry": table["geometry"],
        "effective_count": pa.array(eff_arr, type=pa.int32()),
    }
    # Pre-compute CDL{year} per polygon. Replace BARREN sentinel with 0 (USDA
    # convention: no-cropland encodes back to original CDL class would be
    # nice but lossy; BARREN→0 keeps schema int16+ compatible and signals
    # "non-cropland year" for downstream filters).
    for i, year in enumerate(years):
        cdl_arr = cdl_per_combo_year[combo_arr, i].astype(np.int32)
        # BARREN_CODE marks non-cropland; downstream USDA-style schema uses
        # the original CDL classes here (they represent forest/water/etc).
        # Since we overwrote those at combine-time, emit 0 as "non-cropland"
        # — preserves type but the original CDL class is no longer available.
        cdl_arr = np.where(cdl_arr == BARREN_CODE, 0, cdl_arr)
        out_cols[f"CDL{year}"] = pa.array(cdl_arr, type=pa.int32())

    out_table = pa.table(out_cols)

    out_path = intermediate_dir / f"{area}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Write plain parquet (geoparquet metadata added in phase 2).
    pq.write_table(out_table, out_path, compression="zstd")

    elapsed = time.perf_counter() - t0
    logger.info(
        "%s: Phase 1 done — %s polygons in %.1fs", area, out_table.num_rows, elapsed
    )
    return f"Phase1 {area} ({out_table.num_rows} polygons, {elapsed:.0f}s)"


# ---------------------------------------------------------------------------
# Phase 2: coverage_simplify + min_area filter (CPU-bound)
# ---------------------------------------------------------------------------


def _phase2_simplify(args: tuple[str, dict[str, Any]]) -> str:
    """Phase 2: coverage_simplify pre-simplify polygons -> final GeoParquet."""
    area, params = args
    cfg = params["config"]
    intermediate_dir = Path(params["intermediate_dir"])
    output_dir = Path(params["output_dir"])

    pcfg = cfg["polygonize"]
    simplify_tol = pcfg["simplify_tolerance"]
    min_area_keep = pcfg["min_polygon_area"]

    intermediate_path = intermediate_dir / f"{area}.parquet"
    t0 = time.perf_counter()

    table = pq.read_table(intermediate_path)
    if table.num_rows == 0:
        return f"Skipped {area} (empty intermediate)"

    # shapely 2.x bulk from_wkb takes numpy arrays directly (no .tolist() copy).
    geoms = shapely.from_wkb(np.asarray(table["geometry"]))

    logger.info("%s: coverage_simplify %s polygons (tol=%sm)", area, len(geoms), simplify_tol)
    geoms_simp = shapely.coverage_simplify(
        geoms, tolerance=simplify_tol, simplify_boundary=True
    )

    areas = shapely.area(geoms_simp)
    keep = areas >= min_area_keep
    if not keep.any():
        return f"Skipped {area} (all below min_area after simplify)"

    # Boolean indexing on numpy object array — no Python list comp.
    kept_geoms = geoms_simp[keep]
    kept_areas = areas[keep]
    keep_arrow = pa.array(keep)

    out_cols: dict[str, Any] = {
        "geometry": pa.array(shapely.to_wkb(kept_geoms), type=pa.binary()),
    }
    for name in table.schema.names:
        if name == "geometry":
            continue
        out_cols[name] = table.column(name).filter(keep_arrow)
    out_cols["area_sqm"] = pa.array(kept_areas, type=pa.float64())

    out_table = pa.table(out_cols)

    out_path = output_dir / f"{area}.parquet"
    write_geoparquet(out_table, out_path)

    elapsed = time.perf_counter() - t0
    logger.info("%s: Phase 2 done — %s polygons in %.1fs", area, out_table.num_rows, elapsed)
    return f"Finished {area} ({out_table.num_rows} polygons, {elapsed:.0f}s)"


# ---------------------------------------------------------------------------
# Single-tile entry point (for tests / --area debugging)
# ---------------------------------------------------------------------------


def process_tile(args: tuple[str, dict[str, Any]]) -> str:
    """Run both phases on a single tile, no intermediate parquet on disk."""
    area, params = args
    intermediate_dir = Path(params.get("intermediate_dir") or params["output_dir"]) / "_tmp"
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    p1_params = {**params, "intermediate_dir": str(intermediate_dir)}
    r1 = _phase1_polygonize((area, p1_params))
    if r1.startswith("Skipped"):
        return r1
    p2_params = {**params, "intermediate_dir": str(intermediate_dir)}
    return _phase2_simplify((area, p2_params))


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_polygonize(
    cfg: dict[str, Any],
    start_year: int,
    end_year: int,
    output_dir: str | Path,
    area: str | None = None,
) -> Path:
    """Run POLYGONIZE for all (or one) window tile(s).

    Two-phase: Phase 1 (raster-side, memory-heavy, fewer workers) writes
    intermediate parquets; Phase 2 (polygon-side, CPU-bound, more workers)
    simplifies into final GeoParquet. Both phases stream concurrently — Phase
    2 starts on tiles as soon as Phase 1 finishes them, removing the global
    barrier the legacy two-stage driver had.
    """
    import shutil
    from concurrent.futures import ProcessPoolExecutor, as_completed

    from rich.console import Console

    from csb.utils import worker_count

    console = Console()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    intermediate_dir = output_dir / "_intermediate"
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    national_cdl = Path(cfg["paths"]["national_cdl"])
    pcfg = cfg.get("polygonize", {})
    tile_size = pcfg.get("tile_size", DEFAULT_TILE_SIZE)

    default_workers = worker_count(cfg["global"]["cpu_fraction"])
    phase1_workers = pcfg.get("phase1_workers", max(1, default_workers // 4))
    phase2_workers = (
        pcfg.get("phase2_workers") or pcfg.get("max_workers") or default_workers
    )

    first_cdl = national_cdl / str(start_year) / f"{start_year}_30m_cdls.tif"
    with rasterio.open(first_cdl) as src:
        raster_width, raster_height = src.width, src.height

    all_tiles = _tile_windows(raster_width, raster_height, tile_size)
    if area:
        all_tiles = [(name, win) for name, win in all_tiles if name == area]

    done = {f.stem for f in output_dir.glob("*.parquet")}
    phase1_done = {f.stem for f in intermediate_dir.glob("*.parquet")}

    phase1_remaining = [
        (name, win) for name, win in all_tiles
        if name not in done and name not in phase1_done
    ]
    phase2_pending = [
        (name, win) for name, win in all_tiles
        if name in phase1_done and name not in done
    ]

    console.print(
        f"POLYGONIZE: {len(all_tiles)} tiles, {start_year}-{end_year}\n"
        f"  Phase 1 (raster-side): {len(phase1_remaining)} remaining, "
        f"{phase1_workers} workers\n"
        f"  Phase 2 (simplify):    {len(phase2_pending)} pending + new, "
        f"{phase2_workers} workers\n"
        f"  Already done:          {len(done)}"
    )

    if not phase1_remaining and not phase2_pending:
        console.print("[green]All tiles already processed.")
        return output_dir

    p1_params = {
        "config": cfg,
        "start_year": start_year,
        "end_year": end_year,
        "intermediate_dir": str(intermediate_dir),
    }
    p2_params = {
        "config": cfg,
        "intermediate_dir": str(intermediate_dir),
        "output_dir": str(output_dir),
    }

    def _p1_args(name: str, w: Window) -> tuple[str, dict[str, Any]]:
        return (
            name,
            {
                **p1_params,
                "window": {
                    "col_off": w.col_off,
                    "row_off": w.row_off,
                    "width": w.width,
                    "height": w.height,
                },
            },
        )

    p1_completed = 0
    p2_completed = 0
    p2_skipped = 0
    p1_skipped = 0

    # Streaming pipeline: P2 worker pool eats tiles as P1 finishes them.
    with (
        ProcessPoolExecutor(max_workers=phase1_workers) as p1_pool,
        ProcessPoolExecutor(max_workers=phase2_workers) as p2_pool,
    ):
        # Submit P2 tasks for tiles that already have intermediates from prior runs.
        p2_futures: dict = {}
        for name, _w in phase2_pending:
            p2_futures[p2_pool.submit(_phase2_simplify, (name, p2_params))] = name

        # Submit all P1 tasks.
        p1_futures: dict = {
            p1_pool.submit(_phase1_polygonize, _p1_args(name, w)): name
            for name, w in phase1_remaining
        }

        # As P1 finishes each tile, submit it to P2 immediately.
        for fut in as_completed(p1_futures):
            name = p1_futures[fut]
            try:
                msg = fut.result()
            except Exception as e:
                logger.exception("%s: phase1 failed: %s", name, e)
                continue
            if msg.startswith("Phase1"):
                p1_completed += 1
                p2_futures[p2_pool.submit(_phase2_simplify, (name, p2_params))] = name
            else:
                p1_skipped += 1
                console.print(f"  {msg}")

        # Drain P2.
        for fut in as_completed(p2_futures):
            name = p2_futures[fut]
            try:
                msg = fut.result()
            except Exception as e:
                logger.exception("%s: phase2 failed: %s", name, e)
                continue
            if msg.startswith("Finished"):
                p2_completed += 1
            else:
                p2_skipped += 1
                console.print(f"  {msg}")

    console.print(
        f"[bold green]POLYGONIZE complete: P1 {p1_completed} done, "
        f"{p1_skipped} skipped; P2 {p2_completed} done, {p2_skipped} skipped"
    )

    shutil.rmtree(intermediate_dir, ignore_errors=True)
    return output_dir
