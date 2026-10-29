# BathyTrace 0.1.0 — publication review

Reviewed 29 October 2026. Source: `C:\Users\moham\Desktop\bathy trace`.
This is the chart digitization project, previously displayed as Contour Studio 1.2.1.
The separate Copernicus CoastDepth project and its review do not apply here.

## Verdict

Ready for further local testing; **not ready for QGIS repository submission**.
The public homepage, source repository and issue tracker have not been supplied.
Resolved: GPL-3.0-or-later was applied with the owner's authorization. The full
GPL v3 text is included in LICENSE, with copyright and licence notices in each
Python source, README, metadata, NOTICE and the dock interface.
Repository name availability and source/archive parity still need verification.

## Completed

- BathyTrace title throughout menus, dock, layer names, messages, documentation
  and DXF CRS companion notes.
- Requested description: Professional Bathymetric Contour Digitization from Charts & Imagery.
- Version 0.1.0 and first-release changelog.
- Author and prominent copyright: Mohamed Hamouda, 2026.
- Corrected contact: Mohamedhamoudaa186@gmail.com.
- About metadata now discloses NumPy/Pillow, supported GeoTIFF CRSs, unrotated input,
  QGIS 3-only support, Windows validation scope and temporary working layers.
- ZIP uses the importable folder `bathytrace`, not the disk folder with a space.
  Caches, test output, chart imagery and compiled binaries are excluded.
- Internal class names, dock object ID, field names and JSON project schema remain
  unchanged to avoid unnecessary compatibility changes.

## Checks performed in this review

Actual installed QGIS 3.40.15 on Windows, offscreen with an interface harness:

- Python syntax, metadata version and author email.
- Plugin construction, Raster menu registration and dock branding.
- Synthetic EPSG:3857 GeoTIFF georeferencing and working-layer creation.
- Simple straight-line extraction and assisted tracing.
- Three-contour depth ordering and an aligned gap join.
- GeoPackage, GeoJSON, DXF and Shapefile export/readback with three features each.
- DXF and Shapefile vertex elevations preserved as 0, -1 and -2 metres.
- Working-layer deletion and plugin unload.

These checks do not validate real chart extraction quality, all CRS combinations,
interactive QGIS installation, Civil 3D interaction, QGIS 4, Linux or macOS.
The first harness run encountered a Windows file lock while deleting a temporary
Shapefile after all functional checks passed. The harness tolerates deferred
temporary cleanup; synthetic test files are excluded from the package.
Historical regression claims in README are inherited, not newly reproduced in full.

## Upgrade areas and workflow cleanup

1. **Contour extraction loses topology.** `raster_core.py:Chart.detect` keeps one
   breadth-first path per connected dark-pixel component. That cannot preserve all
   branches or a complete closed loop. Chart text touching contours can change the
   chosen path. Add skeletonization, junction splitting and explicit closed-loop
   handling, with real-chart regression fixtures. Treat extracted lines as drafts.
2. **Cancellation does not interrupt computation.** `plugin.py:background` checks
   cancellation before and after `fn()`, but extraction/joining loops receive no
   cancellation callback. Exit discards results; it may leave CPU work running.
   Add cooperative cancellation and progress checks to the core loops.
3. **Temporary contours are easy to lose.** The warnings are useful, but ordinary
   QGIS project saving does not persist memory-layer features. Offer a persistent
   GeoPackage workspace or an explicit save prompt before closing the project.
4. **Editable project round-trip is incomplete.** `save_project` writes depthState
   and depthSettings, while `load_project` resets the seed and does not restore those
   settings. Restore validated values or clearly document that only contours reload.
5. **Repeated full-layer replacement is costly.** `apply_attribute` and depth
   assignment replace every feature, even for a single value change. Update target
   attributes within an edit command to preserve feature IDs and selection and
   reduce work on large charts.
6. **3D export builds the layer twice.** `export` constructs it to validate and
   `export_to` constructs it again. Share the validated result, particularly for
   large datasets. Consider atomic writes for DXF plus its CRS companion.
7. **Input support is narrow.** Only unrotated, internally georeferenced TIFFs in
   EPSG:3857/4326 work. Use QGIS/GDAL geotransforms for additional CRSs, rotated
   imagery and other georeferenced chart formats before expanding support claims.
8. **Performance and reproducibility.** Gap joining uses repeated endpoint-pair
   and segment-intersection scans; use spatial indexes. Extraction uses set order;
   deterministic seed/tie-breaking would make identical input more reproducible.
9. **Release engineering.** Add a build/check script, maintained regression tests,
   sample chart fixture and dependency instructions. The original archive contained
   no test suite. No runtime libraries should be bundled into the plugin ZIP.

## Official publishing references

- https://plugins.qgis.org/docs/publish
- https://docs.qgis.org/3.40/en/docs/pyqgis_developer_cookbook/plugins/releasing.html

The official requirements include a GPL-compatible licence, LICENSE file, complete
metadata, working public homepage/source/tracker links, dependency disclosure,
documentation, a single plugin folder in the ZIP, no binaries and size below 25 MB.
