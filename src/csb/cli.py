"""Command-line entrypoint for the ``csb`` package."""

import json
from pathlib import Path

import click
from rich.console import Console

from csb.config import (
    DEFAULT_BOUNDARIES_PATH,
    DEFAULT_CPU_FRACTION,
    DEFAULT_ELIMINATE_THRESHOLDS,
    DEFAULT_MIN_CROPLAND_YEARS,
    DEFAULT_MIN_POLYGON_AREA,
    DEFAULT_NATIONAL_CDL_DIR,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SIMPLIFY_TOLERANCE,
    DEFAULT_TILE_SIZE,
)

console = Console()

_DEFAULT_THRESHOLDS_STR = ",".join(str(int(t)) for t in DEFAULT_ELIMINATE_THRESHOLDS)


def _parse_thresholds(
    _ctx: click.Context, _param: click.Parameter, value: str
) -> tuple[float, ...]:
    """Click callback: parse a comma-separated list of floats."""
    if not value:
        return DEFAULT_ELIMINATE_THRESHOLDS
    try:
        return tuple(float(x.strip()) for x in value.split(","))
    except ValueError as e:
        msg = f"--eliminate-thresholds must be a comma-separated list of numbers, got {value!r}"
        raise click.BadParameter(msg) from e


@click.group()
def main() -> None:
    """CSB — open-source Crop Sequence Boundaries pipeline."""


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@main.command()
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=DEFAULT_NATIONAL_CDL_DIR,
    show_default=True,
    help="Output directory for CDL TIFs.",
)
@click.option(
    "--resolution",
    "-r",
    type=click.Choice(["10", "30"]),
    default="30",
    show_default=True,
    help="Pixel resolution in meters. 10m only for 2024+.",
)
@click.option("--overwrite", is_flag=True, help="Re-download existing files.")
@click.option(
    "--workers",
    "-w",
    type=int,
    default=4,
    show_default=True,
    help="Concurrent download workers.",
)
def download(
    start_year: int, end_year: int, output: str, resolution: str, overwrite: bool, workers: int
) -> None:
    """Download USDA CDL rasters for the given year range."""
    from csb.download import download_cdl

    out = Path(output)
    years = list(range(start_year, end_year + 1))
    console.print(
        f"[bold]Downloading CDL {start_year}-{end_year} ({resolution}m) "
        f"to {out} with {workers} workers"
    )
    paths = download_cdl(
        years, out, resolution=int(resolution), overwrite=overwrite, workers=workers
    )
    console.print(f"[bold green]Downloaded {len(paths)} CDL rasters")


@main.command(name="roads-prep")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    required=True,
    help="Output GeoParquet path for the buffered road/rail polygons.",
)
@click.option(
    "--release",
    default=None,
    help="Overture release tag (default: pinned recent release in csb.roads).",
)
@click.option(
    "--buffer-m",
    type=float,
    default=15.0,
    show_default=True,
    help="Buffer (metres) applied to road/rail centerlines before rasterization.",
)
@click.option("--threads", type=int, default=16, show_default=True)
def roads_prep(output: str, release: str | None, buffer_m: float, threads: int) -> None:
    """Download CONUS road + rail centerlines from Overture into an indexed parquet."""
    from csb.roads import DEFAULT_OVERTURE_RELEASE, fetch_overture_roads

    fetch_overture_roads(
        Path(output),
        release=release or DEFAULT_OVERTURE_RELEASE,
        buffer_m=buffer_m,
        threads=threads,
    )


@main.command(name="build-boundaries")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=DEFAULT_BOUNDARIES_PATH,
    show_default=True,
    help="Output GeoParquet path.",
)
def build_boundaries(output: str) -> None:
    """Build the ASD+county boundary GeoParquet from Census TIGER + NASS."""
    from csb.boundaries import build_boundaries as _build

    _build(Path(output))


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _polygonize_options(f):  # noqa: ANN001, ANN202 — Click decorator factory
    """Shared --option flags for polygonize / run-all (so both stay in sync)."""
    flags = [
        click.option(
            "--national-cdl-dir",
            type=click.Path(exists=True, file_okay=False),
            default=DEFAULT_NATIONAL_CDL_DIR,
            show_default=True,
            help="Directory containing per-year CDL TIFs.",
        ),
        click.option(
            "--tile-size",
            type=int,
            default=DEFAULT_TILE_SIZE,
            show_default=True,
            help="Side length (px) of each processing tile.",
        ),
        click.option(
            "--min-cropland-years",
            type=int,
            default=DEFAULT_MIN_CROPLAND_YEARS,
            show_default=True,
            help="Minimum number of cropland years to keep a pixel.",
        ),
        click.option(
            "--eliminate-thresholds",
            default=_DEFAULT_THRESHOLDS_STR,
            show_default=True,
            callback=_parse_thresholds,
            help="Comma-separated area thresholds (m²) for the eliminate passes.",
        ),
        click.option(
            "--min-polygon-area",
            type=float,
            default=DEFAULT_MIN_POLYGON_AREA,
            show_default=True,
            help="Drop polygons smaller than this (m²).",
        ),
        click.option(
            "--simplify-tolerance",
            type=float,
            default=DEFAULT_SIMPLIFY_TOLERANCE,
            show_default=True,
            help="coverage_simplify tolerance in meters.",
        ),
        click.option(
            "--cpu-fraction",
            type=float,
            default=DEFAULT_CPU_FRACTION,
            show_default=True,
            help="Fraction of CPUs to use for the worker pool.",
        ),
        click.option(
            "--phase1-workers",
            type=int,
            default=None,
            help="Phase-1 (raster-side) workers. Defaults to ~1/4 of cpu_fraction *cpu_count.",
        ),
        click.option(
            "--phase2-workers",
            type=int,
            default=None,
            help="Phase-2 (simplify) workers. Defaults to cpu_fraction *cpu_count.",
        ),
        click.option(
            "--roads-mask",
            type=click.Path(exists=True, dir_okay=False),
            default=None,
            help="Optional GeoParquet from `csb roads-prep`. When set, road/rail "
            "buffers are excluded from the cropland mask before connected-components "
            "labeling so adjacent fields don't merge across roads.",
        ),
        click.option(
            "--same-combo-dissolve/--no-same-combo-dissolve",
            "same_combo_dissolve",
            default=True,
            help="Toggle the same-combo dissolve pass (default on; off for ablation).",
        ),
        click.option(
            "--usda-retention/--no-usda-retention",
            "usda_retention",
            default=False,
            help="Use USDA's polygon-level retention (effective >= 2 OR area >= "
            "min-polygon-area with effective >= 1) instead of the pixel-level "
            "min-cropland-years mask.",
        ),
        click.option(
            "--exclude-low-noncrop/--no-exclude-low-noncrop",
            "exclude_low_noncrop",
            default=False,
            help="Treat CDL 61-65 (fallow/idle, pasture, forest, shrub, barren) "
            "as non-crop, matching USDA's GEE category grouping.",
        ),
        click.option(
            "--usda-noise-filter",
            "usda_noise_px",
            type=int,
            default=0,
            show_default=True,
            help="Erase same-value components of <= N px per CDL year before "
            "combine (USDA's production RegionGroup/Con/Shrink filter; USDA "
            "uses 2). 0 disables. See docs/usda_smoothing_reference.md.",
        ),
        click.option(
            "--focal-radius",
            type=int,
            default=0,
            show_default=True,
            help="Focal-mode noise filter radius (px) applied per CDL year "
            "before combine; 0 disables. Emulates USDA's GEE noise filtering.",
        ),
    ]
    for opt in reversed(flags):
        f = opt(f)
    return f


@main.command()
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help=f"Output dir (default: {DEFAULT_OUTPUT_DIR}/polygonize/<years>/).",
)
@click.option("--area", "-a", default=None, help="Process a single tile (debug).")
@click.option(
    "--num-shards",
    type=int,
    default=1,
    show_default=True,
    help="Split tiles into this many shards for multi-node SLURM arrays.",
)
@click.option(
    "--shard-index",
    type=int,
    default=0,
    show_default=True,
    help="0-based index of the shard to process (0..num-shards-1).",
)
@_polygonize_options
def polygonize(
    start_year: int,
    end_year: int,
    output: str | None,
    area: str | None,
    num_shards: int,
    shard_index: int,
    national_cdl_dir: str,
    tile_size: int,
    min_cropland_years: int,
    eliminate_thresholds: tuple[float, ...],
    min_polygon_area: float,
    simplify_tolerance: float,
    cpu_fraction: float,
    phase1_workers: int | None,
    phase2_workers: int | None,
    roads_mask: str | None,
    same_combo_dissolve: bool,
    usda_retention: bool,
    exclude_low_noncrop: bool,
    usda_noise_px: int,
    focal_radius: int,
) -> None:
    """Combine multi-year CDL → label-eliminate → simplify → GeoParquet."""
    from csb.polygonize import run_polygonize

    out = (
        Path(output)
        if output
        else Path(DEFAULT_OUTPUT_DIR) / "polygonize" / f"{start_year}_{end_year}"
    )
    run_polygonize(
        start_year=start_year,
        end_year=end_year,
        output_dir=out,
        national_cdl_dir=national_cdl_dir,
        tile_size=tile_size,
        min_cropland_years=min_cropland_years,
        eliminate_thresholds=eliminate_thresholds,
        min_polygon_area=min_polygon_area,
        simplify_tolerance=simplify_tolerance,
        cpu_fraction=cpu_fraction,
        phase1_workers=phase1_workers,
        phase2_workers=phase2_workers,
        area=area,
        num_shards=num_shards,
        shard_index=shard_index,
        roads_mask=roads_mask,
        same_combo_dissolve=same_combo_dissolve,
        usda_retention=usda_retention,
        exclude_low_noncrop=exclude_low_noncrop,
        usda_noise_px=usda_noise_px,
        focal_radius=focal_radius,
    )


@main.command()
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--polygonize-dir",
    type=click.Path(exists=True),
    required=True,
    help="Directory containing polygonize stage output.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help=f"Output dir (default: {DEFAULT_OUTPUT_DIR}/postprocess/<years>/).",
)
@click.option(
    "--boundaries",
    type=click.Path(),
    default=DEFAULT_BOUNDARIES_PATH,
    show_default=True,
    help="ASD+county boundary GeoParquet (from `csb build-boundaries`).",
)
@click.option(
    "--cpu-fraction",
    type=float,
    default=DEFAULT_CPU_FRACTION,
    show_default=True,
    help="Fraction of CPUs to use for the worker pool.",
)
def postprocess(
    start_year: int,
    end_year: int,
    polygonize_dir: str,
    output: str | None,
    boundaries: str,
    cpu_fraction: float,
) -> None:
    """Enrich polygons with county/ASD attributes and split by state."""
    from csb.postprocess import run_postprocess

    out = (
        Path(output)
        if output
        else Path(DEFAULT_OUTPUT_DIR) / "postprocess" / f"{start_year}_{end_year}"
    )
    run_postprocess(
        start_year=start_year,
        end_year=end_year,
        polygonize_dir=Path(polygonize_dir),
        output_dir=out,
        boundaries_path=Path(boundaries),
        cpu_fraction=cpu_fraction,
    )


@main.command(name="run-all")
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=DEFAULT_OUTPUT_DIR,
    show_default=True,
    help="Root output directory.",
)
@click.option(
    "--boundaries",
    type=click.Path(),
    default=DEFAULT_BOUNDARIES_PATH,
    show_default=True,
    help="ASD+county boundary GeoParquet.",
)
@_polygonize_options
def run_all(
    start_year: int,
    end_year: int,
    output: str,
    boundaries: str,
    national_cdl_dir: str,
    tile_size: int,
    min_cropland_years: int,
    eliminate_thresholds: tuple[float, ...],
    min_polygon_area: float,
    simplify_tolerance: float,
    cpu_fraction: float,
    phase1_workers: int | None,
    phase2_workers: int | None,
    roads_mask: str | None,
    same_combo_dissolve: bool,
    usda_retention: bool,
    exclude_low_noncrop: bool,
    usda_noise_px: int,
    focal_radius: int,
) -> None:
    """Run polygonize + postprocess back-to-back."""
    from csb.polygonize import run_polygonize
    from csb.postprocess import run_postprocess

    base = Path(output)
    tag = f"{start_year}_{end_year}"
    console.print(f"[bold]Running full CSB pipeline for {start_year}-{end_year}")
    polygonize_dir = run_polygonize(
        start_year=start_year,
        end_year=end_year,
        output_dir=base / "polygonize" / tag,
        national_cdl_dir=national_cdl_dir,
        tile_size=tile_size,
        min_cropland_years=min_cropland_years,
        eliminate_thresholds=eliminate_thresholds,
        min_polygon_area=min_polygon_area,
        simplify_tolerance=simplify_tolerance,
        cpu_fraction=cpu_fraction,
        phase1_workers=phase1_workers,
        phase2_workers=phase2_workers,
        roads_mask=roads_mask,
        same_combo_dissolve=same_combo_dissolve,
        usda_retention=usda_retention,
        exclude_low_noncrop=exclude_low_noncrop,
        usda_noise_px=usda_noise_px,
        focal_radius=focal_radius,
    )
    run_postprocess(
        start_year=start_year,
        end_year=end_year,
        polygonize_dir=polygonize_dir,
        output_dir=base / "postprocess" / tag,
        boundaries_path=Path(boundaries),
        cpu_fraction=cpu_fraction,
    )
    console.print(f"[bold green]Pipeline complete. Output: {base}")


# ---------------------------------------------------------------------------
# Validation + publishing
# ---------------------------------------------------------------------------


@main.command(name="parity-prep")
@click.option(
    "--ours",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Our CONUS national GeoParquet (from postprocess).",
)
@click.option(
    "--ours-out",
    type=click.Path(dir_okay=False),
    required=True,
    help="Where to write the indexed ours parquet.",
)
@click.option(
    "--usda-gdb", type=click.Path(exists=True), required=True, help="USDA CSB FileGDB ground truth."
)
@click.option(
    "--usda-out",
    type=click.Path(dir_okay=False),
    required=True,
    help="Where to write the indexed USDA parquet.",
)
@click.option("--threads", type=int, default=32, show_default=True)
def parity_prep(ours: str, ours_out: str, usda_gdb: str, usda_out: str, threads: int) -> None:
    """Hilbert-sort + add bbox columns to enable DuckDB row-group pruning."""
    from csb.parity import prep_inputs

    prep_inputs(Path(ours), Path(ours_out), Path(usda_gdb), Path(usda_out), threads=threads)


@main.command()
@click.option(
    "--ours",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Indexed ours parquet (from `csb parity-prep`).",
)
@click.option(
    "--usda",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Indexed USDA parquet (from `csb parity-prep`).",
)
@click.option(
    "--report",
    type=click.Path(dir_okay=False),
    default=None,
    help="JSON output path for the per-region report.",
)
@click.option("--threads", type=int, default=16, show_default=True)
@click.option(
    "--whole-conus",
    is_flag=True,
    help="Skip the 16-region sample and compute IoU over the full CONUS extent.",
)
@click.option(
    "--per-class",
    type=int,
    default=None,
    help="Also produce an area-weighted CDL confusion matrix for the given year "
    "(e.g. --per-class 2024).",
)
def parity(
    ours: str,
    usda: str,
    report: str | None,
    threads: int,
    whole_conus: bool,
    per_class: int | None,
) -> None:
    """Compare our CSB output against USDA ground truth."""
    from csb.parity import (
        DEFAULT_REGIONS,
        per_class_confusion,
        run_parity,
        run_parity_whole_conus,
        summarize,
    )

    if whole_conus:
        result = run_parity_whole_conus(Path(ours), Path(usda), threads=threads)
        console.print(json.dumps(result, indent=2))
        confusion: list[dict] = []
        if per_class is not None:
            console.print(f"\n[bold]CDL{per_class} confusion (area-weighted, top 25):")
            confusion = per_class_confusion(Path(ours), Path(usda), year=per_class, threads=threads)
            for r in sorted(confusion, key=lambda r: -r["area_sqm"])[:25]:
                console.print(
                    f"  ours={r['ours_class']:>3}  usda={r['usda_class']:>3}  "
                    f"{r['area_sqm'] / 1e6:>10.1f} km²"
                )
        if report:
            Path(report).parent.mkdir(parents=True, exist_ok=True)
            with Path(report).open("w") as f:
                json.dump({"conus": result, "per_class": confusion}, f, indent=2)
            console.print(f"Report: {report}")
        return

    results = run_parity(
        Path(ours),
        Path(usda),
        DEFAULT_REGIONS,
        threads=threads,
        report_path=Path(report) if report else None,
    )
    console.print(
        f"{'region':<22}{'n_ours':>10}{'n_usda':>10}{'ratio_p':>9}{'ratio_a':>9}{'IoU':>8}"
    )
    console.print("-" * 68)
    for r in results:
        if r.get("iou") is None:
            console.print(f"{r['region']:<22}  (skipped/empty)")
            continue
        console.print(
            f"{r['region']:<22}{r['n_ours']:>10,}{r['n_usda']:>10,}"
            f"{r['ratio_polys']:>9.2f}{r['ratio_acres']:>9.2f}{r['iou']:>8.3f}"
        )
    summary = summarize(results)
    if summary.get("n", 0) > 0:
        console.print(
            f"\n[bold]IoU: mean={summary['iou_mean']:.3f} "
            f"median={summary['iou_median']:.3f} "
            f"min={summary['iou_min']:.3f} max={summary['iou_max']:.3f} (n={summary['n']})"
        )
    if report:
        console.print(f"Report: {report}")
        console.print(json.dumps(summary, indent=2))


@main.command()
@click.option(
    "--ours",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Indexed ours parquet (from `csb parity-prep`).",
)
@click.option(
    "--usda",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Indexed USDA parquet (from `csb parity-prep`).",
)
@click.option(
    "--region",
    type=str,
    default=None,
    help="Named region from csb.parity.DEFAULT_REGIONS (e.g. iowa_corn_belt). "
    "If omitted, --bbox is required.",
)
@click.option(
    "--bbox",
    type=str,
    default=None,
    help="EPSG:5070 bbox 'minx,miny,maxx,maxy'. Overrides --region.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    required=True,
    help="Output PNG path.",
)
@click.option("--title", type=str, default="")
@click.option("--dpi", type=int, default=200, show_default=True)
def visualize(
    ours: str,
    usda: str,
    region: str | None,
    bbox: str | None,
    output: str,
    title: str,
    dpi: int,
) -> None:
    """Render a 4-panel comparison (ours / USDA / intersection / sym-diff)."""
    from csb.parity import DEFAULT_REGIONS, find_bbox_5070
    from csb.visualize import render_comparison

    if bbox:
        parts = [float(x.strip()) for x in bbox.split(",")]
        if len(parts) != 4:
            msg = f"--bbox must have 4 comma-separated floats, got {bbox!r}"
            raise click.BadParameter(msg)
        bbox_5070 = (parts[0], parts[1], parts[2], parts[3])
        title = title or f"bbox {bbox}"
    elif region:
        match = next((r for r in DEFAULT_REGIONS if r[0] == region), None)
        if match is None:
            msg = f"unknown region {region!r}; see csb.parity.DEFAULT_REGIONS"
            raise click.BadParameter(msg)
        _name, tx, ty, what = match
        bbox_5070 = find_bbox_5070(tx, ty)
        title = title or f"{region} — {what}"
    else:
        msg = "must specify either --region or --bbox"
        raise click.UsageError(msg)

    render_comparison(Path(ours), Path(usda), bbox_5070, Path(output), title=title, dpi=dpi)
    console.print(f"[bold green]wrote {output}")


@main.command()
@click.option(
    "--input",
    "-i",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="National CSB GeoParquet (from postprocess).",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    required=True,
    help="Where to write the .pmtiles archive.",
)
@click.option(
    "--workdir",
    type=click.Path(file_okay=False),
    default=None,
    help="Working dir for the FGB intermediate (default: alongside output).",
)
@click.option("--keep-fgb", is_flag=True, help="Keep the FlatGeobuf intermediate.")
@click.option("--minimum-zoom", type=int, default=4, show_default=True)
@click.option("--maximum-zoom", type=int, default=12, show_default=True)
@click.option(
    "--tippecanoe", default="tippecanoe", show_default=True, help="Path to tippecanoe binary."
)
def pmtiles(
    input: str,
    output: str,
    workdir: str | None,
    keep_fgb: bool,
    minimum_zoom: int,
    maximum_zoom: int,
    tippecanoe: str,
) -> None:
    """Build a CONUS PMTiles archive from a CSB GeoParquet output."""
    from csb.pmtiles import build_pmtiles

    build_pmtiles(
        Path(input),
        Path(output),
        workdir=Path(workdir) if workdir else None,
        keep_fgb=keep_fgb,
        minimum_zoom=minimum_zoom,
        maximum_zoom=maximum_zoom,
        tippecanoe=tippecanoe,
    )
    console.print(f"[bold green]pmtiles: {output}")


@main.command(name="bench-eliminate")
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option("--col-off", type=int, required=True, help="Window column offset into national CDL.")
@click.option("--row-off", type=int, required=True, help="Window row offset into national CDL.")
@click.option(
    "--sizes",
    default="1000,2500,5000",
    show_default=True,
    help="Comma-separated square tile sizes (px) to benchmark.",
)
@click.option("--repeats", type=int, default=5, show_default=True, help="Timed runs per size.")
@click.option(
    "--implementations",
    default="raster,duckdb,sedona",
    show_default=True,
    help="Comma-separated subset of raster,duckdb,sedona to benchmark.",
)
@click.option(
    "--national-cdl-dir",
    type=click.Path(exists=True, file_okay=False),
    default=DEFAULT_NATIONAL_CDL_DIR,
    show_default=True,
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    required=True,
    help="Machine-readable JSON results path.",
)
def bench_eliminate(
    start_year: int,
    end_year: int,
    col_off: int,
    row_off: int,
    sizes: str,
    repeats: int,
    implementations: str,
    national_cdl_dir: str,
    output: str,
) -> None:
    """Time raster-side vs polygon-side (DuckDB, SedonaDB) elimination on one tile."""
    from csb.bench import bench_tile

    size_list = [int(s.strip()) for s in sizes.split(",") if s.strip()]
    impls = tuple(s.strip() for s in implementations.split(",") if s.strip())
    payload = bench_tile(
        start_year=start_year,
        end_year=end_year,
        col_off=col_off,
        row_off=row_off,
        sizes=size_list,
        repeats=repeats,
        implementations=impls,
        national_cdl_dir=national_cdl_dir,
        output=output,
    )
    for r in payload["results"]:
        console.print(f"[bold]{r['size']}px ({r['n_labels']} labels):")
        for impl in impls:
            d = r["implementations"].get(impl, {})
            if d.get("status") == "ok":
                t, m = d["time_s"]["median"], d["peak_rss_mb"]["median"]
                console.print(
                    f"  {impl:>7}: {t:8.2f}s  {m:8.0f} MB  "
                    f"{d['n_survivors']} polys  {d['area_m2'] / 1e6:.1f} km²"
                )
            else:
                err = d.get("sample_error", {}).get("error_type", "?")
                console.print(f"  {impl:>7}: [red]{err}")
    console.print(f"[bold green]bench-eliminate: {output}")


@main.command(name="object-eval")
@click.option(
    "--ours",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Our prepped GeoParquet (bbox columns; from `csb parity-prep`).",
)
@click.option(
    "--usda",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="USDA prepped GeoParquet (bbox columns; from `csb parity-prep`).",
)
@click.option(
    "--region",
    default=None,
    help="Single region name from csb.parity.DEFAULT_REGIONS (default: all 16).",
)
@click.option("--threads", type=int, default=32, show_default=True)
@click.option(
    "--output", "-o", type=click.Path(dir_okay=False), required=True, help="Results JSON."
)
def object_eval(ours: str, usda: str, region: str | None, threads: int, output: str) -> None:
    """Directional matched-polygon IoU vs USDA CSB1825, per region (§5.3)."""
    import json

    from csb.object_eval import matched_polygon_iou, summarize_matched
    from csb.parity import DEFAULT_REGIONS, _connect, find_bbox_5070

    regions = (
        [r for r in DEFAULT_REGIONS if r[0] == region] if region else list(DEFAULT_REGIONS)
    )
    if not regions:
        msg = f"unknown region {region!r}; see csb.parity.DEFAULT_REGIONS"
        raise click.BadParameter(msg)

    conn = _connect(threads)
    out = []
    for name, tx, ty, _what in regions:
        bbox = find_bbox_5070(tx, ty)
        res = matched_polygon_iou(conn, ours, usda, bbox)
        summ = summarize_matched(res)
        summ["region"] = name
        summ["bbox_5070"] = list(bbox)
        out.append(summ)
        med = summ.get("median_iou")
        med_s = f"{med:.3f}" if med is not None else "—"
        console.print(
            f"  {name:<20} n_usda={summ['n_usda']:>7} matched={summ['n_matched']:>7} "
            f"median_iou={med_s}"
        )
    conn.close()

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(out, indent=2))
    console.print(f"[bold green]object-eval: {output}")


@main.command(name="instance-metrics")
@click.option(
    "--ours",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Our prepped GeoParquet (bbox columns; from `csb parity-prep`).",
)
@click.option(
    "--usda",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="USDA prepped GeoParquet (bbox columns; from `csb parity-prep`).",
)
@click.option(
    "--region",
    default=None,
    help="Single region name from csb.parity.DEFAULT_REGIONS (default: all 16).",
)
@click.option("--threads", type=int, default=16, show_default=True)
@click.option(
    "--output", "-o", type=click.Path(dir_okay=False), required=True, help="Results JSON."
)
def instance_metrics_cmd(
    ours: str, usda: str, region: str | None, threads: int, output: str
) -> None:
    """Symmetric polygon-instance metrics vs USDA: PQ/SQ/RQ, F1@t, chamfer."""
    import json

    from csb.instance_metrics import instance_metrics, load_tile_geoms
    from csb.parity import DEFAULT_REGIONS, find_bbox_5070

    regions = (
        [r for r in DEFAULT_REGIONS if r[0] == region] if region else list(DEFAULT_REGIONS)
    )
    if not regions:
        msg = f"unknown region {region!r}; see csb.parity.DEFAULT_REGIONS"
        raise click.BadParameter(msg)

    out = []
    for name, tx, ty, _what in regions:
        bbox = find_bbox_5070(tx, ty)
        gt = load_tile_geoms(usda, bbox, threads=threads)
        pred = load_tile_geoms(ours, bbox, threads=threads)
        rec = instance_metrics(gt, pred)
        rec["region"] = name
        rec["bbox_5070"] = list(bbox)
        out.append(rec)
        if "error" in rec:
            console.print(f"  {name:<20} {rec['error']}")
        else:
            console.print(
                f"  {name:<20} PQ={rec['pq']:.3f} SQ={rec['sq']:.3f} RQ={rec['rq']:.3f} "
                f"F1@.5:.95={rec['f1_mean_50_95']:.3f} "
                f"chamfer={rec['boundary_error_m_mean']:.1f}m"
                if rec.get("boundary_error_m_mean") is not None
                else f"  {name:<20} PQ={rec['pq']:.3f} (no matched pairs)"
            )
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(out, indent=2))
    console.print(f"[bold green]instance-metrics: {output}")


@main.command(name="tile-sweep")
@click.option("--region", required=True, help="Region name from csb.parity.DEFAULT_REGIONS or 'all'.")
@click.option(
    "--usda-indexed",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Prepped USDA parquet (from prep_usda / parity-prep).",
)
@click.option("--cropland-years", default="1,2", show_default=True, help="min_cropland_years values.")
@click.option("--simplify", default="30,60", show_default=True, help="simplify_tolerance (m) values.")
@click.option("--min-area", default="10000", show_default=True, help="min_polygon_area (m²) values.")
@click.option(
    "--dissolve",
    default="true",
    show_default=True,
    help="same_combo_dissolve values: 'true', 'false', or 'true,false'.",
)
@click.option(
    "--roads-mask",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Optional roads mask; when set, sweeps both with and without it.",
)
@click.option("--threads", type=int, default=16, show_default=True)
@click.option("--output", "-o", type=click.Path(dir_okay=False), required=True)
def tile_sweep(
    region: str,
    usda_indexed: str,
    cropland_years: str,
    simplify: str,
    min_area: str,
    dissolve: str,
    roads_mask: str | None,
    threads: int,
    output: str,
) -> None:
    """Sweep parity-driving parameters on one tile (or all) vs USDA CSB1825."""
    import json
    from itertools import product

    from csb.parity import DEFAULT_REGIONS
    from csb.tile_experiment import run_tile_experiment

    regions = (
        list(DEFAULT_REGIONS)
        if region == "all"
        else [r for r in DEFAULT_REGIONS if r[0] == region]
    )
    if not regions:
        msg = f"unknown region {region!r}"
        raise click.BadParameter(msg)

    cy = [int(x) for x in cropland_years.split(",") if x.strip()]
    st = [float(x) for x in simplify.split(",") if x.strip()]
    ma = [float(x) for x in min_area.split(",") if x.strip()]
    dis = [x.strip().lower() == "true" for x in dissolve.split(",") if x.strip()]
    road_opts: list[str | None] = [None, roads_mask] if roads_mask else [None]

    results = []
    for (name, tx, ty, _what), c, s, m, d, road in product(regions, cy, st, ma, dis, road_opts):
        params: dict = {
            "min_cropland_years": c,
            "simplify_tolerance": s,
            "min_polygon_area": m,
            "same_combo_dissolve": d,
            "roads_mask": road,
        }
        console.print(f"[cyan]{name} cy={c} simp={s} mmu={m} dissolve={d} roads={bool(road)}")
        rec = run_tile_experiment(
            region_name=name, target_x=tx, target_y=ty, params=params,
            usda_indexed=Path(usda_indexed), threads=threads,
        )
        rec["params"]["roads_mask"] = bool(road)  # store flag, not path
        results.append(rec)
        iou = rec.get("iou")
        console.print(
            f"    -> IoU={iou:.3f} " if iou is not None else "    -> (no output) "
        )
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(results, indent=2))
    console.print(f"[bold green]tile-sweep: {output}")
