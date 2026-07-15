"""Build the ASD+county boundary file from Census TIGER/Line + NASS crosswalk."""

import logging
import urllib.request
from pathlib import Path

import geopandas as gpd
from rich.console import Console

logger = logging.getLogger(__name__)

TIGER_COUNTIES_URL = "https://www2.census.gov/geo/tiger/TIGER2020/COUNTY/tl_2020_us_county.zip"

NASS_COUNTY_LIST_URL = (
    "https://www.nass.usda.gov/Data_and_Statistics/County_Data_Files/"
    "Frequently_Asked_Questions/county_list.txt"
)

# FIPS codes to exclude (non-CONUS)
EXCLUDE_FIPS = {"02", "15", "60", "66", "69", "72", "78"}


def _fetch_nass_crosswalk() -> dict[tuple[str, str], tuple[str, str]]:
    """Download the NASS county→ASD crosswalk.

    Returns:
        Dict mapping (STATEFIPS, COUNTYFIPS) -> (ASD_CODE, STATEASD).
    """
    logger.info("Downloading NASS county-to-ASD crosswalk")
    resp = urllib.request.urlopen(NASS_COUNTY_LIST_URL)
    text = resp.read().decode("utf-8")

    crosswalk: dict[tuple[str, str], tuple[str, str]] = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        state_fips = parts[0]
        asd_code = parts[1]
        county_fips = parts[2]
        history_flag = parts[-1]

        # Skip header, aggregates, and historical entries
        if not state_fips.isdigit():
            continue
        if county_fips in ("000", "888", "999"):
            continue
        if history_flag != "1":
            continue

        stateasd = state_fips + asd_code
        crosswalk[(state_fips, county_fips)] = (asd_code, stateasd)

    return crosswalk


def build_boundaries(output_path: str | Path) -> Path:
    """Download and build the CONUS ASD+county boundary GeoParquet.

    Combines Census TIGER/Line 2020 county polygons with the NASS
    county-to-ASD crosswalk, filters to CONUS, and reprojects to EPSG:5070.

    Args:
        output_path: Where to write the output GeoParquet.

    Returns:
        Path to the written file.
    """

    console = Console()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Download TIGER/Line counties
    console.print("[bold]Downloading Census TIGER/Line 2020 counties...")
    counties = gpd.read_file(TIGER_COUNTIES_URL)
    console.print(f"  Loaded {len(counties)} counties")

    # 2. Filter to CONUS
    counties = counties[~counties["STATEFP"].isin(EXCLUDE_FIPS)].copy()
    console.print(f"  CONUS: {len(counties)} counties")

    # 3. Fetch NASS crosswalk
    console.print("[bold]Downloading NASS ASD crosswalk...")
    crosswalk = _fetch_nass_crosswalk()
    console.print(f"  {len(crosswalk)} county→ASD mappings")

    # 4. Join
    asd_codes = []
    stateasd_codes = []
    for _, row in counties.iterrows():
        key = (row["STATEFP"], row["COUNTYFP"])
        if key in crosswalk:
            asd, stateasd = crosswalk[key]
            asd_codes.append(asd)
            stateasd_codes.append(stateasd)
        else:
            asd_codes.append("")
            stateasd_codes.append("")

    counties["STATEFIPS"] = counties["STATEFP"]
    counties["ASD"] = asd_codes
    counties["STATEASD"] = stateasd_codes
    counties["CNTY"] = counties["NAME"]
    counties["CNTYFIPS"] = counties["COUNTYFP"]

    # 5. Keep only needed columns
    result = counties[["STATEFIPS", "STATEASD", "ASD", "CNTY", "CNTYFIPS", "geometry"]].copy()

    # Drop counties with no ASD mapping
    before = len(result)
    result = result[result["ASD"] != ""].copy()
    if before != len(result):
        logger.info("Dropped %s counties with no ASD mapping", before - len(result))

    # 6. Reproject to Albers
    console.print("[bold]Reprojecting to EPSG:5070...")
    result = result.to_crs("EPSG:5070")

    # 7. Write GeoParquet
    result.to_parquet(output_path)
    console.print(f"[bold green]Wrote {len(result)} boundaries to {output_path}")

    return output_path
