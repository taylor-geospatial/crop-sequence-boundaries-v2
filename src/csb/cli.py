"""Command-line entrypoint for the ``csb`` package."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console

from csb.config import bundled_config_path, load_config

console = Console()


@click.group()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True),
    default=None,
    help="Path to YAML config (defaults to the bundled configs/default.yaml).",
)
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """CSB — open-source Crop Sequence Boundaries pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config or bundled_config_path())


# ---------------------------------------------------------------------------
# Stage commands
# ---------------------------------------------------------------------------


@main.command()
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output dir (default: <config.paths.output>/polygonize/<years>/).",
)
@click.option("--area", "-a", default=None, help="Process a single tile (debug).")
@click.pass_context
def polygonize(
    ctx: click.Context, start_year: int, end_year: int, output: str | None, area: str | None
) -> None:
    """Combine multi-year CDL → label-eliminate → simplify → GeoParquet."""
    from csb.polygonize import run_polygonize

    cfg = ctx.obj["config"]
    out = (
        Path(output)
        if output
        else Path(cfg["paths"]["output"]) / "polygonize" / f"{start_year}_{end_year}"
    )
    run_polygonize(cfg, start_year, end_year, out, area=area)


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
    help="Output dir (default: <config.paths.output>/postprocess/<years>/).",
)
@click.pass_context
def postprocess(
    ctx: click.Context,
    start_year: int,
    end_year: int,
    polygonize_dir: str,
    output: str | None,
) -> None:
    """Enrich polygons with county/ASD attributes and split by state."""
    from csb.postprocess import run_postprocess

    cfg = ctx.obj["config"]
    out = (
        Path(output)
        if output
        else Path(cfg["paths"]["output"]) / "postprocess" / f"{start_year}_{end_year}"
    )
    run_postprocess(cfg, start_year, end_year, polygonize_dir, out)


@main.command()
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output dir (default: <config.paths.national_cdl>).",
)
@click.option(
    "--resolution",
    "-r",
    type=click.Choice(["10", "30"]),
    default="30",
    help="Pixel resolution in meters. 10m only for 2024+.",
)
@click.option("--overwrite", is_flag=True, help="Re-download existing files.")
@click.option("--workers", "-w", type=int, default=4, help="Concurrent download workers.")
@click.pass_context
def download(
    ctx: click.Context,
    start_year: int,
    end_year: int,
    output: str | None,
    resolution: str,
    overwrite: bool,
    workers: int,
) -> None:
    """Download USDA CDL rasters for the given year range."""
    from csb.download import download_cdl

    cfg = ctx.obj["config"]
    out = Path(output) if output else Path(cfg["paths"]["national_cdl"])
    years = list(range(start_year, end_year + 1))
    console.print(
        f"[bold]Downloading CDL {start_year}-{end_year} ({resolution}m) "
        f"to {out} with {workers} workers"
    )
    paths = download_cdl(
        years, out, resolution=int(resolution), overwrite=overwrite, workers=workers
    )
    console.print(f"[bold green]Downloaded {len(paths)} CDL rasters")


@main.command(name="build-boundaries")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output path (default: <config.paths.boundaries>).",
)
@click.pass_context
def build_boundaries(ctx: click.Context, output: str | None) -> None:
    """Build the ASD+county boundary GeoParquet from Census TIGER + NASS."""
    from csb.boundaries import build_boundaries as _build

    cfg = ctx.obj["config"]
    out = Path(output) if output else Path(cfg["paths"]["boundaries"])
    _build(out)


@main.command(name="run-all")
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option("--output", "-o", type=click.Path(), default=None, help="Root output directory.")
@click.pass_context
def run_all(ctx: click.Context, start_year: int, end_year: int, output: str | None) -> None:
    """Run polygonize + postprocess back-to-back."""
    from csb.polygonize import run_polygonize
    from csb.postprocess import run_postprocess

    cfg = ctx.obj["config"]
    base = Path(output) if output else Path(cfg["paths"]["output"])
    tag = f"{start_year}_{end_year}"
    console.print(f"[bold]Running full CSB pipeline for {start_year}-{end_year}")
    polygonize_dir = run_polygonize(cfg, start_year, end_year, base / "polygonize" / tag)
    run_postprocess(cfg, start_year, end_year, polygonize_dir, base / "postprocess" / tag)
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
@click.option("--threads", type=int, default=32)
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
@click.option("--threads", type=int, default=16)
def parity(ours: str, usda: str, report: str | None, threads: int) -> None:
    """Compare our CSB output against USDA ground truth across 16 regions."""
    from csb.parity import DEFAULT_REGIONS, run_parity, summarize

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
@click.option("--minimum-zoom", type=int, default=4)
@click.option("--maximum-zoom", type=int, default=12)
@click.option("--tippecanoe", default="tippecanoe", help="Path to tippecanoe binary.")
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
