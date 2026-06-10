# MicaSense single-image georeferencing

Direct georeferencing of a **single** MicaSense (Altum-PT / RedEdge-P) multispectral
frame over flat ground — no second image, no calibration panel, and no ground control
points. Under a planar-ground assumption (well justified for agricultural fields) the
map from an image pixel to a ground coordinate `(E, N)` is an *exact* 3×3 projective
homography. This tool reads the camera model and pose from the image metadata, builds
that homography, and can warp the band to a north-up GeoTIFF.

*Developed by Ali Lotfi, in collaboration with Claude Opus 4.8.*

## What it does

- Reads each band's own intrinsics, lens distortion, attitude (yaw/pitch/roll), and GPS
  directly from the file's XMP/EXIF.
- Builds the world→camera rotation through the NED→ENU convention and the camera
  projection centre in the correct UTM zone (auto-selected from the GPS).
- Provides `pixel_to_ground`, `ground_to_pixel`, and the closed-form flat-ground
  homography `H = K[r1 r2 t]`.
- Optionally warps the band to a north-up georeferenced GeoTIFF (single-image
  ortho-on-plane).
- Self-checks the homography against the rigorous ray-cast (exact for a plane) and warns
  when GNSS-accuracy sentinels indicate a ground/panel capture.

## Install

```bash
pip install -r requirements.txt
```

`rasterio` is only required if you write a GeoTIFF (`--out`). The math/homography path
needs only `numpy`, `pyproj`, `Pillow`, and `tifffile`.

## Usage

Print the homography and the self-checks:

```bash
python micasense_georef.py IMG_0010_4.tif --ground-elev 481.5
```

Write a north-up GeoTIFF:

```bash
python micasense_georef.py IMG_0010_4.tif --ground-elev 481.5 --gsd 0.012 --out out.tif
```

- `--ground-elev` — field elevation in metres ASL (from a DEM). This is the one external
  input; it sets the footprint's absolute scale and position.
- `--gsd` — output pixel size in metres (default 0.012).
- `--out` — output GeoTIFF path (optional).

The functions are also importable:

```python
from micasense_georef import load_model, pixel_to_ground, flat_ground_homography
m = load_model("IMG_0010_4.tif")
E, N = pixel_to_ground(m, u=1032, v=772, ground_elev=481.5)   # UTM
H_utm, H_local = flat_ground_homography(m, ground_elev=481.5)
```

## How it works

See `georef_explainer.pdf` (2 pages) for the derivation, including the collinearity ray /
plane intersection that yields `(E, N)` and the equivalent homography form. The `.tex`
source is included.

## Notes and limitations

- **Run per band, after band alignment.** Each band has its own focal length, principal
  point, distortion, and physical position on the sensor head (`RigTranslations`, up to
  ~48 mm apart). The script uses each band's own intrinsics but places its camera at the
  shared GPS position; it does not apply the per-band rig translations/relatives. For a
  co-registered multispectral stack, align the bands in image space first (e.g. the
  MicaSense ECC workflow) and georeference the aligned stack once.
- **Multispectral bands only.** The panchromatic (`_6`) and thermal (`_7`) bands have
  different geometry and need their own treatment.
- **Absolute accuracy is GNSS-limited** (~1–2 m for a standard fix). The homography is
  geometrically exact; absolute position is removable error only via RTK/PPK or a GCP.
- **Canopy parallax.** At low flight heights a tall, height-variable canopy behaves like
  relief and adds decimetre-scale offset near the frame edges; it vanishes toward nadir.
  A uniform field slope is harmless (a homography handles any tilted plane).
