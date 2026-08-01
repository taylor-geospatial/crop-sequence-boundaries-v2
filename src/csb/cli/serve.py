"""Publishing commands: pmtiles."""

from pathlib import Path

import click

from csb.cli._group import console, main


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
