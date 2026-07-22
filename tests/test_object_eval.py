"""Object-level matched-polygon IoU must reproduce a known directional match.

A single USDA field split into two equal generated fields should best-match one
half with IoU 0.5, and a perfectly coincident field should match at 1.0. These
pin the directional-match semantics used for the §5.3 analysis.
"""

from pathlib import Path

import duckdb
import geopandas as gpd
import shapely

from csb.object_eval import matched_polygon_iou, summarize_matched


def _write(path: Path, polys: list) -> None:
    geoms = [shapely.box(*b) for b in polys]
    gdf = gpd.GeoDataFrame(
        {
            "geometry": geoms,
            "xmin": [b[0] for b in polys],
            "ymin": [b[1] for b in polys],
            "xmax": [b[2] for b in polys],
            "ymax": [b[3] for b in polys],
        },
        crs="EPSG:5070",
    )
    gdf.to_parquet(path)


def test_matched_iou_split_and_exact(tmp_path: Path) -> None:
    # USDA: one field [0,0,20,20] plus one isolated field [100,100,110,110].
    _write(tmp_path / "usda.parquet", [(0, 0, 20, 20), (100, 100, 110, 110)])
    # Ours: two halves of the first field; nothing near the isolated field.
    _write(tmp_path / "ours.parquet", [(0, 0, 20, 10), (0, 10, 20, 20)])

    conn = duckdb.connect()
    conn.install_extension("spatial")
    conn.load_extension("spatial")
    res = matched_polygon_iou(
        conn,
        str(tmp_path / "ours.parquet"),
        str(tmp_path / "usda.parquet"),
        (-10, -10, 200, 200),
    )
    conn.close()

    assert res["n_usda"] == 2
    assert res["n_matched"] == 1  # isolated USDA field has no overlap
    assert res["n_no_overlap"] == 1
    # The split field matches one half: inter 200 / union (400+200-200) = 0.5.
    assert abs(res["ious"][0] - 0.5) < 1e-9

    summ = summarize_matched(res)
    assert summ["frac_no_overlap"] == 0.5
    assert abs(summ["median_iou"] - 0.5) < 1e-9
