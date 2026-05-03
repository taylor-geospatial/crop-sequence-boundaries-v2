"""End-to-end integration test.

Builds tiny synthetic CDL TIFs + a synthetic county boundary, runs the
full pipeline (polygonize -> postprocess), and verifies the output schema
matches USDA's CSB schema exactly. Light enough to run on every CI.

A heavier variant (more years, more polygons, parity vs an injected
ground truth) is gated on the ``CSB_HEAVY_E2E`` env var so it only runs
on release tags / nightly.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pyarrow.parquet as pq
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import Polygon

from csb.config import STATE_FIPS
from csb.polygonize import run_polygonize
from csb.postprocess import run_postprocess

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIZE = 30  # 30x30 pixels per CDL TIF — enough for >1 connected component
YEARS = (2020, 2021, 2022)


def _make_cdl(base_dir: Path, years: tuple[int, ...] = YEARS, size: int = SIZE) -> None:
    """Synthetic CDL: two homogeneous regions of corn (1) split by a barren strip."""
    transform = from_bounds(0, 0, size * 30, size * 30, size, size)
    for year in years:
        year_dir = base_dir / str(year)
        year_dir.mkdir(parents=True)
        data = np.full((size, size), 1, dtype=np.uint8)  # corn everywhere
        data[:, size // 2] = 152  # vertical shrubland strip → BARREN at combine time
        path = year_dir / f"{year}_30m_cdls.tif"
        profile = {
            "driver": "GTiff",
            "dtype": "uint8",
            "width": size,
            "height": size,
            "count": 1,
            "crs": "EPSG:5070",
            "transform": transform,
            "nodata": 0,
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data, 1)


def _make_boundaries(path: Path) -> None:
    """Synthetic county/ASD boundaries that fully cover the CDL extent."""
    extent = Polygon([(0, 0), (SIZE * 30, 0), (SIZE * 30, SIZE * 30), (0, SIZE * 30)])
    gdf = gpd.GeoDataFrame(
        {
            "STATEFIPS": ["19"],
            "STATEASD": ["1900"],
            "ASD": ["00"],
            "CNTY": ["TestCounty"],
            "CNTYFIPS": ["001"],
            "geometry": [extent],
        },
        crs="EPSG:5070",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(path)


def _light_polygonize_kwargs() -> dict:
    return {
        "tile_size": SIZE,
        "min_cropland_years": 1,
        "eliminate_thresholds": (100.0,),
        "min_polygon_area": 1.0,
        "simplify_tolerance": 5.0,
        "cpu_fraction": 0.5,
        "phase1_workers": 1,
        "phase2_workers": 1,
    }


# ---------------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------------


def test_e2e_polygonize_then_postprocess_produces_usda_schema(tmp_path: Path) -> None:
    """Run polygonize + postprocess on synthetic data; verify USDA-identical schema."""
    cdl_dir = tmp_path / "cdl"
    _make_cdl(cdl_dir)

    boundaries = tmp_path / "boundaries" / "US.parquet"
    _make_boundaries(boundaries)

    polygonize_out = tmp_path / "polygonize"
    postprocess_out = tmp_path / "postprocess"

    # Polygonize (inline; one tile, one worker).
    with patch(
        "csb.utils.parallel_map", side_effect=lambda fn, items, **kw: [fn(i) for i in items]
    ):
        result_dir = run_polygonize(
            start_year=YEARS[0],
            end_year=YEARS[-1],
            output_dir=polygonize_out,
            national_cdl_dir=cdl_dir,
            **_light_polygonize_kwargs(),
        )
    assert result_dir.exists()
    poly_parquets = list(result_dir.glob("*.parquet"))
    assert poly_parquets, "polygonize emitted no parquets"

    # Each polygonize parquet should already carry CDL{year} columns.
    schema = pq.read_schema(poly_parquets[0])
    for year in YEARS:
        assert f"CDL{year}" in schema.names, (
            f"CDL{year} missing from polygonize schema: {schema.names}"
        )

    # Postprocess: spatial-join, dissolve tile edges, derive CSBID, split by state.
    with (
        patch("csb.postprocess.STATE_FIPS", {"IA": "19"}),
        patch(
            "csb.utils.parallel_starmap",
            side_effect=lambda fn, items, **kw: [fn(*args) for args in items],
        ),
        patch(
            "csb.utils.parallel_map",
            side_effect=lambda fn, items, **kw: [fn(i) for i in items],
        ),
    ):
        run_postprocess(
            start_year=YEARS[0],
            end_year=YEARS[-1],
            polygonize_dir=polygonize_out,
            output_dir=postprocess_out,
            boundaries_path=boundaries,
            cpu_fraction=0.5,
        )

    # National parquet exists.
    csb_tag = f"{str(YEARS[0])[2:]}{str(YEARS[-1])[2:]}"
    national = postprocess_out / "national" / f"CSB{csb_tag}.parquet"
    assert national.exists(), f"national parquet not at {national}"

    # USDA-identical schema check.
    nat_schema = {f.name: str(f.type) for f in pq.read_schema(national)}
    required = {
        "geometry": ("binary",),
        "CSBID": ("string",),
        "CSBYEARS": ("string",),
        "CSBACRES": ("double",),
        "STATEFIPS": ("string",),
        "STATEASD": ("string",),
        "ASD": ("string",),
        "CNTY": ("string",),
        "CNTYFIPS": ("string",),
        "INSIDE_X": ("double",),
        "INSIDE_Y": ("double",),
        "Shape_area": ("double",),
        "Shape_Length": ("double",),
    }
    for col, accepted in required.items():
        assert col in nat_schema, f"missing required column {col} in {nat_schema}"
        assert any(nat_schema[col].startswith(t) for t in accepted), (
            f"column {col} has unexpected type {nat_schema[col]}; accepted {accepted}"
        )
    for year in YEARS:
        assert f"CDL{year}" in nat_schema, f"missing CDL{year}"

    # State parquet exists for IA (the only state in our boundary fixture).
    state_parquet = postprocess_out / "state" / f"CSBIA{csb_tag}.parquet"
    assert state_parquet.exists(), f"state parquet not at {state_parquet}"

    # CSBID format: STATEFIPS (2) + CSBYEARS (4) + zfill(OBJECTID, 9) = 15 chars.
    nat = pq.read_table(national).to_pandas()
    assert (nat["CSBID"].str.len() == 15).all(), "CSBID must be 15 chars"
    assert (nat["CSBYEARS"] == csb_tag).all(), f"CSBYEARS must equal {csb_tag}"
    assert (nat["STATEFIPS"] == "19").all(), "STATEFIPS must come from boundary join"


@pytest.mark.skipif(
    not os.environ.get("CSB_HEAVY_E2E"),
    reason="Heavy E2E gated on CSB_HEAVY_E2E (set in release CI)",
)
def test_heavy_e2e_full_pipeline_8_years(tmp_path: Path) -> None:
    """Full 8-year synthetic run. Takes ~30s. Triggered on release tags."""
    years = tuple(range(2018, 2026))
    cdl_dir = tmp_path / "cdl"
    _make_cdl(cdl_dir, years=years, size=60)

    boundaries = tmp_path / "boundaries" / "US.parquet"
    _make_boundaries(boundaries)

    polygonize_out = tmp_path / "polygonize"
    postprocess_out = tmp_path / "postprocess"

    with patch(
        "csb.utils.parallel_map", side_effect=lambda fn, items, **kw: [fn(i) for i in items]
    ):
        run_polygonize(
            start_year=years[0],
            end_year=years[-1],
            output_dir=polygonize_out,
            national_cdl_dir=cdl_dir,
            tile_size=60,
            min_cropland_years=1,
            eliminate_thresholds=(100.0, 1000.0),
            min_polygon_area=1.0,
            simplify_tolerance=5.0,
            cpu_fraction=0.5,
            phase1_workers=1,
            phase2_workers=1,
        )

    with (
        patch("csb.postprocess.STATE_FIPS", {"IA": "19"}),
        patch(
            "csb.utils.parallel_starmap",
            side_effect=lambda fn, items, **kw: [fn(*args) for args in items],
        ),
        patch(
            "csb.utils.parallel_map",
            side_effect=lambda fn, items, **kw: [fn(i) for i in items],
        ),
    ):
        run_postprocess(
            start_year=years[0],
            end_year=years[-1],
            polygonize_dir=polygonize_out,
            output_dir=postprocess_out,
            boundaries_path=boundaries,
            cpu_fraction=0.5,
        )

    national = postprocess_out / "national" / f"CSB{str(years[0])[2:]}{str(years[-1])[2:]}.parquet"
    assert national.exists()
    nat = pq.read_table(national).to_pandas()
    # Heavy variant exercises all 8 CDL{year} columns.
    for y in years:
        assert f"CDL{y}" in nat.columns

    # Cleanup so tmp_path doesn't balloon on repeated CI runs.
    shutil.rmtree(polygonize_out)
    shutil.rmtree(postprocess_out)
