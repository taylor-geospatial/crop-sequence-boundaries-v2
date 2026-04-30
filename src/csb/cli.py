"""CLI entrypoint for the CSB pipeline."""

from __future__ import annotations

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
    help="Path to YAML config. Defaults to bundled configs/default.yaml.",
)
@click.pass_context
def main(ctx: click.Context, config: str | None) -> None:
    """CSB — Crop Sequence Boundaries pipeline.

    Generate national crop sequence boundary datasets from USDA CDL rasters
    for any user-specified time range.

    Stages: polygonize -> postprocess (or run-all for the full pipeline).
    """
    ctx.ensure_object(dict)
    cfg_path = config or bundled_config_path()
    ctx.obj["config"] = load_config(cfg_path)


@main.command()
@click.argument("start_year", type=int)
@click.argument("end_year", type=int)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output directory. Defaults to <config.paths.output>/polygonize/<years>/.",
)
@click.option("--area", "-a", default=None, help="Process a single area tile.")
@click.pass_context
def polygonize(
    ctx: click.Context, start_year: int, end_year: int, output: str | None, area: str | None
) -> None:
    """Stage 1: Combine CDL rasters -> polygonize -> eliminate -> simplify."""
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
    help="Path to POLYGONIZE output directory.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output directory. Defaults to <config.paths.output>/postprocess/<years>/.",
)
@click.pass_context
def postprocess(
    ctx: click.Context,
    start_year: int,
    end_year: int,
    polygonize_dir: str,
    output: str | None,
) -> None:
    """Stage 2: Enrich polygons with CDL/boundary attributes + distribute by state."""
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
    help="Output directory. Defaults to <config.paths.national_cdl>.",
)
@click.option(
    "--resolution",
    "-r",
    type=click.Choice(["10", "30"]),
    default="30",
    help="Pixel resolution in meters. 10m only available for 2024+.",
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
        f"[bold]Downloading CDL {start_year}-{end_year} ({resolution}m) to {out} "
        f"with {workers} workers"
    )
    paths = download_cdl(
        years, out, resolution=int(resolution), overwrite=overwrite, workers=workers
    )
    console.print(f"[bold green]Downloaded {len(paths)} CDL rasters")
    for p in paths:
        console.print(f"  {p}")


@main.command(name="build-boundaries")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output path. Defaults to <config.paths.boundaries>.",
)
@click.pass_context
def build_boundaries(ctx: click.Context, output: str | None) -> None:
    """Build ASD+county boundary GeoParquet from Census TIGER + NASS crosswalk."""
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
    """Run the full pipeline: polygonize -> postprocess."""
    from csb.polygonize import run_polygonize
    from csb.postprocess import run_postprocess

    cfg = ctx.obj["config"]
    base = Path(output) if output else Path(cfg["paths"]["output"])
    tag = f"{start_year}_{end_year}"

    console.print(f"[bold]Running full CSB pipeline for {start_year}-{end_year}")

    polygonize_dir = run_polygonize(cfg, start_year, end_year, base / "polygonize" / tag)
    run_postprocess(cfg, start_year, end_year, polygonize_dir, base / "postprocess" / tag)

    console.print(f"[bold green]Pipeline complete. Output: {base}")
