"""TIGER-based road and rail mask for cropland-mask preprocessing.

USDA builds its road/rail mask from TIGER lines burned as thin 10 m rasters.
This module produces the analogous input for our pipeline: unbuffered TIGER
road + rail centerlines in EPSG:5070 with bbox columns, which
``rasterize_roads_for_window`` burns all_touched (~1 px at 30 m) via the
existing ``--roads-mask`` path. This is the roads source used by the v5
configuration of record.

Census www2 is flaky under load — it can return HTTP 200 with a truncated
body or an HTML error page — so every zip is validated (``zipfile.testzip``)
and re-downloaded with growing backoff, staged through a ``.part`` file. A
county whose object stays unavailable is skipped and logged rather than
aborting the national build.
"""

import logging
import subprocess
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

logger = logging.getLogger(__name__)

DEFAULT_TIGER_YEAR = 2025
DEFAULT_CACHE_DIR = Path("data/tiger")

# Primary, secondary, local roads. Excludes 4WD trails (S1500: field-access
# tracks would split fields USDA keeps whole), ramps, service drives, paths.
DEFAULT_MTFCC = ("S1100", "S1200", "S1400")

# Alaska, Hawaii, and territories — excluded so the build covers CONUS + DC.
_NON_CONUS_STATEFP = ("02", "15", "60", "66", "69", "72", "78")


def tiger_url(kind: str, name: str, tiger_year: int = DEFAULT_TIGER_YEAR) -> str:
    """URL of a TIGER zip, e.g. ``tiger_url("ROADS", "tl_2025_19001_roads")``."""
    return f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/{kind}/{name}.zip"


def valid_zip(path: Path) -> bool:
    """True if ``path`` is a complete, uncorrupted zip archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except zipfile.BadZipFile:
        return False


def fetch(url: str, dest: Path, *, is_zip: bool = True, attempts: int = 6) -> Path:
    """Download ``url`` to ``dest`` with retry/backoff and zip validation.

    Census www2 sometimes returns a 200 with a truncated body or an HTML
    error page (even a Cloudflare 520 for a listed object) under load; curl
    reports success on the HTML stub, so we validate the zip and re-download
    with growing backoff until it is intact. Downloads stage through a
    ``.part`` file so ``dest`` is only ever a complete file.
    """
    if dest.exists() and (not is_zip or valid_zip(dest)):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for i in range(attempts):
        rc = subprocess.run(
            [
                "curl",
                "-sfL",
                "--retry",
                "5",
                "--retry-all-errors",
                "--retry-delay",
                "2",
                "-o",
                str(part),
                url,
            ],
            check=False,
        ).returncode
        if rc == 0 and (not is_zip or valid_zip(part)):
            part.rename(dest)
            return dest
        part.unlink(missing_ok=True)
        if i < attempts - 1:
            time.sleep(3 * (i + 1))  # 3,6,9,... s — let a poisoned edge recover
    msg = f"failed to fetch a valid file after {attempts} attempts: {url}"
    raise RuntimeError(msg)


def unzip(zpath: Path) -> Path:
    """Extract ``zpath`` next to itself and return the contained shapefile."""
    out = zpath.with_suffix("")
    if not out.exists():
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(out)
    shp = list(out.glob("*.shp"))
    return shp[0]


def _read_lines_5070(
    conn: duckdb.DuckDBPyConnection, shp: Path, where: str = "TRUE"
) -> list[tuple[bytes, str]]:
    return conn.execute(f"""
        SELECT ST_AsWKB(ST_Transform(geom, 'EPSG:4269', 'EPSG:5070', always_xy => true)),
               MTFCC
        FROM ST_Read('{shp}') WHERE {where}
    """).fetchall()


def fetch_tiger_roads(
    output: Path,
    *,
    tiger_year: int = DEFAULT_TIGER_YEAR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    mtfcc: tuple[str, ...] = DEFAULT_MTFCC,
    workers: int = 8,
) -> Path:
    """Download CONUS TIGER road + rail lines and write a roads-mask GeoParquet.

    Fetches every county's ROADS zip (lower 48 + DC) plus the national RAILS
    zip, filters roads to ``mtfcc`` classes, reprojects to EPSG:5070, and
    writes ``(geometry, kind, xmin, ymin, xmax, ymax)`` — the same schema as
    :func:`csb.roads.fetch_overture_roads`, ready for ``--roads-mask``.
    Downloads cache under ``cache_dir`` so re-runs only fetch what's missing.
    """
    conn = duckdb.connect()
    conn.execute("INSTALL spatial; LOAD spatial;")
    county_name = f"tl_{tiger_year}_us_county"
    county_shp = unzip(
        fetch(tiger_url("COUNTY", county_name, tiger_year), cache_dir / f"{county_name}.zip")
    )

    non_conus = ", ".join(f"'{s}'" for s in _NON_CONUS_STATEFP)
    geoids = [
        r[0]
        for r in conn.execute(f"""
            SELECT GEOID FROM ST_Read('{county_shp}')
            WHERE STATEFP NOT IN ({non_conus})
        """).fetchall()
    ]
    logger.info("%d CONUS counties", len(geoids))

    def _roads_zip(geoid: str) -> Path | None:
        # A county whose object is transiently unavailable server-side is
        # skipped (its roads just won't split fields there) rather than
        # aborting the whole national build; every skip is logged below.
        name = f"tl_{tiger_year}_{geoid}_roads"
        try:
            return fetch(tiger_url("ROADS", name, tiger_year), cache_dir / "roads" / f"{name}.zip")
        except RuntimeError as e:
            logger.warning("SKIP %s: %s", geoid, e)
            return None

    ordered = sorted(geoids)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        fetched = list(ex.map(_roads_zip, ordered))
    zips = [z for z in fetched if z is not None]
    skipped = [g for g, z in zip(ordered, fetched, strict=True) if z is None]
    logger.info("downloaded %d/%d county road zips", len(zips), len(ordered))
    if skipped:
        logger.warning("SKIPPED %d counties (unavailable): %s", len(skipped), ",".join(skipped))

    mtfcc_where = "MTFCC IN (" + ", ".join(f"'{m}'" for m in mtfcc) + ")"
    rows: list[tuple[bytes, str]] = []
    for i, z in enumerate(zips):
        rows.extend(_read_lines_5070(conn, unzip(z), mtfcc_where))
        if (i + 1) % 100 == 0:
            logger.info("  %d/%d counties, %d lines", i + 1, len(zips), len(rows))

    rails_name = f"tl_{tiger_year}_us_rails"
    rails_shp = unzip(
        fetch(tiger_url("RAILS", rails_name, tiger_year), cache_dir / f"{rails_name}.zip")
    )
    n_road = len(rows)
    rows.extend(_read_lines_5070(conn, rails_shp))
    logger.info("%d road lines + %d rail lines (national)", n_road, len(rows) - n_road)
    conn.close()

    geoms = shapely.from_wkb([bytes(r[0]) for r in rows])
    kinds = [r[1] for r in rows]
    minx, miny, maxx, maxy = shapely.bounds(geoms).T
    table = pa.table(
        {
            "geometry": pa.array(shapely.to_wkb(geoms), type=pa.binary()),
            "kind": pa.array(kinds, type=pa.string()),
            "xmin": pa.array(minx, type=pa.float64()),
            "ymin": pa.array(miny, type=pa.float64()),
            "xmax": pa.array(maxx, type=pa.float64()),
            "ymax": pa.array(maxy, type=pa.float64()),
        }
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output, compression="zstd", row_group_size=50000)
    logger.info("wrote %s (%d lines)", output, len(rows))
    return output
