"""Align a directory of not-quite-fixed-position photos (e.g. drone shots)
into a common frame so they can be cut into a smooth timelapse.

Unlike capture/archive.py, this is a batch build-time step, not a capture-time
one: it reads a directory of already-taken photos and writes normalized
frames to an output directory. Nothing here talks to a network or an AI
model — alignment is classical computer vision, run entirely locally with
OpenCV, via one of two methods:

- "orb" (estimate_alignment): sparse feature matching -- detect ORB
  keypoints in each image, match them, fit a similarity transform to the
  matches that agree with each other (RANSAC). Fast, and the traditional
  choice, but keypoint detection needs locally distinctive texture to work
  from -- on scenes that are mostly repetitive low-texture surface (grass,
  dirt, pavement), the keypoint budget gets diluted by noisy texture
  keypoints and the sparser, more reliable structural ones (fences,
  building edges) can get crowded out.
- "ecc" (estimate_alignment_ecc): direct intensity-based alignment --
  optimizes an affine transform against the whole image's pixel gradients
  at once (OpenCV's findTransformECC), coarse-to-fine over an image
  pyramid for robustness. Doesn't depend on discrete keypoints at all, so
  it holds up much better on low-texture scenes and even survives large
  appearance changes (seasonal color shift, snow cover) as long as the
  underlying structure (fences, buildings) is still visible. Slower than
  ORB and the default for normalize_sequence.

Photos are processed in EXIF capture-time order (not filename order), and
any photo that doesn't match the reference closely enough (see min_matches/
min_confidence on normalize_sequence) is skipped rather than forced into the
sequence — so a directory doesn't need to be manually sorted down to just
the matching photos first.
"""

import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import ExifTags, Image

from normalize import control_points as control_points_module

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


def estimate_alignment(reference_gray, target_gray, min_matches=10, nfeatures=20000):
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

    nfeatures needs to be high on low-texture scenes (grass/dirt fields):
    ORB's default cap of ~2000 keypoints gets spread thin across repetitive
    grass texture, crowding out the sparser, more distinctive keypoints
    along structures like fence lines and building edges that actually
    carry reliable matches. 20000 is enough headroom for that to stop being
    the bottleneck on the drone-photo farmland shots this was built for.
    """
    orb = cv2.ORB_create(nfeatures=nfeatures)
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


# Working resolutions (pixels wide) for estimate_alignment_ecc's coarse-to-fine
# pyramid, coarsest first. ECC's gradient-descent optimizer can converge to the
# wrong local optimum if it starts too far from the true alignment; solving at
# a small, cheap resolution first and refining the result at each larger
# resolution keeps it out of that trap without paying full-resolution cost at
# every step. The final width is capped to the reference's own width in
# estimate_alignment_ecc, so this list is a ceiling, not a guarantee.
ECC_PYRAMID_WIDTHS = (200, 400, 800, 1600)

# estimate_alignment_ecc rejects a converged transform if its implied uniform
# scale falls outside this range: same-location drone photos shouldn't need
# extreme rescaling to line up, so a wildly small/large scale is a sign ECC
# converged on garbage rather than a real alignment (ECC has no built-in
# concept of "this pair doesn't match" the way RANSAC's inlier count does —
# findTransformECC always returns *some* transform, so this is the guard that
# plays min_matches's role for the ecc method).
ECC_MIN_SCALE = 1.0 / 3.0
ECC_MAX_SCALE = 3.0


def _resize_gray(gray, width, height):
    return cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)


def _plausible_affine(matrix):
    """Reject an affine matrix implying an unreasonable uniform scale --
    see ECC_MIN_SCALE/ECC_MAX_SCALE.
    """
    a, b, c, d = matrix[0, 0], matrix[0, 1], matrix[1, 0], matrix[1, 1]
    det = a * d - b * c
    if det <= 0:
        return False
    scale = det**0.5
    return ECC_MIN_SCALE <= scale <= ECC_MAX_SCALE


def estimate_alignment_ecc(
    reference_gray,
    target_gray,
    pyramid_widths=ECC_PYRAMID_WIDTHS,
    max_iters=200,
    eps=1e-6,
):
    """Estimate the affine transform mapping target_gray onto reference_gray
    using direct intensity-based alignment (ECC), instead of estimate_alignment's
    sparse feature matching.

    Returns (matrix, confidence). matrix is None if ECC failed to converge at
    all, or converged to a transform implausible enough to reject (see
    _plausible_affine) -- confidence (the ECC correlation coefficient, roughly
    0-1) is for min_confidence on normalize_sequence to threshold against, the
    role min_matches plays for estimate_alignment. Unlike an inlier count,
    confidence isn't a clean probability -- it also reflects appearance
    differences (season, lighting) having nothing to do with whether the
    geometry is aligned -- so treat a low but passing value as "probably fine,
    spot check it" rather than a precise score.

    reference_gray and target_gray don't need to be the same size or aspect
    ratio: each pyramid level resizes both to reference_gray's own aspect
    ratio (squashing target_gray if its aspect differs), which the affine
    model's independent x/y scale absorbs. The final matrix is expressed back
    in each image's own full native resolution, matching the coordinate space
    warp_to_reference expects.
    """
    ref_h, ref_w = reference_gray.shape[:2]
    tgt_h, tgt_w = target_gray.shape[:2]
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, max_iters, eps)

    warp = np.eye(2, 3, dtype=np.float32)
    prev_width = None
    confidence = 0.0

    for width in pyramid_widths:
        width = min(width, ref_w)
        height = max(1, round(width * ref_h / ref_w))
        ref_level = _resize_gray(reference_gray, width, height).astype(np.float32) / 255.0
        tgt_level = _resize_gray(target_gray, width, height).astype(np.float32) / 255.0

        if prev_width is not None:
            # Every level shares reference_gray's aspect ratio by construction
            # (only the working width changes), so carrying the warp forward
            # between levels is just a uniform rescale of its translation
            # component -- the rotation/scale part of the matrix is
            # resolution-independent.
            warp[:, 2] *= width / prev_width

        try:
            confidence, warp = cv2.findTransformECC(
                ref_level, tgt_level, warp, cv2.MOTION_AFFINE, criteria, None, 5
            )
        except cv2.error:
            return None, 0.0
        prev_width = width

    # cv2.findTransformECC's returned warp maps reference_gray's own content
    # forward onto target_gray (confirmed empirically: warping reference_gray
    # by warp reproduces target_gray) -- the opposite of what this function
    # returns and what warp_to_reference needs (target -> reference). Invert
    # it before doing anything else, including the working-space-to-full-
    # resolution conversion below.
    warp_homogeneous = np.linalg.inv(np.vstack([warp, [0, 0, 1]]))

    # Map from the final pyramid level's shared working space back to each
    # image's own full native resolution.
    final_width = prev_width
    final_height = max(1, round(final_width * ref_h / ref_w))
    scale_ref = np.diag([final_width / ref_w, final_height / ref_h, 1.0])
    scale_tgt = np.diag([final_width / tgt_w, final_height / tgt_h, 1.0])
    full_matrix = (np.linalg.inv(scale_ref) @ warp_homogeneous @ scale_tgt)[:2, :]
    full_matrix = full_matrix.astype(np.float32)

    if not _plausible_affine(full_matrix):
        return None, float(confidence)

    return full_matrix, float(confidence)


def warp_to_reference(image, matrix, ref_shape):
    """Warp image into the reference frame's coordinate space, returning the
    warped image plus a mask of which pixels came from real image data (as
    opposed to the black border warping introduces).

    matrix is either a 2x3 affine matrix (estimate_alignment,
    estimate_alignment_ecc) or a 3x3 homography (control_points.py's
    anchor-composed transforms, which chain a manually-verified homography
    with an automated affine estimate) -- dispatched to warpAffine or
    warpPerspective accordingly.
    """
    height, width = ref_shape[:2]
    warp_fn = cv2.warpPerspective if matrix.shape[0] == 3 else cv2.warpAffine
    warped = warp_fn(image, matrix, (width, height), borderValue=0)
    ones = np.full(image.shape[:2], 255, dtype=np.uint8)
    mask = warp_fn(ones, matrix, (width, height), borderValue=0)
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


def _compose_with_anchor(anchor_to_reference, photo_to_anchor):
    """Chain a photo's automated alignment to an anchor with that anchor's
    own manually-verified transform onto the reference, producing one
    photo -> reference matrix. anchor_to_reference is always 3x3 (a
    control_points.py homography); photo_to_anchor may be 2x3 (affine, from
    estimate_alignment/estimate_alignment_ecc) or already 3x3.
    """
    if photo_to_anchor.shape[0] == 2:
        photo_to_anchor = np.vstack([photo_to_anchor, [0, 0, 1]])
    return (anchor_to_reference @ photo_to_anchor).astype(np.float32)


def _best_candidate_alignment(
    gray, reference_gray, anchors, method, min_matches, min_confidence, nfeatures
):
    """Try aligning gray against the reference and each anchor in anchors,
    keeping whichever produces the best-scoring match, composed into the
    reference's coordinate space if an anchor won.

    anchors: {anchor_name: (anchor_gray, anchor_to_reference_matrix)} --
    built from control_points.py data, one entry per manually-annotated
    anchor. Aligning against the *nearest* anchor first (whichever the photo
    actually resembles most) rather than always the single global reference
    is what makes this more accurate than plain estimate_alignment/
    estimate_alignment_ecc on a batch spanning several distinct zoom/angle
    groupings: the automated step only has to correct the small remaining
    gap between the photo and its nearest anchor, and the anchor's own
    manually-verified transform (exact, since a person clicked it) carries
    the rest of the way.

    Returns (matrix, score, label) -- label is "reference" or an anchor
    name, whichever won. matrix/score/label are all None if nothing passed
    (mirrors estimate_alignment/estimate_alignment_ecc's None-on-failure).
    score is an ORB inlier count or an ECC confidence depending on method --
    only comparable to other scores from the same method, which is all this
    ever does with it.
    """
    candidates = [("reference", reference_gray, None)]
    candidates += [
        (name, anchor_gray, anchor_matrix) for name, (anchor_gray, anchor_matrix) in anchors.items()
    ]

    best = None
    for label, candidate_gray, anchor_matrix in candidates:
        if method == "orb":
            matrix, score = estimate_alignment(
                candidate_gray, gray, min_matches=min_matches, nfeatures=nfeatures
            )
            passed = matrix is not None
        else:
            matrix, score = estimate_alignment_ecc(candidate_gray, gray)
            passed = matrix is not None and score >= min_confidence
        if not passed:
            continue
        final_matrix = (
            matrix if anchor_matrix is None else _compose_with_anchor(anchor_matrix, matrix)
        )
        if best is None or score > best[0]:
            best = (score, label, final_matrix)

    if best is None:
        return None, None, None
    score, label, matrix = best
    return matrix, score, label


def normalize_sequence(
    input_dir,
    output_dir,
    reference=None,
    method="ecc",
    min_matches=10,
    min_confidence=0.2,
    output_size=None,
    nfeatures=20000,
    crop="intersection",
    control_points=None,
):
    """Align every photo in input_dir onto a reference frame and write the
    results to output_dir under their original filenames.

    method selects the alignment algorithm -- "ecc" (default, see
    estimate_alignment_ecc) or "orb" (see estimate_alignment). ecc is the
    better default for the low-texture aerial/farmland scenes this was
    built for; orb is faster and remains available for scenes with more
    distinctive texture, or as a point of comparison.

    crop selects how each frame's black border (introduced by warping it
    into the reference's coordinate space) is handled:
    - "intersection" (default): shrink every frame to the single rectangle
      that's valid across *all* aligned frames, so no output frame ever has
      a black edge. This is punishing at scale: each frame's own warp
      already loses some border on its own, and intersecting that loss
      across many frames compounds -- on a real 118-photo batch, one frame
      alone lost ~46% of its area to its own border, and the common region
      across all of them shrank to ~22% by the time all frames were
      combined. More frames (or a single more-tilted one) can shrink the
      kept region arbitrarily far.
    - "none": don't crop at all -- every output frame is the reference's
      full size, with genuine black wherever that particular frame's warp
      didn't reach. Keeps each frame's actual content instead of
      discarding most of it to guarantee a black-free rectangle; the
      tradeoff is a black border on frames whose alignment shifted/rotated
      enough to leave one.

    input_dir can contain unrelated photos mixed in with the ones that
    belong to this sequence — anything that doesn't match the reference
    well enough (fewer than min_matches RANSAC-consistent feature matches
    for "orb", below min_confidence for "ecc") is left out of the output and
    reported in "skipped" instead, so the directory doesn't need to be
    sorted by hand first.

    control_points (optional) is a normalize/control_points.py dict, or a
    path to one, holding manually-clicked anchor photos (see
    normalize/annotate.py). When given, every non-anchor photo is aligned
    automatically against the reference *and* every anchor, keeping
    whichever match scores best and composing it with that anchor's
    manually-verified transform -- more accurate on a batch spanning several
    distinct zoom/angle groupings than aligning everything against one
    global reference, since the automated step only has to close the
    (smaller) gap to the nearest anchor rather than the full gap to the
    reference. Anchor photos themselves use their control points' transform
    directly, skipping automated estimation entirely. Any photo listed in
    control_points's "rejected" (see control_points.reject) is excluded
    entirely, whether it would otherwise have been used as an anchor or just
    matched automatically -- for photos manually judged not to belong in the
    sequence at all, or too poor an angle/zoom to be worth aligning.

    Returns a report dict: {"aligned": [...], "skipped": [(name, reason)],
    "crop_box": (top, left, bottom, right)} -- crop_box is the full frame
    extent, (0, 0, height, width), when crop="none".
    """
    if method not in ("orb", "ecc"):
        raise ValueError(f"unknown method {method!r} -- expected 'orb' or 'ecc'")
    if crop not in ("intersection", "none"):
        raise ValueError(f"unknown crop {crop!r} -- expected 'intersection' or 'none'")

    images = list_images(input_dir)
    if not images:
        raise ValueError(f"no images found in {input_dir}")

    reference_path = Path(reference).resolve() if reference else images[0].resolve()
    reference_bgr = cv2.imread(str(reference_path))
    if reference_bgr is None:
        raise ValueError(f"could not read reference image {reference_path}")
    reference_gray = cv2.cvtColor(reference_bgr, cv2.COLOR_BGR2GRAY)
    ref_shape = reference_bgr.shape

    if control_points is not None:
        cp_data = (
            control_points
            if isinstance(control_points, dict)
            else control_points_module.load(control_points)
        )
        rejected = set(cp_data.get("rejected", []))
        anchors = {}
        for anchor_name in cp_data.get("anchors", {}):
            anchor_bgr = cv2.imread(str(Path(input_dir) / anchor_name))
            if anchor_bgr is None:
                raise ValueError(f"could not read anchor image {anchor_name} in {input_dir}")
            anchor_gray = cv2.cvtColor(anchor_bgr, cv2.COLOR_BGR2GRAY)
            anchors[anchor_name] = (
                anchor_gray,
                control_points_module.anchor_transform(cp_data, anchor_name),
            )
    else:
        rejected = set()
        anchors = {}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    aligned = []
    skipped = []
    timestamps = {}
    intersection_mask = None

    for image_path in images:
        if image_path.name in rejected:
            skipped.append((image_path.name, "manually marked as not belonging to this sequence"))
            continue

        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            skipped.append((image_path.name, "unreadable"))
            continue

        if image_path.resolve() == reference_path:
            matrix = IDENTITY_MATRIX
        elif image_path.name in anchors:
            matrix = anchors[image_path.name][1]
        elif anchors:
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            matrix, score, label = _best_candidate_alignment(
                gray, reference_gray, anchors, method, min_matches, min_confidence, nfeatures
            )
            if matrix is None:
                skipped.append(
                    (
                        image_path.name,
                        "no match against the reference or any anchor cleared "
                        f"{'min-matches' if method == 'orb' else 'min-confidence'} "
                        "— likely doesn't belong to this sequence",
                    )
                )
                continue
        elif method == "orb":
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            matrix, inliers = estimate_alignment(
                reference_gray, gray, min_matches=min_matches, nfeatures=nfeatures
            )
            if matrix is None:
                skipped.append(
                    (
                        image_path.name,
                        f"only {inliers} matching feature(s) agree with the reference "
                        f"(need {min_matches}) — likely doesn't belong to this sequence",
                    )
                )
                continue
        else:
            gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
            matrix, confidence = estimate_alignment_ecc(reference_gray, gray)
            if matrix is None or confidence < min_confidence:
                skipped.append(
                    (
                        image_path.name,
                        f"ecc confidence {confidence:.2f} below min-confidence "
                        f"{min_confidence:.2f} — likely doesn't belong to this sequence",
                    )
                )
                continue

        warped, mask = warp_to_reference(image_bgr, matrix, ref_shape)
        if crop == "intersection":
            intersection_mask = (
                mask if intersection_mask is None else cv2.bitwise_and(intersection_mask, mask)
            )
        cv2.imwrite(str(output_dir / image_path.name), warped)
        aligned.append(image_path.name)
        timestamps[image_path.name] = capture_time(image_path)

    if not aligned:
        raise ValueError("no frames could be aligned")

    if crop == "intersection":
        top, left, bottom, right = common_crop_box(intersection_mask)
        if top >= bottom or left >= right:
            raise ValueError(
                "aligned frames share no common region — try a different --reference "
                "frame, a lower --min-matches/--min-confidence, or --crop none"
            )
    else:
        top, left, bottom, right = 0, 0, ref_shape[0], ref_shape[1]

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
