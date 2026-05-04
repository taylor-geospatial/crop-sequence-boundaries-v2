"""Command-line entrypoint for the ``csb`` package."""

from __future__ import annotations

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
@_polygonize_options
def polygonize(
    start_year: int,
    end_year: int,
    output: str | None,
    area: str | None,
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
        roads_mask=roads_mask,
        same_combo_dissolve=same_combo_dissolve,
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
