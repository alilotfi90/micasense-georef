# GCP Dimension Check V2 Plan

## Goal

Build a small sidecar workflow that checks whether the current georeferencing maps a
manually marked purple GCP to its expected real-world size. The first target is the
instructor's check:

1. Pick a raw MicaSense `.tif` image that contains one GCP.
2. Generate the georeferenced editor view in memory.
3. Mark the four GCP corner pixels in that editor view as `(p1_x, p1_y)` through
   `(p4_x, p4_y)`.
4. Convert those editor-view pixels to georeferenced ground coordinates `(X_i, Y_i)`.
5. Compute Euclidean side distances around the GCP.
6. Compare the distances against the expected GCP side length, likely `0.60 m`.

The initial implementation should not modify `micasense_georef.py`. It should import
the existing georeferencing functions where possible, or live as a new v2 script if the
workflow needs its own CLI.

## Core Decision

Use manual corner marking first. It is the cleanest way to answer whether the
georeferencing math produces realistic GCP dimensions. Use `matplotlib` for the
interactive point picker and overlay, so the user can click corners directly on the
generated georeferenced image and immediately see the selected pixel coordinates.
Automatic "purple GCP" detection can come later, because a single MicaSense band is
usually grayscale and does not preserve enough color information to identify purple
reliably.

The primary workflow is:

1. Pass a raw MicaSense `.tif` path to `gcp_dimension_check_v2.py`.
2. The script builds a north-up georeferenced raster view in memory using the existing
   camera model and flat-ground georeferencing math.
3. The script opens that generated view in a `matplotlib` editor.
4. The user clicks the four GCP corners in the editor.
5. The script reports editor pixel coordinates, real-world coordinates, side lengths,
   diagonals, and four-point polygon area.

By default, the v2 script should not write a GeoTIFF to disk. Saving the georeferenced
image, overlay, corners, or JSON report can be optional flags for reproducibility.

## Proposed Files

- `gcp_dimension_check_v2.py`
  - CLI entry point for loading an image, reading or collecting four corner pixels,
    converting them to world coordinates, and printing/saving the measurement report.
  - Builds the georeferenced raster in memory and opens it in a `matplotlib` editor by
    default.
  - Uses `matplotlib` for interactive corner picking and optional overlay output.
- `plans/gcp_dimension_check_v2_plan.md`
  - This plan.
- Optional later split, if the v2 script grows:
  - `georef_sources.py` for pixel-to-world providers.
  - `gcp_corners.py` for corner input, interactive picking, ordering, and validation.
  - `gcp_measurement.py` for distance, diagonal, area, and error calculations.
  - `gcp_report.py` for console, JSON, CSV, and overlay output.

## SRP Boundaries

Keep each responsibility narrow:

- Georeference source
  - One object/function should answer only: "Given pixel `(u, v)`, what are world
    coordinates `(X, Y)`?"
  - Primary source: in-memory georeferenced raster view with affine transform and CRS.
  - Raw MicaSense input: imports `load_model`, `distort`, and related math from
    `micasense_georef.py` to build the in-memory georeferenced view.
  - Optional saved GeoTIFF input: uses the raster affine transform and CRS.
- Corner collection
  - Reads four corners from CLI args, JSON, CSV, or a `matplotlib` interactive picker.
  - Shows the clicked point labels and pixel coordinates while selecting points.
  - Does not compute geospatial distances.
- Corner validation/order
  - Ensures exactly four finite points.
  - Either requires clockwise click order or sorts around the centroid.
  - Warns if the polygon self-intersects.
- Measurement
  - Converts four pixel points to four world points through the selected georef source.
  - Computes side distances, diagonals, area, side mean, side standard deviation, and
    error against expected GCP size.
  - Contains no image display or file-writing code.
- Reporting
  - Prints concise tables for point coordinates, distances, and area.
  - Optionally writes JSON/CSV and a `matplotlib` image overlay.
  - Does not own measurement math.

## Primary Georeferenced Editor Workflow

The normal v2 flow should start from a raw MicaSense band such as `IMG_0202_1.tif`,
create the georeferenced view in memory, and open the editor immediately.

- Required input:
  - raw `.tif` image path,
  - `--ground-elev`,
  - optional `--gsd`, defaulting to the current georeferencing default.
- Implementation:
  - `model = load_model(image_path)`
  - Build the same north-up orthorectified raster that `georeference_to_geotiff`
    currently writes, but return `(array, transform, crs, bounds)` instead of writing a
    file.
  - Display the in-memory raster with `matplotlib`.
  - Collect four clicked editor pixels.
  - Convert clicked editor pixels to `(X, Y)` using the in-memory raster's affine
    transform and CRS.
- Reason:
  - The user wants the script to "make it a GeoTIFF" conceptually, then load the
    editor, not produce a GeoTIFF file as the main output.
  - The clicked coordinates should correspond to the georeferenced editor view, so the
    transform from that generated view is the right pixel-to-world source.

This can be implemented without modifying `micasense_georef.py` by adding a v2 helper
that reuses the same logic as `georeference_to_geotiff`, but stops before the
`rasterio.open(..., "w")` write step.

## Optional Replay/Input Modes

Support these after the primary editor workflow works:

- Saved corner replay:
  - Load previously clicked editor-view corners from JSON.
  - Rebuild the same in-memory georeferenced view using the same raw input,
    `--ground-elev`, and `--gsd`.
  - Recompute coordinates and measurements without opening the editor.
- Already georeferenced GeoTIFF input:
  - Read `src.transform` and `src.crs` with `rasterio`.
  - Open the saved GeoTIFF directly in the `matplotlib` editor.
  - Convert marked pixel coordinates to map coordinates using the saved raster
    transform.
  - If the CRS is projected in metres, Euclidean distances are direct.
  - If the CRS is geographic degrees, transform points to an appropriate UTM CRS before
    computing metres.

## Pixel Coordinate Convention

Document this clearly in the CLI help and report:

- `x` is editor image column / horizontal pixel coordinate.
- `y` is editor image row / vertical pixel coordinate.
- In the primary workflow, these are pixels in the generated georeferenced raster view,
  not pixels in the original raw MicaSense frame.
- Interactive clicks may be floating-point values for subpixel corner placement.
- For displayed image coordinates, integer `(x, y)` should mean the centre of that
  displayed pixel.
- For raster affine transforms, adapt this convention consistently, because a raster
  transform normally maps pixel grid corners; centre-index clicks usually need a
  `+0.5` offset before applying the affine.
- The report should label these as `editor_pixel_x` and `editor_pixel_y` to avoid
  confusion with raw sensor pixel coordinates.

## CLI Shape

Primary interactive workflow:

```bash
python gcp_dimension_check_v2.py IMG_0202_1.tif \
  --ground-elev 481.5 \
  --gsd 0.012 \
  --expected-size 0.60
```

The script should georeference the input in memory, open the `matplotlib` editor, and
print the measurement report after the fourth point is selected.

Replay saved editor-view corners without opening the editor:

```bash
python gcp_dimension_check_v2.py IMG_0202_1.tif \
  --ground-elev 481.5 \
  --gsd 0.012 \
  --corners-json results/IMG_0202_gcp_corners.json \
  --expected-size 0.60 \
  --no-editor \
  --report-json results/IMG_0202_gcp_measurement.json
```

Optional saved artifacts:

```bash
python gcp_dimension_check_v2.py IMG_0202_1.tif \
  --ground-elev 481.5 \
  --save-corners results/IMG_0202_gcp_corners.json \
  --overlay results/IMG_0202_gcp_overlay.png \
  --save-geotiff results/IMG_0202_gcp_editor_view.tif
```

The implementation should add `matplotlib` to `environment.yml` because interactive
clicking and visual overlays are part of v2, not a stretch feature.

Saved GeoTIFF input can be a secondary path:

```bash
python gcp_dimension_check_v2.py IMG_0202_1_utm.tif \
  --input-mode geotiff \
  --expected-size 0.60
```

## Corner Input Format

Use a simple JSON schema:

```json
{
  "image": "IMG_0202_1.tif",
  "coordinate_space": "generated_georeferenced_editor_view",
  "ground_elev": 481.5,
  "gsd": 0.012,
  "order": "clockwise",
  "points": [
    {"name": "p1", "x": 1012.4, "y": 773.2},
    {"name": "p2", "x": 1065.1, "y": 770.8},
    {"name": "p3", "x": 1068.0, "y": 824.6},
    {"name": "p4", "x": 1014.9, "y": 827.3}
  ]
}
```

Default click order should be clockwise or counter-clockwise around the GCP perimeter.
If arbitrary order is allowed, the code should sort by angle around the centroid and
state that it reordered the points in the report.

Corner JSON should store editor-view pixel coordinates. If we later need original raw
sensor pixel coordinates too, add them as a separate optional field instead of
overloading `x` and `y`.

## Measurement Report

Console output should include:

- Input image path.
- Input mode: raw MicaSense source or saved GeoTIFF source.
- Editor view: in-memory georeferenced raster unless `--input-mode geotiff` is used.
- CRS/EPSG when available.
- Ground elevation and GSD for raw MicaSense input.
- Point table with one row per corner:
  - point name,
  - editor pixel `x`,
  - editor pixel `y`,
  - real-world `X/E`,
  - real-world `Y/N`.
- Side distances:
  - `p1 -> p2`
  - `p2 -> p3`
  - `p3 -> p4`
  - `p4 -> p1`
- Diagonal distances:
  - `p1 -> p3`, expected about `0.8485 m` for a `0.60 m` square.
  - `p2 -> p4`, expected about `0.8485 m` for a `0.60 m` square.
- Summary:
  - Mean side length.
  - Minimum and maximum side length.
  - Error from expected side length in metres and centimetres.
  - Percent error.
  - Area of the four-point polygon using the shoelace formula, expected about
    `0.36 m^2` for a `0.60 m` square.
  - Area error from expected square area in `m^2` and percent.

Example:

```text
GCP dimension check
image: IMG_0202_1.tif
input: raw MicaSense, editor_view=in-memory georeferenced raster
EPSG:32613, ground_elev=481.50 m, gsd=0.012 m/px
expected side: 0.600 m

point  editor_pixel_x  editor_pixel_y  world_x_E        world_y_N
p1     1012.4          773.2           412345.123       5522334.456
p2     1065.1          770.8           412345.721       5522334.432
p3     1068.0          824.6           412345.748       5522333.835
p4     1014.9          827.3           412345.151       5522333.812

side      distance_m  error_cm
p1-p2     0.598       -0.2
p2-p3     0.598       -0.2
p3-p4     0.598       -0.2
p4-p1     0.644        4.4

mean side: 0.609 m
max abs error: 4.4 cm
area: 0.364 m^2
area error: 0.004 m^2 (1.1%)
```

## Implementation Phases

### Phase 1: Measurement, Picker, and Details

- Create `gcp_dimension_check_v2.py`.
- Add `matplotlib` to `environment.yml`.
- Accept a raw MicaSense `.tif` image path as the main positional argument.
- Require `--ground-elev` for raw MicaSense input and accept optional `--gsd`.
- Build the georeferenced raster view in memory instead of writing a GeoTIFF by
  default.
- Open the in-memory georeferenced raster in a `matplotlib` editor immediately.
- Collect four clicked editor-view corners.
- Convert editor-view pixels to real-world coordinates using the generated affine
  transform.
- Compute and print editor pixel coordinates, real-world coordinates, side distances,
  diagonals, polygon area, and expected-size errors.
- Accept replayed corners through `--corners-json --no-editor`.
- Write optional JSON report.
- Save optional overlay image showing the clicked corners, point labels, perimeter, and
  side length labels.
- Save an optional GeoTIFF only when `--save-geotiff` is provided.

This phase directly answers the instructor's check and preserves
`micasense_georef.py`.

### Phase 2: Usability Polish

- Add keyboard shortcuts for accepting, undoing, and clearing clicked points.
- Show a small `matplotlib` detail panel or figure title with the current point label,
  pixel coordinate, real-world coordinate after conversion, and polygon area after four
  points are selected.
- Save clicked corners to JSON when the editor is used and `--save-corners` is
  provided.
- Add optional `--input-mode geotiff` for opening an already saved georeferenced TIFF
  directly in the editor.

### Phase 3: Optional Purple GCP Assist

- Only attempt automatic purple detection when the input has RGB/multiband color or a
  suitable color composite is provided.
- Keep this as an assist, not the source of truth:
  - propose a polygon/mask,
  - let the user adjust or confirm corners,
  - then run the same measurement code.
- Do not add auto-detection to the measurement function.

### Phase 4: Tests and Docs

- Add small tests for pure measurement functions:
  - a perfect `0.60 m x 0.60 m` square,
  - a rotated square,
  - a non-square quadrilateral,
  - arbitrary click order if auto-ordering is implemented.
- Add an affine-transform test for GeoTIFF mode using synthetic coordinates.
- Add a smoke-test command to `README.md` once the script exists.

## Acceptance Criteria

- `micasense_georef.py` remains unchanged for the first implementation.
- Passing a raw `.tif` path opens a `matplotlib` editor showing an in-memory
  georeferenced raster view.
- No GeoTIFF is written by default.
- Four marked pixels produce four finite world coordinates.
- Interactive marking works through `matplotlib`.
- The displayed/report point table includes editor-view pixel coordinates and
  real-world coordinates for each corner.
- The report prints all four side distances and compares them to `--expected-size`
  defaulting to `0.60 m`.
- The report prints the four-point polygon area and compares it to
  `expected_size ** 2`.
- The report makes it obvious whether distances are close to the GCP size.
- Raw MicaSense input uses the same orthorectification math as
  `georeference_to_geotiff`, but returns the generated raster and transform in memory.
- Pixel-to-world conversion for clicked corners uses the generated raster transform and
  records the CRS.
- Corner JSON and measurement JSON are reproducible enough to send to the instructor.

## Main Risks

- Wrong ground elevation in raw input changes the scale and position of the result.
- Standard GNSS metadata can have metre-level absolute offset, although side lengths
  should still be a useful scale check if pose/elevation are reasonable.
- A GCP that is not on the assumed ground plane will produce biased dimensions.
- Editor-view pixel corners are not interchangeable with pixel corners marked on the
  original raw image.
- Single-band imagery cannot reliably detect "purple" automatically; manual marking is
  the correct first validation path.
