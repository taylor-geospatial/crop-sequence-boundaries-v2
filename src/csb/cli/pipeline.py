"""Pipeline-stage commands: download, roads-prep, build-boundaries, polygonize, postprocess, run-all."""

from pathlib import Path

import click

from csb.cli._group import console, main
from csb.cli.options import _polygonize_options
from csb.config import (
    DEFAULT_BOUNDARIES_PATH,
    DEFAULT_CPU_FRACTION,
    DEFAULT_NATIONAL_CDL_DIR,
    DEFAULT_OUTPUT_DIR,
)


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
    help="Output GeoParquet path for the road/rail lines.",
)
@click.option(
    "--source",
    type=click.Choice(["tiger", "overture"]),
    default="tiger",
    show_default=True,
    help="Roads source. TIGER matches USDA's method (configuration of record).",
)
@click.option(
    "--tiger-year",
    type=int,
    default=None,
    help="TIGER vintage (default: pinned year in csb.tiger). Ignored for overture.",
)
@click.option(
    "--cache-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Download cache for TIGER zips (default: data/tiger). Ignored for overture.",
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
    help="Buffer (metres) applied to Overture centerlines. TIGER lines stay unbuffered.",
)
@click.option("--threads", type=int, default=16, show_default=True)
def roads_prep(
    output: str,
    source: str,
    tiger_year: int | None,
    cache_dir: str | None,
    release: str | None,
    buffer_m: float,
    threads: int,
) -> None:
    """Download CONUS road + rail centerlines into an indexed `--roads-mask` parquet."""
    if source == "tiger":
        from csb.tiger import DEFAULT_CACHE_DIR, DEFAULT_TIGER_YEAR, fetch_tiger_roads

        fetch_tiger_roads(
            Path(output),
            tiger_year=tiger_year or DEFAULT_TIGER_YEAR,
            cache_dir=Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR,
            workers=threads,
        )
    else:
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
    edges_mask: str | None,
    same_combo_dissolve: bool,
    usda_retention: bool,
    exclude_low_noncrop: bool,
    use_reclass: bool,
    upsample: int,
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
        edges_mask=edges_mask,
        same_combo_dissolve=same_combo_dissolve,
        usda_retention=usda_retention,
        exclude_low_noncrop=exclude_low_noncrop,
        use_reclass=use_reclass,
        upsample=upsample,
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
    edges_mask: str | None,
    same_combo_dissolve: bool,
    usda_retention: bool,
    exclude_low_noncrop: bool,
    use_reclass: bool,
    upsample: int,
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
        edges_mask=edges_mask,
        same_combo_dissolve=same_combo_dissolve,
        usda_retention=usda_retention,
        exclude_low_noncrop=exclude_low_noncrop,
        use_reclass=use_reclass,
        upsample=upsample,
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
