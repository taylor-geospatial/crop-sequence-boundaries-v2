# USDA CSB CDL-smoothing reference (ground truth)

Received from the USDA CSB team by email on 2026-07-21. This is the previously
unpublished preprocessing that feeds `Combine` in `CSB-create.py` — the step
our parity investigation identified as the missing piece. Code below is
**verbatim** as shared; do not edit. Our port lives in `src/csb/usda_filter.py`
(`--usda-noise-filter`).

## Context from the email (paraphrased)

- **Tiles** existed only to parallelize ArcGIS without splitting fields:
  county-sized tiles were cut from the *road network* (county boundaries split
  fields). Not needed by a method that tiles differently.
- **Roads/rail** were developed from **TIGER lines**, converted to **10 m
  rasters** to ensure continuity (no stair-step, no gaps). They offered to
  share the rasters.
- **Two smoothing implementations existed.** GEE came first; the ArcPy version
  below is the one they "became to favor". Order in the favored pipeline:
  **split → reclass → filter → resample (to 10 m) → reimpose roads**.
- Key quote: *"We ran the polygon creation without CDL smoothing and those
  results were often better but a minimal CDL filtering made it run faster.
  Smoothing the CDL might not be needed with your method."* — i.e. the filter
  exists for ArcGIS runtime, not accuracy.

## GEE version (superseded)

```javascript
//already resampled to 10m and reclassified
var patchSize = 320;
var kernel = {
    radius: 12,
    kernelType: 'circle',
    iterations: 6,
    units: 'pixels',
};
var clumpedCDLm1 = CDLm8b.connectedPixelCount(patchSize, false);
var filteredCDLm1 = CDLm8b.focal_mode(kernel);
var smoothedCDLm1b = CDLm8b.where(clumpedCDLm1.lt(patchSize),filteredCDLm1).updateMask(edges.eq(0));
var smoothedCDLm1a = smoothedCDLm1b.focal_mode(2, 'diamond', 'pixels', 1).updateMask(edges.eq(0));
```

Interpretation: at 10 m, components smaller than 320 px (3.2 ha!) are replaced
by a circle-radius-12 (120 m) focal mode iterated 6×, then a diamond-radius-2
focal mode pass, with road/rail `edges` masked out. This is *heavy* smoothing —
conceptually what our `csb.focal` emulates — and USDA moved away from it.

## ArcPy version (favored, production)

```python
def split_raster(area):
    print("Start: "f"{area}")
    arcpy.env.workspace = fr"{scratch_dir}\{area}"
    with arcpy.EnvManager(parallelProcessingFactor="0",pyramid="NONE"):
         arcpy.management.SplitRaster(in_raster = input_ras,
                                      out_folder = fr"{scratch_dir}\{area}",
                                      out_base_name = area,
                                      split_method="POLYGON_FEATURES",
                                      split_polygon_feature_class = fr'{area_tiles_dir}\{area}.shp')

    #Reclass and Project
    with arcpy.EnvManager(outputCoordinateSystem='GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'):
        arcpy.ddd.ReclassByTable(
            in_raster=fr"{scratch_dir}\{area}\{area}0.tif",
            in_remap_table= reclass_table,
            from_value_field="FROM",
            to_value_field="TO",
            output_value_field="OUT",
            out_raster=fr"memory\{area}_reclass",
            missing_values="DATA")

    #Filter: Region Group
    out_raster = arcpy.sa.RegionGroup(
        in_raster=fr"memory\{area}_reclass",
        number_neighbors="FOUR",
        zone_connectivity="WITHIN",
        add_link="NO_LINK",
        excluded_value=0)
    out_raster.save(fr"memory\{area}_region")

    #Filter: Condition
    out_raster = arcpy.sa.Con(
        in_conditional_raster=fr"memory\{area}_region",
        in_true_raster_or_constant=99,
        in_false_raster_or_constant=fr"memory\{area}_reclass",
        where_clause="Count <= 2")
    out_raster.save(fr"memory\{area}_con")
    arcpy.management.Delete(in_data=fr"memory\{area}_region", data_type="")
    arcpy.management.Delete(in_data=fr"memory\{area}_reclass")

    #Filter: Shrink
    with arcpy.EnvManager(parallelProcessingFactor="0"):
        out_raster = arcpy.sa.Shrink(
            in_raster=fr"memory\{area}_con",
            number_cells=2,
            zone_values=[99],
            shrink_method="MORPHOLOGICAL")
        out_raster.save(fr"memory\{area}_shrink")
    arcpy.management.Delete(in_data=fr"memory\{area}_con", data_type="")
    arcpy.management.Delete(in_data=fr"{scratch_dir}\{area}\{area}0.tif", data_type="")

    #Resample cell size and reimpose roads back to tiff
    with arcpy.EnvManager(outputCoordinateSystem='GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]', cellSize=input_road):
        out_raster = arcpy.sa.Con(
            in_conditional_raster=input_road,
            in_true_raster_or_constant=fr"memory\{area}_shrink",
            in_false_raster_or_constant=None,
            where_clause="Value = 0")
    out_raster.save(fr"{output_folder}\{area}_{YEAR}_0.TIF")
    arcpy.management.Delete(in_data=fr"memory\{area}_shrink", data_type="")
    print("Done: "f"{area}")
```

Interpretation, step by step (per CDL year, at native 30 m, on the
*reclassified* raster):

1. **Reclass** by `reclass_table` (FROM/TO/OUT ranges; table itself still
   unpublished — our `--exclude-low-noncrop` covers the 61–65 grouping known
   from CSB1825 metadata).
2. **RegionGroup** — 4-connected, same-value (`WITHIN`) components, value 0
   excluded.
3. **Con Count <= 2 → 99** — components of ≤ 2 pixels (≤ 0.18 ha at 30 m)
   flagged as noise.
4. **Shrink(2 cells, zone 99, MORPHOLOGICAL)** — noise zones erased; the
   surrounding zones grow inward to fill them.
5. **Resample** to 10 m (`cellSize=input_road`) and **reimpose roads**:
   `Con(road == 0 → smoothed CDL, else NoData)` — TIGER-derived 10 m road/rail
   pixels become NoData so fields split at roads.

So the production noise filter is *minimal*: kill ≤2-pixel speckle, nothing
else. That is far weaker than both the GEE version and our focal-mode
experiments, and consistent with our finding that heavy smoothing raises
coverage IoU but *lowers* instance agreement (boundary migration).

## Reclass table (received 2026-07-21)

Peter sent `reclass_table` as a CSV — saved verbatim at
`src/csb/data/CDL_tempGeneralCode.csv`, ported in `src/csb/reclass.py`
(`--reclass`). It is a FROM/TO/OUT lookup (raw CDL → "temp general code") that
does two things:

1. **Crop / non-crop definition.** `OUT == 0` is non-crop and dropped. This is
   broader than the 61–65 grouping: 58/59/60 (clover, sod/grass seed,
   switchgrass), 63/64/65, 71, 176 (grass/pasture), 190/195 (wetlands), all
   developed/water/forest → 0; hay (37) and specialty crops stay crop.
2. **Class consolidation.** Commonly-confused classes collapse to one temp
   code so year-to-year flicker doesn't fracture a field — e.g. tree-fruit /
   orchard classes 66/67/68/72/74–77 and 204/210–212/215/217/218/220/223 → 46.

Barren (CDL 61, 131) → temp 45, which is how `COUNT45` in the retention rule
works. Peter's notes: the table is *ideally state- and year-specific* (mis-
classification differs regionally); this national version is what he shared.
He also said USDA reports only the original CDL code in outputs, never the
temp code — so our port keeps raw CDL for attributes and uses temp codes only
for delineation.

## Roads / rail (method described 2026-07-21)

Peter couldn't easily share the original code (built outside USDA) but gave the
method: **TIGER Edges** for roads (all but the smallest class) + **all rail
codes** (+ fence lines), buffer each line **5 m (maybe 7 m)** → ~10 m-wide
polygon, rasterize at **10 m**. Pre-2020 they used one static network for all
years; with more years now he suggests **per-year TIGER**. The >1 GB zipped
raster is awkward to send, so rebuilding from TIGER (`scratch/fetch_tiger.py`)
is the intended path. He also flagged: **use the CSB1825 metadata as reference
— things changed since Hunt et al. 2024.**

## What remains unpublished

- Nothing load-bearing on the raster side. The reclass table is national, not
  state/year-specific (a possible future refinement).
