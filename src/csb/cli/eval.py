"""Validation commands: parity-prep, parity, object-eval, instance-metrics, visualize."""

import json
from pathlib import Path

import click

from csb.cli._group import console, main


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

    regions = [r for r in DEFAULT_REGIONS if r[0] == region] if region else list(DEFAULT_REGIONS)
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

    regions = [r for r in DEFAULT_REGIONS if r[0] == region] if region else list(DEFAULT_REGIONS)
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
