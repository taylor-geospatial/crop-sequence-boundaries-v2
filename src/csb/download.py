"""Download USDA Cropland Data Layer (CDL) rasters from NASS."""

import logging
import urllib.request
import zipfile
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

logger = logging.getLogger(__name__)

CDL_BASE_URL = "https://www.nass.usda.gov/Research_and_Science/Cropland/Release/datasets"

# Years that have 10m CDL available (in addition to 30m)
YEARS_WITH_10M = {2024, 2025}

# Earliest year with national CDL
MIN_YEAR = 2008


def cdl_url(year: int, resolution: int = 30) -> str:
    """Return the download URL for a CDL zip file."""
    if year < MIN_YEAR:
        msg = f"National CDL not available before {MIN_YEAR}, got {year}"
        raise ValueError(msg)
    if resolution == 10 and year not in YEARS_WITH_10M:
        msg = f"10m CDL only available for {sorted(YEARS_WITH_10M)}, got {year}"
        raise ValueError(msg)
    return f"{CDL_BASE_URL}/{year}_{resolution}m_cdls.zip"


def _download_one(
    year: int,
    output_dir: Path,
    resolution: int,
    overwrite: bool,
    progress: Progress,
) -> Path | None:
    """Download + extract a single CDL year. Returns the .tif path or None."""
    year_dir = output_dir / str(year)
    tif_name = f"{year}_{resolution}m_cdls.tif"
    tif_path = year_dir / tif_name

    if tif_path.exists() and not overwrite:
        logger.info("%s: Already exists at %s", year, tif_path)
        return tif_path

    year_dir.mkdir(parents=True, exist_ok=True)
    url = cdl_url(year, resolution)
    zip_path = year_dir / f"{year}_{resolution}m_cdls.zip"

    task: TaskID = progress.add_task(f"[cyan]{year} {resolution}m", total=None)
    logger.info("%s: Downloading %s", year, url)

    def _hook(block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            progress.update(task, total=total_size)
        progress.update(task, advance=block_size)

    try:
        urllib.request.urlretrieve(url, zip_path, reporthook=_hook)
    except Exception as e:
        logger.error("%s: Download failed: %s", year, e)
        progress.update(task, description=f"[red]{year} FAILED")
        if zip_path.exists():
            zip_path.unlink()
        return None

    progress.update(task, description=f"[yellow]{year} extracting")
    logger.info("%s: Extracting %s", year, zip_path)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(year_dir)
    zip_path.unlink()

    progress.update(task, description=f"[green]{year} done")

    if tif_path.exists():
        return tif_path
    tifs = list(year_dir.glob("*.tif"))
    if tifs:
        return tifs[0]
    logger.warning("%s: No .tif found after extraction", year)
    return None


def download_cdl(
    years: Sequence[int],
    output_dir: str | Path,
    resolution: int = 30,
    overwrite: bool = False,
    workers: int = 4,
) -> list[Path]:
    """Download and extract CDL rasters for the given years in parallel.

    Args:
        years: List of years to download.
        output_dir: Root directory. Files are saved to <output_dir>/<year>/.
        resolution: Pixel size in meters (10 or 30).
        overwrite: Re-download even if the file already exists.
        workers: Concurrent download workers (IO-bound; NASS is the limit).

    Returns:
        List of paths to extracted TIF files (successful downloads only).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []

    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(_download_one, y, output_dir, resolution, overwrite, progress): y
                for y in years
            }
            for fut in as_completed(futures):
                year = futures[fut]
                try:
                    path = fut.result()
                except Exception as e:
                    logger.error("%s: worker error: %s", year, e)
                    continue
                if path is not None:
                    extracted.append(path)

    return extracted
