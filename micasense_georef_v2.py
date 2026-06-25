#!/usr/bin/env python3
"""
micasense_georef.py
===================
Developed by Ali Lotfi, in collaboration with Claude Opus 4.8.

Direct georeferencing of a SINGLE MicaSense (Altum-PT / RedEdge-P) multispectral
frame over flat ground -- no second image, no calibration panel, no GCP.

Under a planar-ground assumption (well justified for agricultural fields) the map
from a pixel to a ground coordinate (E, N) is an *exact* 3x3 projective homography.
This script reads the camera model + pose from the image metadata, builds that
homography, and (optionally) warps the band to a north-up GeoTIFF.

Everything needed is in the file:
  * intrinsics  -> Perspective Focal Length, Principal Point, FocalPlaneXResolution
  * distortion  -> Perspective Distortion  (Brown: k1,k2,k3,p1,p2)
  * pose        -> Yaw (DLS/Irradiance heading), Pitch, Roll (camera)
                   +  GPS Latitude/Longitude/Altitude

The ONE external input is the ground elevation under the frame (--ground-elev),
which sets the absolute scale and position of the footprint.  It must be an
orthometric (MSL / DEM) elevation: the GPSAltitude in the metadata is a WGS84
*ellipsoid* height and is converted to orthometric internally (EGM96), so the
two are compared in the same vertical datum.  If the AGL is known directly
(e.g. from the flight plan), pass --agl instead and skip the datum question.

Usage
-----
    python micasense_georef.py IMG_0010_4.tif --ground-elev 481.5
    python micasense_georef.py IMG_0010_4.tif --ground-elev 481.5 \
           --gsd 0.012 --out IMG_0010_NIR_utm.tif

Dependencies: numpy, pyproj, Pillow, tifffile, (rasterio only if --out is used).
Validated on the 5 multispectral bands (_1.._5). The panchromatic (_6) and
thermal (_7) bands have different geometry and need their own intrinsics.
"""
import argparse, re
import numpy as np
from PIL import Image
from PIL.ExifTags import GPSTAGS
from pyproj import Transformer


# ----------------------------------------------------------------------------
# 1. Metadata extraction
# ----------------------------------------------------------------------------
def _xmp(raw: bytes) -> str:
    i, j = raw.find(b"<x:xmpmeta"), raw.find(b"</x:xmpmeta")
    if i == -1 or j == -1:
        raise ValueError("No XMP packet found -- not a MicaSense image?")
    return raw[i:j + 12].decode("utf-8", "ignore")


def _scalar(xmp: str, key: str) -> str:
    m = (re.search(rf'[A-Za-z]+:{key}="([^"]*)"', xmp) or
         re.search(rf'<[A-Za-z]+:{key}>([^<]*)<', xmp))
    if not m:
        raise ValueError(f"XMP tag {key} not found")
    return m.group(1)


def _seq(xmp: str, key: str):
    m = re.search(rf'{key}>\s*<rdf:Seq>(.*?)</rdf:Seq>', xmp, re.S)
    return [float(v) for v in re.findall(r'<rdf:li>([^<]+)</rdf:li>', m.group(1))]


def _heading_deg(xmp: str) -> float:
    """Camera heading (yaw), degrees, from the best available source.

    MicaSense frequently leaves ``Camera:Yaw`` at 0 (no gimbal/heading feedback
    on the camera itself) while the true platform heading is recorded as
    ``Camera:IrradianceYaw`` (deg) / ``DLS:Yaw`` (rad) -- the magnetometer-backed
    DLS heading used for irradiance correction. The sensor is rigidly mounted, so
    that heading *is* the camera heading. We take only the yaw from the DLS; the
    camera's own ``Pitch``/``Roll`` are kept (the DLS pitch/roll describe the
    light sensor's tilt, not the camera's, and must not be substituted).
    """
    try:
        return float(_scalar(xmp, "IrradianceYaw"))
    except ValueError:
        pass
    m = re.search(r'<DLS:Yaw>([^<]*)<', xmp)
    if m:
        return np.degrees(float(m.group(1)))
    return float(_scalar(xmp, "Yaw"))


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def ellipsoid_to_orthometric(lon: float, lat: float, h_ell: float):
    """WGS84 ellipsoid height -> orthometric (EGM96) height, in metres.

    MicaSense writes GPSAltitude as height above the WGS84 *ellipsoid*, while
    DEM ground elevations are orthometric (above the geoid / MSL).  Mixing the
    two biases the AGL by the geoid undulation (about -22 m on the Canadian
    prairies) and scales every ground distance by the same factor.

    Returns (orthometric_height, geoid_undulation N = h_ell - H).  Falls back
    to the input height (N = 0) with a warning if the geoid grid is missing.
    """
    try:
        t = Transformer.from_crs("EPSG:4979", "EPSG:9707", always_xy=True)
        h_orth = t.transform(lon, lat, h_ell)[2]
        if np.isfinite(h_orth) and h_orth != h_ell:
            return h_orth, h_ell - h_orth
    except Exception:
        pass
    print("WARNING: EGM96 geoid grid unavailable -- treating GPSAltitude as "
          "orthometric; give --ground-elev as ellipsoid height (or use --agl).")
    return h_ell, 0.0


def load_model(path: str) -> dict:
    """Read intrinsics, distortion, pose and GPS; build rotation + camera centre."""
    raw = open(path, "rb").read()
    xmp = _xmp(raw)

    f_mm = float(_scalar(xmp, "PerspectiveFocalLength"))
    ppx, ppy = (float(v) for v in _scalar(xmp, "PrincipalPoint").split(","))
    k1, k2, k3, p1, p2 = _seq(xmp, "PerspectiveDistortion")[:5]
    # Yaw (heading) comes from the DLS/Irradiance magnetometer, not Camera:Yaw,
    # which MicaSense often leaves at 0.  Pitch/Roll stay with the camera.  See
    # _heading_deg.
    pitch, roll = (np.radians(float(_scalar(xmp, k))) for k in ("Pitch", "Roll"))
    yaw = np.radians(_heading_deg(xmp))
    try:
        band = _scalar(xmp, "BandName")
    except ValueError:
        band = "?"
    def _opt(key):
        try:    return float(_scalar(xmp, key))
        except ValueError: return None
    gps_xy_acc, gps_z_acc = _opt("GPSXYAccuracy"), _opt("GPSZAccuracy")

    exif = Image.open(path).getexif()
    eifd = exif.get_ifd(0x8769)                      # Exif IFD
    res = float(eifd.get(0xA20E, 289.855072))        # FocalPlaneXResolution px/mm

    g = {GPSTAGS.get(k, k): v for k, v in exif.get_ifd(0x8825).items()}
    if "GPSLatitude" not in g:
        raise ValueError("No GPS in EXIF (is this a ground/panel capture?)")
    dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
    lat = dms(g["GPSLatitude"]);  lat = -lat if g.get("GPSLatitudeRef") == "S" else lat
    lon = dms(g["GPSLongitude"]); lon = -lon if g.get("GPSLongitudeRef") == "W" else lon
    alt_ell = float(g["GPSAltitude"])
    if g.get("GPSAltitudeRef", b"\x00") in (b"\x01", 1):
        alt_ell = -alt_ell
    alt, geoid_N = ellipsoid_to_orthometric(lon, lat, alt_ell)

    # intrinsic matrix K (OpenCV convention: x right, y down, z forward; top-left origin)
    fx = fy = f_mm * res
    cx, cy = ppx * res, ppy * res
    K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])

    # exterior orientation: camera(OpenCV) -> world ENU (= UTM E,N,U)
    #   R_cb : camera -> body NED (x fwd, y right, z down), look-down mount
    #   C_nb : body  -> navigation NED   (Baumker:  Rz(yaw) Ry(pitch) Rx(roll))
    #   C_en : NED   -> ENU swap
    cy_, sy_ = np.cos(yaw), np.sin(yaw)
    cp_, sp_ = np.cos(pitch), np.sin(pitch)
    cr_, sr_ = np.cos(roll), np.sin(roll)
    C_nb = np.array([
        [cy_ * cp_, cy_ * sp_ * sr_ - sy_ * cr_, cy_ * sp_ * cr_ + sy_ * sr_],
        [sy_ * cp_, sy_ * sp_ * sr_ + cy_ * cr_, sy_ * sp_ * cr_ - cy_ * sr_],
        [    -sp_,                   cp_ * sr_,                   cp_ * cr_],
    ])
    R_cb = np.array([[0., -1, 0], [1, 0, 0], [0, 0, 1]])
    C_en = np.array([[0., 1, 0], [1, 0, 0], [0, 0, -1]])
    R_c2w = C_en @ C_nb @ R_cb           # camera -> world
    R_w2c = R_c2w.T                      # world  -> camera

    epsg = utm_epsg(lon, lat)
    E0, N0 = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}",
                                  always_xy=True).transform(lon, lat)

    img = Image.open(path)
    return dict(path=path, band=band, W=img.width, H=img.height,
                K=K, fx=fx, fy=fy, cx=cx, cy=cy, dist=(k1, k2, k3, p1, p2),
                R_c2w=R_c2w, R_w2c=R_w2c, C=np.array([E0, N0, alt]),
                E0=E0, N0=N0, alt=alt, alt_ell=alt_ell, geoid_N=geoid_N,
                epsg=epsg, lat=lat, lon=lon,
                gps_xy_acc=gps_xy_acc, gps_z_acc=gps_z_acc)


# ----------------------------------------------------------------------------
# 2. Lens distortion (Brown) -- forward and iterative inverse, normalised coords
# ----------------------------------------------------------------------------
def distort(model, x, y):
    k1, k2, k3, p1, p2 = model["dist"]
    r2 = x * x + y * y
    rad = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 ** 3
    xd = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return xd, yd


def undistort(model, xd, yd, iters=20):
    k1, k2, k3, p1, p2 = model["dist"]
    x, y = np.array(xd, float), np.array(yd, float)
    for _ in range(iters):
        r2 = x * x + y * y
        rad = 1 + k1 * r2 + k2 * r2 * r2 + k3 * r2 ** 3
        x = (xd - (2 * p1 * x * y + p2 * (r2 + 2 * x * x))) / rad
        y = (yd - (p1 * (r2 + 2 * y * y) + 2 * p2 * x * y)) / rad
    return x, y


# ----------------------------------------------------------------------------
# 3. Pixel <-> ground (the collinearity ray, intersected with plane Z=ground)
# ----------------------------------------------------------------------------
def pixel_to_ground(model, u, v, ground_elev):
    """Raw pixel -> (E, N) in UTM, by ray-plane intersection."""
    xi, yi = undistort(model, (u - model["cx"]) / model["fx"],
                              (v - model["cy"]) / model["fy"])
    d = model["R_c2w"] @ np.array([xi, yi, 1.0])
    t = (ground_elev - model["C"][2]) / d[2]
    P = model["C"] + t * d
    return P[0], P[1]


def ground_to_pixel(model, E, N, ground_elev):
    """(E, N) on the ground plane -> raw pixel (u, v)."""
    Xc = model["R_w2c"] @ (np.array([E, N, ground_elev]) - model["C"])
    x, y = Xc[0] / Xc[2], Xc[1] / Xc[2]
    xd, yd = distort(model, x, y)
    return model["fx"] * xd + model["cx"], model["fy"] * yd + model["cy"]


# ----------------------------------------------------------------------------
# 4. Closed-form flat-ground homography  H = K [r1 r2 t]
#    Maps an *ideal* (undistorted) pixel to UTM (E, N). Exact for a plane.
# ----------------------------------------------------------------------------
def flat_ground_homography(model, ground_elev):
    agl = model["C"][2] - ground_elev
    r1, r2, r3 = model["R_w2c"][:, 0], model["R_w2c"][:, 1], model["R_w2c"][:, 2]
    H_img_from_local = model["K"] @ np.column_stack([r1, r2, -agl * r3])
    H_local_from_img = np.linalg.inv(H_img_from_local)
    H_local_from_img /= H_local_from_img[2, 2]
    # local ground (origin at nadir) -> UTM: add the camera's planimetric position
    T = np.array([[1, 0, model["E0"]], [0, 1, model["N0"]], [0, 0, 1.0]])
    H_utm_from_img = T @ H_local_from_img
    return H_utm_from_img / H_utm_from_img[2, 2], H_local_from_img


def apply_homography(H, u, v):
    p = H @ np.array([u, v, 1.0])
    return p[0] / p[2], p[1] / p[2]


# ----------------------------------------------------------------------------
# 5. Optional: warp the band to a north-up GeoTIFF (single-image ortho-on-plane)
# ----------------------------------------------------------------------------
def georeference_to_geotiff(model, ground_elev, out_path, gsd=0.012):
    import tifffile, rasterio
    from rasterio.transform import from_origin

    img = tifffile.imread(model["path"]).astype(np.float32)
    if img.ndim == 3:
        img = img[..., 0]
    H, W = img.shape

    corners = np.array([pixel_to_ground(model, u, v, ground_elev)
                        for u, v in [(0, 0), (W, 0), (W, H), (0, H)]])
    Emin, Emax = corners[:, 0].min(), corners[:, 0].max()
    Nmin, Nmax = corners[:, 1].min(), corners[:, 1].max()
    nx, ny = int((Emax - Emin) / gsd), int((Nmax - Nmin) / gsd)
    transform = from_origin(Emin, Nmax, gsd, gsd)

    EE, NN = np.meshgrid(Emin + (np.arange(nx) + 0.5) * gsd,
                         Nmax - (np.arange(ny) + 0.5) * gsd)
    P = np.stack([EE.ravel(), NN.ravel(), np.full(EE.size, ground_elev)], 1) - model["C"]
    Xc = P @ model["R_w2c"].T
    x, y = Xc[:, 0] / Xc[:, 2], Xc[:, 1] / Xc[:, 2]
    xd, yd = distort(model, x, y)
    us = (model["fx"] * xd + model["cx"]).reshape(ny, nx)
    vs = (model["fy"] * yd + model["cy"]).reshape(ny, nx)

    u0, v0 = np.floor(us).astype(int), np.floor(vs).astype(int)
    a, b = us - u0, vs - v0
    valid = (u0 >= 0) & (u0 < W - 1) & (v0 >= 0) & (v0 < H - 1)
    u0c, v0c = np.clip(u0, 0, W - 2), np.clip(v0, 0, H - 2)
    out = (img[v0c, u0c] * (1 - a) * (1 - b) + img[v0c, u0c + 1] * a * (1 - b) +
           img[v0c + 1, u0c] * (1 - a) * b + img[v0c + 1, u0c + 1] * a * b)
    out = np.where(valid, out, 0).astype(np.float32)

    with rasterio.open(out_path, "w", driver="GTiff", height=ny, width=nx, count=1,
                       dtype="float32", crs=f"EPSG:{model['epsg']}",
                       transform=transform, nodata=0, compress="deflate") as dst:
        dst.write(out, 1)
    return out_path, (nx, ny), (Emax - Emin, Nmax - Nmin)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Single-image georeferencing of a MicaSense frame (flat ground).")
    ap.add_argument("image", help="MicaSense band GeoTIFF (e.g. IMG_0010_4.tif)")
    ap.add_argument("--ground-elev", type=float,
                    help="ground elevation under the frame, m orthometric/MSL (from a DEM)")
    ap.add_argument("--agl", type=float,
                    help="camera height above ground, m (alternative to --ground-elev; "
                         "sidesteps vertical-datum issues entirely)")
    ap.add_argument("--gsd", type=float, default=0.012, help="output GSD, m/px (default 0.012)")
    ap.add_argument("--out", help="if given, write a north-up GeoTIFF here")
    args = ap.parse_args()
    if (args.ground_elev is None) == (args.agl is None):
        ap.error("give exactly one of --ground-elev or --agl")

    m = load_model(args.image)
    ground_elev = m["alt"] - args.agl if args.agl is not None else args.ground_elev
    agl = m["alt"] - ground_elev
    if (m["gps_xy_acc"] == 0) or (m["gps_z_acc"] is not None and m["gps_z_acc"] > 50):
        print("WARNING: GNSS-accuracy sentinels (XY={}, Z={}) suggest a ground/panel "
              "capture with no valid fix -- georeferencing will be meaningless."
              .format(m["gps_xy_acc"], m["gps_z_acc"]))
    print(f"band={m['band']}  size={m['W']}x{m['H']}  EPSG:{m['epsg']}")
    print(f"f={m['fx']:.2f}px  pp=({m['cx']:.1f},{m['cy']:.1f})  "
          f"camera UTM=({m['E0']:.2f},{m['N0']:.2f},{m['alt']:.2f})  AGL={agl:.2f} m")
    print(f"GPS ellipsoid height {m['alt_ell']:.2f} m -> orthometric {m['alt']:.2f} m "
          f"(geoid undulation N={m['geoid_N']:.2f} m)")

    H_utm, H_local = flat_ground_homography(m, ground_elev)
    np.set_printoptions(suppress=True, precision=6)
    print("\nflat-ground homography  H : ideal pixel [u,v,1] -> local ground [E,N,1] m (origin at nadir)")
    print(H_local)
    print(f"absolute UTM (E,N) = local (E,N) + ({m['E0']:.2f}, {m['N0']:.2f})")

    # self-check: homography vs rigorous ray-cast, and a round-trip
    W, Hh = m["W"], m["H"]
    err = []
    for u, v in [(0, 0), (W, 0), (W, Hh), (0, Hh), (W / 2, Hh / 2)]:
        gc = np.array(pixel_to_ground(m, u, v, ground_elev))
        xi, yi = undistort(m, (u - m["cx"]) / m["fx"], (v - m["cy"]) / m["fy"])
        ip = m["K"] @ np.array([xi, yi, 1.0])
        gh = np.array(apply_homography(H_utm, ip[0], ip[1]))
        err.append(np.linalg.norm(gc - gh))
    print(f"homography vs ray-cast (max over corners+centre) = {max(err) * 1000:.4f} mm  [exact for a plane]")
    uc, vc = ground_to_pixel(m, *pixel_to_ground(m, W / 2, Hh / 2, ground_elev), ground_elev)
    print(f"pixel round-trip at centre = {np.hypot(uc - W / 2, vc - Hh / 2):.4f} px")

    if args.out:
        out, (nx, ny), (we, hn) = georeference_to_geotiff(m, ground_elev, args.out, args.gsd)
        print(f"\nwrote {out}\nfootprint {we:.1f} x {hn:.1f} m  ->  {nx} x {ny} px @ {args.gsd*1000:.0f} mm")


if __name__ == "__main__":
    main()
