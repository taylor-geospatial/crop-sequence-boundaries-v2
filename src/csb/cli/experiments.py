"""Experiment commands: bench-eliminate, tile-sweep."""

from pathlib import Path

import click

from csb.cli._group import console, main
from csb.config import DEFAULT_NATIONAL_CDL_DIR


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
    default="raster,duckdb",
    show_default=True,
    help="Comma-separated subset of raster,duckdb to benchmark.",
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


@main.command(name="tile-sweep")
@click.option(
    "--region", required=True, help="Region name from csb.parity.DEFAULT_REGIONS or 'all'."
)
@click.option(
    "--usda-indexed",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Prepped USDA parquet (from prep_usda / parity-prep).",
)
@click.option(
    "--cropland-years", default="1,2", show_default=True, help="min_cropland_years values."
)
@click.option(
    "--simplify", default="30,60", show_default=True, help="simplify_tolerance (m) values."
)
@click.option(
    "--min-area", default="10000", show_default=True, help="min_polygon_area (m²) values."
)
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
        list(DEFAULT_REGIONS) if region == "all" else [r for r in DEFAULT_REGIONS if r[0] == region]
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
            region_name=name,
            target_x=tx,
            target_y=ty,
            params=params,
            usda_indexed=Path(usda_indexed),
            threads=threads,
        )
        rec["params"]["roads_mask"] = bool(road)  # store flag, not path
        results.append(rec)
        iou = rec.get("iou")
        console.print(f"    -> IoU={iou:.3f} " if iou is not None else "    -> (no output) ")
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(results, indent=2))
    console.print(f"[bold green]tile-sweep: {output}")
