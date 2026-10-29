# BathyTrace 0.1.0 for QGIS

**Professional Bathymetric Contour Digitization from Charts & Imagery**

**Copyright © 2026 Mohamed Hamouda.**

Author contact: Mohamedhamoudaa186@gmail.com

Native QGIS tool for contour extraction, joining, depth assignment and GIS/CAD export. No browser or web server is required.

## Install or update

1. Save/export your current contours before updating. Working layers are temporary; saving a QGIS .qgz alone does not save their features.
2. Open **Plugins → Manage and Install Plugins → Install from ZIP**, select **BathyTrace-0.1.0.zip**, and install it. Replace the previous version if prompted.
3. Restart QGIS after updating so the old Python modules are unloaded. Enable BathyTrace if necessary.
4. Open **Raster → BathyTrace → BathyTrace**, or use its green toolbar icon.

Supports QGIS 3.40+ in the 3.x series, tested with QGIS 3.40.15 on Windows. QGIS 4 / Qt 6 is not supported. NumPy and Pillow must be available in QGIS's Python; both are present in the tested installation. Dependencies are not installed automatically.

## Main workflow

### 1 · Chart

**Open TIFF**, then **Extract + join contours**. These operations now run together with conservative defaults. Use an unrotated GeoTIFF in EPSG:3857 or EPSG:4326. The QGIS display CRS may differ; map clicks are transformed correctly.

Extraction requires an empty working layer. Opening a TIFF again creates a new layer and retains previous layers. Cyan strokes identify inferred connections across gaps.

### 2 · Depths

Click **Select a contour on the map**, then click anywhere along a line. Enter its depth/unit and **Save selected value** for individual edits.

For a sequence:

1. Select the shoreline or shallowest known line and **Use selected as shallowest**. Shallowest means closest to zero: −1 rather than −9 m.
2. Enter **Start depth** (0 for shoreline) and **Interval** (0.5, entered as positive spacing).
3. **Pick deeper direction + preview**, then click across the contours toward deeper water. The calculation extends that direction across the chart, beyond the clicked point.
4. Inspect the preview table and **Apply depth sequence**. Values decrease: 0, −0.5, −1.0… Only crossed lines are assigned. Existing values on those lines are replaced; estimates remain unreviewed.

Closed circles, nested loops, open winding lines and nearly closed contours are supported. Each line is counted once at its first crossing. The sequence stops at the first return crossing of any contour, so it does not continue up the opposite slope or into another basin. Pick a direction through the nested lines toward the deepest zone. Lines outside this slope remain unchanged; assign another basin separately. The preview reports when it stops at a return crossing. Review the estimated values before applying; geometry alone cannot establish whether a local feature is a depression or a mound.

Missing or extra contours can shift the sequence. **Undo last change** uses QGIS's native edit undo stack. Committing edits clears that history.

### 3 · Export

Choose the format and **Export**:

| Format | Geometry / coordinates | Attributes |
| --- | --- | --- |
| 3D DXF — Civil 3D | Actual 3D POLYLINE entities, XYZ in metres in the chosen projected CRS | BATHYMETRY CAD layer; elevation at every vertex |
| 3D Shapefile | Polyline Z, selected projected metre CRS | cs_id, original depth and unit, elev_m, reviewed, d_method |
| GeoPackage | 2D lines; source CRS unless another export CRS is selected | Full working-layer attributes |
| GeoJSON | 2D lines in WGS84 longitude/latitude | Full working-layer attributes |

**For DXF and Shapefile, explicitly choose the projected metre CRS used in the destination drawing.** Geographic longitude/latitude is rejected for 3D export. The chosen CRS must match the Civil 3D drawing; chart Web Mercator is not automatically assumed to be your engineering grid.

Each exported 3D contour needs a finite depth and known unit. If some are missing, the export dialog shows the counts and offers **Select lines needing depths** or **Export valued contours only**. The latter explicitly omits unvalued lines and reports the exported/omitted counts. It never substitutes zero for missing depths. If no lines have depths, apply the depth sequence preview first, or save individual line values. Preview alone does not save values.

Feet and fathoms are converted to metres for Z. Negative depths remain negative elevations. No sign inversion or vertical datum transformation is applied.

Shapefile is a set of files: keep .shp, .shx, .dbf, .prj and any .cpg together. Its field names are intentionally short; large bridge metadata is omitted. Use GeoPackage or editable JSON to preserve all editing metadata.

DXF includes a **.dxf.crs.txt** companion describing the CRS, metre units and elevation convention. DXF does not automatically assign this CRS to a Civil 3D drawing.

## Civil 3D workflow

Open the DXF and configure the Civil 3D drawing coordinate system and units to match its companion file. The lines are 3D polylines with a constant elevation per contour. To create a surface, create a TIN surface and add them through **Surface → Definition → Contours → Add**. This exports source contour linework, not a Civil 3D surface or DWG file.

Autodesk documents using [2D or 3D contour polylines at constant elevation](https://help.autodesk.com/cloudhelp/2026/ENU/Civil3D-UserGuide/files/GUID-F7C05A09-A0C2-4A39-8D3F-BA945B909D92.htm). DXF flags follow its [POLYLINE](https://help.autodesk.com/cloudhelp/2015/ENU/AutoCAD-DXF/files/GUID-ABF6B778-BE20-4B49-9B58-A94E64CEFFF3.htm) and [VERTEX](https://help.autodesk.com/cloudhelp/2021/ENU/AutoCAD-DXF/files/GUID-0741E831-599E-4CBF-91E1-8ADBCFD6556D.htm) specifications. Generated DXF files were read back with QGIS/GDAL to verify vertex Z values. They were not interactively tested inside Civil 3D.

## Exit and layer deletion

**Exit tool** or the panel's **X** returns control to the previous QGIS map tool (or Pan), hides overlays and keeps your layers. Reopen the panel from the Raster menu to continue.

Deleting the contour layer safely disconnects its signals, clears its reference, and releases the map tool. Running calculations are cancelled and their results discarded. Open a TIFF again to create a new working layer. Project clearing and plugin unloading use the same cleanup. Removing only the raster retains the existing vector work.

## Advanced controls

The collapsed **Advanced** section contains:

- Use a selected QGIS raster.
- Manual/assisted tracing: right-click or Enter finishes, Backspace undoes an anchor, Esc discards the draft. Assisted anchors must be within 1,600 source pixels.
- Detection thresholds, separate extraction/joining, bridge visibility.
- Review status and preview recalculation.
- Save/open editable JSON projects, including browser-version projects with the matching TIFF. Finish/discard active geometry before transferring it.

Use QGIS vertex tools for detailed geometry edits. Do not rename required fields, duplicate IDs, or convert the working layer to multipart geometry. Edits made during a calculation are preserved: the stale result is discarded.

## Package and verification

The ZIP contains source, icon, metadata and instructions. It excludes chart imagery, project data, browser code and binaries. It is a local package, not a public repository listing; the public homepage, source repository and issue tracker still need to be supplied before submission.

The inherited project documentation reports QGIS 3.40.15 regression checks covering deletion of the working layer, a deletion/background-task race, reopening after deletion, project clearing, Exit and panel X, extraction/joining, depth estimation/undo, CRS transformation, 3D DXF/Shapefile readback, and GeoPackage/GeoJSON export.


See [PUBLISHING_REVIEW.md](PUBLISHING_REVIEW.md) for current verification results and publication gaps.

## Licence

Copyright (C) 2026 Mohamed Hamouda. BathyTrace is licensed under
**GPL-3.0-or-later**. You may use, modify and redistribute it under that licence.
It is provided without warranty. See [LICENSE](LICENSE) for the full GPL v3 text
and [NOTICE](NOTICE) for the version choice and attribution. Third-party libraries
and input charts retain their own terms.
