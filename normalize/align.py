"""Align a directory of not-quite-fixed-position photos (e.g. drone shots)
into a common frame so they can be cut into a smooth timelapse.

Unlike capture/archive.py, this is a batch build-time step, not a capture-time
one: it reads a directory of already-taken photos and writes normalized
frames to an output directory. Nothing here talks to a network or an AI
model — alignment is classical feature matching (ORB) plus a similarity
transform (rotation/scale/translation), run entirely locally with OpenCV.

Photos are processed in EXIF capture-time order (not filename order), and
any photo that doesn't match the reference closely enough (see min_matches
on estimate_alignment/normalize_sequence) is skipped rather than forced into
the sequence — so a directory doesn't need to be manually sorted down to
just the matching photos first.
"""

import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import ExifTags, Image

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# normalize_sequence writes this alongside the aligned frames: a
# filename -> ISO 8601 capture-timestamp map. cv2.imwrite (used below to
# write warped/cropped frames) strips EXIF, so this is the only place the
# original capture time survives normalization — the video builder reads it
# to compute real-time gaps between frames for proportional-duration
# timelapses.
MANIFEST_FILENAME = "manifest.json"

# Similarity transform (rotation + uniform scale + translation), not a full
# projective homography: drone frames are slightly shifted/tilted/zoomed
# versions of roughly the same shot, not wildly different viewing angles, so
# a homography would over-fit and risk keystone-distorting the frame.
IDENTITY_MATRIX = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)

EXIF_DATETIME_FORMAT = "%Y:%m:%d %H:%M:%S"

# estimateAffinePartial2D needs at least a few point pairs to even attempt a
# fit; this is a floor below RANSAC, not the "does this belong" threshold
# (min_matches, checked against the RANSAC inlier count below, is).
MIN_CANDIDATE_MATCHES = 4


def capture_time(path):
    """The photo's EXIF capture time (DateTimeOriginal, falling back to the
    DateTime tag), or the file's mtime if it has no EXIF data at all.
    """
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            raw = exif_ifd.get(36867) or exif_ifd.get(36868) or exif.get(306)
        if raw:
            return datetime.strptime(raw, EXIF_DATETIME_FORMAT)
    except Exception:
        pass
    return datetime.fromtimestamp(Path(path).stat().st_mtime)


def list_images(input_dir):
    """Every photo in input_dir, in EXIF capture-time order (not filename
    order — drone photo filenames aren't necessarily chronological).

    Ties in capture_time (identical EXIF timestamps, or several no-EXIF
    photos copied in the same batch with equal mtimes) break by filename, so
    the order is deterministic across runs and platforms rather than
    depending on directory-iteration order.
    """
    paths = [p for p in Path(input_dir).iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS]
    return sorted(paths, key=lambda p: (capture_time(p), p.name))


def estimate_alignment(reference_gray, target_gray, min_matches=10):
    """Estimate the similarity transform mapping target_gray onto
    reference_gray, and how many feature matches actually agree with it.

    Returns (matrix, inlier_count). matrix is None if target_gray doesn't
    match reference_gray well enough to trust — either too few candidate
    matches to attempt a fit, or too few of them agree on one consistent
    transform (an unrelated photo will produce mostly-inconsistent matches,
    which RANSAC then discards as outliers). min_matches is checked against
    that inlier count, not the raw candidate-match count, which is what
    makes it a real "does this photo belong to this sequence" threshold
    rather than just a feature-richness check: raise it to be more strict
    about excluding photos that don't clearly match the reference, lower it
    to be more lenient.
    """
    orb = cv2.ORB_create(nfeatures=2000)
    ref_kp, ref_desc = orb.detectAndCompute(reference_gray, None)
    tgt_kp, tgt_desc = orb.detectAndCompute(target_gray, None)
    # knnMatch(k=2) returns at most `len(ref_desc)` neighbors per query — if
    # the reference has only one descriptor, every match comes back with a
    # single neighbor instead of two, and the unpacking below would raise.
    # A single-descriptor reference can't produce a trustworthy match anyway.
    if ref_desc is None or tgt_desc is None or len(ref_desc) < 2:
        return None, 0

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(tgt_desc, ref_desc, k=2)
    good = [m for m, n in matches if m.distance < 0.75 * n.distance]
    if len(good) < MIN_CANDIDATE_MATCHES:
        return None, 0

    src_pts = np.float32([tgt_kp[m.queryIdx].pt for m in good])
    dst_pts = np.float32([ref_kp[m.trainIdx].pt for m in good])
    matrix, inlier_mask = cv2.estimateAffinePartial2D(src_pts, dst_pts, method=cv2.RANSAC)
    if matrix is None:
        return None, 0

    inliers = int(inlier_mask.sum())
    if inliers < min_matches:
        return None, inliers
    return matrix, inliers


def warp_to_reference(image, matrix, ref_shape):
    """Warp image into the reference frame's coordinate space, returning the
    warped image plus a mask of which pixels came from real image data (as
    opposed to the black border warping introduces).
    """
    height, width = ref_shape[:2]
    warped = cv2.warpAffine(image, matrix, (width, height), borderValue=0)
    ones = np.full(image.shape[:2], 255, dtype=np.uint8)
    mask = cv2.warpAffine(ones, matrix, (width, height), borderValue=0)
    return warped, mask


def common_crop_box(mask):
    """Find a rectangle that's valid (mask > 0) across every aligned frame.

    Greedily shrinks from whichever border currently has the fewest valid
    pixels until the remaining box is fully valid. Not necessarily the
    largest possible rectangle, but simple, deterministic, and guaranteed to
    terminate — good enough to keep every output frame free of black edges.
    """
    top, bottom = 0, mask.shape[0]
    left, right = 0, mask.shape[1]
    valid = mask > 0

    while top < bottom and left < right:
        region = valid[top:bottom, left:right]
        if region.all():
            break
        counts = {
            "top": region[0, :].sum(),
            "bottom": region[-1, :].sum(),
            "left": region[:, 0].sum(),
            "right": region[:, -1].sum(),
        }
        worst = min(counts, key=counts.get)
        if worst == "top":
            top += 1
        elif worst == "bottom":
            bottom -= 1
        elif worst == "left":
            left += 1
        else:
            right -= 1

    return top, left, bottom, right


def normalize_sequence(input_dir, output_dir, reference=None, min_matches=10, output_size=None):
    """Align every photo in input_dir onto a reference frame, crop all of
    them to the region they have in common, and write the results to
    output_dir under their original filenames.

    input_dir can contain unrelated photos mixed in with the ones that
    belong to this sequence — anything that doesn't match the reference
    well enough (fewer than min_matches RANSAC-consistent feature matches)
    is left out of the output and reported in "skipped" instead, so the
    directory doesn't need to be sorted by hand first.

    Returns a report dict: {"aligned": [...], "skipped": [(name, reason)],
    "crop_box": (top, left, bottom, right)}.
    """
    images = list_images(input_dir)
    if not images:
        raise ValueError(f"no images found in {input_dir}")

    reference_path = Path(reference).resolve() if reference else images[0].resolve()
    reference_bgr = cv2.imread(str(reference_path))
    if reference_bgr is None:
        raise ValueError(f"could not read reference image {reference_path}")
    reference_gray = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY)
    ref_shape = reference_bgr.shape

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    aligned = []
    skipped = []
    timestamps = {}
    intersection_mask = None

    for image_path in images:
        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            skipped.append((image_path.name, "unreadable"))
            continue

        if image_path.resolve() == reference_path:
            matrix = IDENTITY_MATRIX
        else:
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            matrix, inliers = estimate_alignment(reference_gray, gray, min_matches=min_matches)
            if matrix is None:
                skipped.append(
                    (
                        image_path.name,
                        f"only {inliers} matching feature(s) agree with the reference "
                        f"(need {min_matches}) — likely doesn't belong to this sequence",
                    )
                )
                continue

        warped, mask = warp_to_reference(image_bgr, matrix, ref_shape)
        intersection_mask = (
            mask if intersection_mask is None else cv2.bitwise_and(intersection_mask, mask)
        )
        cv2.imwrite(str(output_dir / image_path.name), warped)
        aligned.append(image_path.name)
        timestamps[image_path.name] = capture_time(image_path)

    if not aligned:
        raise ValueError("no frames could be aligned")

    top, left, bottom, right = common_crop_box(intersection_mask)
    if top >= bottom or left >= right:
        raise ValueError(
            "aligned frames share no common region — try a different --reference "
            "frame or a lower --min-matches"
        )

    for name in aligned:
        path = output_dir / name
        frame = cv2.imread(str(path))
        cropped = frame[top:bottom, left:right]
        if output_size is not None:
            cropped = cv2.resize(cropped, output_size, interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(path), cropped)

    manifest = {name: timestamps[name].isoformat() for name in aligned}
    (output_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))

    return {
        "aligned": aligned,
        "skipped": skipped,
        "crop_box": (top, left, bottom, right),
    }
