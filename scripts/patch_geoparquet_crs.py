"""Patch GeoParquet `geo` metadata in-place: rewrite the short-form CRS
spec ({"id": {"authority": "EPSG", "code": N}}) to full PROJJSON.

The short form lacks the "type" field that pyproj 3.x requires, so any
downstream tool that round-trips through pyproj (geopandas, pyogrio,
GDAL/OGR for tippecanoe) refuses to read these files.

Usage:
    uv run python scripts/patch_geoparquet_crs.py <file_or_dir> [...]

Each file is rewritten in place. Other parquet content is byte-preserved
where possible — we re-write only the schema metadata via a full read-modify-write
because pyarrow doesn't expose a metadata-only patch path for a single file.
"""

import json
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq
from pyproj import CRS


def patch(p: Path) -> tuple[bool, str]:
    """Returns (modified, message)."""
    try:
        meta = pq.read_metadata(str(p))
    except Exception as e:
        return False, f"failed to read metadata: {e}"
    if not meta.metadata or b"geo" not in meta.metadata:
        return False, "no 'geo' metadata"
    geo = json.loads(meta.metadata[b"geo"])
    cols = geo.get("columns", {})
    needs_patch = False
    for info in cols.values():
        crs = info.get("crs")
        if isinstance(crs, dict) and "type" not in crs and "id" in crs:
            authority = crs["id"].get("authority")
            code = crs["id"].get("code")
            if authority == "EPSG" and code is not None:
                info["crs"] = CRS.from_epsg(code).to_json_dict()
                needs_patch = True
    if not needs_patch:
        return False, "already valid"

    # Read the table, replace the geo metadata, write it back.
    table = pq.read_table(str(p))
    md = dict(table.schema.metadata or {})
    md[b"geo"] = json.dumps(geo).encode()
    table = table.replace_schema_metadata(md)
    tmp = p.with_suffix(p.suffix + ".tmp")
    pq.write_table(table, tmp)
    tmp.replace(p)
    return True, "patched"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: patch_geoparquet_crs.py <file_or_dir> [...]")
    targets: list[Path] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            targets.extend(sorted(p.rglob("*.parquet")))
        elif p.is_file():
            targets.append(p)
        else:
            print(f"  skipping (missing): {p}")
    print(f"patching {len(targets)} parquet files ...")
    n_ok = 0
    n_skip = 0
    n_fail = 0
    for p in targets:
        t0 = time.perf_counter()
        try:
            modified, msg = patch(p)
        except Exception as e:
            print(f"  FAIL  {p}: {type(e).__name__}: {e}")
            n_fail += 1
            continue
        elapsed = time.perf_counter() - t0
        if modified:
            print(f"  OK    {p}  ({elapsed:.1f}s)")
            n_ok += 1
        else:
            print(f"  skip  {p}  [{msg}]")
            n_skip += 1
    print(f"\ndone: {n_ok} patched, {n_skip} skipped, {n_fail} failed")


if __name__ == "__main__":
    main()
