"""Polygonize stage: combine multi-year CDL rasters into crop sequence polygons.

OSS port of USDA's ``CSB-create.py`` (Combine_sa → RasterToPolygon → JoinField
→ CSBElimination → SimplifyPolygon). Two phases per tile:

* Phase 1 (raster-side): combine N years into compact combo IDs, label
  connected components, run threshold-based elimination on the label raster
  (union-find merge into longest-shared-boundary neighbor), drop labels below
  the min-area floor, polygonize once.
* Phase 2 (polygon-side): :func:`shapely.coverage_simplify` (the
  topology-preserving analogue of arcpy ``BEND_SIMPLIFY``), final min-area
  filter, write GeoParquet.
"""

import gc
import logging
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
import rasterio.windows
import shapely
from rasterio.windows import Window
from rich.console import Console

from csb.config import BARREN_CODE, CDL_CROP_MAX
from csb.io import write_geoparquet
from csb.raster_eliminate import (
    dissolve_same_combo,
    eliminate_label_raster,
    label_areas,
    label_raster,
)
from csb.utils import polygonize, worker_count

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
            window = Window(col_off=x_off, row_off=y_off, width=w, height=h)  # ty: ignore[unknown-argument]
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
    # Pack each year's CDL byte (0..254) into one slot of a uint64 (8 yr * 8 b = 64 b).
    if len(years) > 8:
        msg = f"bit-packed combine supports up to 8 years (got {len(years)})"
        raise ValueError(msg)
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
        shifted = arr.astype(np.uint64) << np.uint64(8 * i)
        if seq_ids is None:
            seq_ids = shifted
        else:
            np.bitwise_or(seq_ids, shifted, out=seq_ids)

    assert seq_ids is not None
    shape = seq_ids.shape
    unique_seqs, flat_ids = np.unique(seq_ids.ravel(), return_inverse=True)
    del seq_ids
    combo_raster = flat_ids.astype(np.int32, copy=False).reshape(shape)
    del flat_ids

    n_combos = len(unique_seqs)
    n_years = len(years)
    cdl_per_combo_year = np.zeros((n_combos, n_years), dtype=np.uint8)
    count0 = np.zeros(n_combos, dtype=np.int16)
    count_barren = np.zeros(n_combos, dtype=np.int16)
    for i in range(n_years):
        yr_vals = ((unique_seqs >> np.uint64(8 * i)) & np.uint64(0xFF)).astype(np.uint8)
        cdl_per_combo_year[:, i] = yr_vals
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

    # Keep filter: effective_count (cropland years - barren years) >= min_cropland_years.
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

    # Per-label combo + effective_count via first-pixel sample (post-eliminate
    # labels are dominated by their seed combo since slivers were < min_area).
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

    # Dissolve adjacent labels sharing the same combo (sliver absorption can
    # leave two former-adjacent regions touching with identical CDL sequence).
    pre_n = n_lbl
    lbl, n_lbl, combo_per_label = dissolve_same_combo(lbl, n_lbl, combo_per_label)
    if n_lbl < pre_n:
        logger.info("%s: same-combo dissolve %s → %s labels", area, pre_n, n_lbl)
        eff_per_label = effective_per_combo[combo_per_label].astype(np.int16, copy=False)
    gc.collect()

    # Drop labels below min_area before polygonizing.
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
    # CDL{year} per polygon: emit 0 for non-cropland years (the original
    # CDL class was overwritten with BARREN at combine time).
    for i, year in enumerate(years):
        cdl_arr = cdl_per_combo_year[combo_arr, i].astype(np.int32)
        cdl_arr = np.where(cdl_arr == BARREN_CODE, 0, cdl_arr)
        out_cols[f"CDL{year}"] = pa.array(cdl_arr, type=pa.int32())

    out_table = pa.table(out_cols)

    out_path = intermediate_dir / f"{area}.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Plain parquet here; GeoParquet metadata is attached in phase 2.
    pq.write_table(out_table, out_path, compression="zstd")

    elapsed = time.perf_counter() - t0
    logger.info("%s: Phase 1 done — %s polygons in %.1fs", area, out_table.num_rows, elapsed)
    return f"Phase1 {area} ({out_table.num_rows} polygons, {elapsed:.0f}s)"


def _phase2_simplify(args: tuple[str, dict[str, Any]]) -> str:
    """Phase 2: coverage_simplify + min-area filter, write final GeoParquet."""
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

    geoms = shapely.from_wkb(np.asarray(table["geometry"]))

    logger.info("%s: coverage_simplify %s polygons (tol=%sm)", area, len(geoms), simplify_tol)
    geoms_simp = shapely.coverage_simplify(geoms, tolerance=simplify_tol, simplify_boundary=True)

    areas = shapely.area(geoms_simp)
    keep = areas >= min_area_keep
    if not keep.any():
        return f"Skipped {area} (all below min_area after simplify)"

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


def run_polygonize(
    cfg: dict[str, Any],
    start_year: int,
    end_year: int,
    output_dir: str | Path,
    area: str | None = None,
) -> Path:
    """Run polygonize for all (or one) window tile(s).

    Two phase pools share a streaming queue: phase 2 starts on each tile as
    soon as phase 1 emits its intermediate, so the two stages overlap.
    """
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
    phase2_workers = pcfg.get("phase2_workers") or pcfg.get("max_workers") or default_workers

    first_cdl = national_cdl / str(start_year) / f"{start_year}_30m_cdls.tif"
    with rasterio.open(first_cdl) as src:
        raster_width, raster_height = src.width, src.height

    all_tiles = _tile_windows(raster_width, raster_height, tile_size)
    if area:
        all_tiles = [(name, win) for name, win in all_tiles if name == area]

    done = {f.stem for f in output_dir.glob("*.parquet")}
    phase1_done = {f.stem for f in intermediate_dir.glob("*.parquet")}

    phase1_remaining = [
        (name, win) for name, win in all_tiles if name not in done and name not in phase1_done
    ]
    phase2_pending = [
        (name, win) for name, win in all_tiles if name in phase1_done and name not in done
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

    with (
        ProcessPoolExecutor(max_workers=phase1_workers) as p1_pool,
        ProcessPoolExecutor(max_workers=phase2_workers) as p2_pool,
    ):
        p2_futures: dict = {
            p2_pool.submit(_phase2_simplify, (name, p2_params)): name for name, _w in phase2_pending
        }
        p1_futures: dict = {
            p1_pool.submit(_phase1_polygonize, _p1_args(name, w)): name
            for name, w in phase1_remaining
        }
        for fut in as_completed(p1_futures):
            name = p1_futures[fut]
            try:
                msg = fut.result()
            except Exception:
                logger.exception("%s: phase1 failed", name)
                continue
            if msg.startswith("Phase1"):
                p1_completed += 1
                p2_futures[p2_pool.submit(_phase2_simplify, (name, p2_params))] = name
            else:
                p1_skipped += 1
                console.print(f"  {msg}")
        for fut in as_completed(p2_futures):
            name = p2_futures[fut]
            try:
                msg = fut.result()
            except Exception:
                logger.exception("%s: phase2 failed", name)
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
