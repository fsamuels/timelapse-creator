"""Manually-clicked control points for anchor photos, and the homography math
built from them.

Where estimate_alignment/estimate_alignment_ecc (align.py) infer a transform
automatically and can be locally imprecise on scenes with few distinctive
features (see docs/design.md Component 4), a control point set is a small
number of point correspondences a person clicked by hand between the
sequence's reference photo and one "anchor" photo -- e.g. the same four barn
corners, once each. Because a person picked them, the resulting transform is
exactly as accurate as that person's clicks, not bounded by how much texture
the scene happens to have.

Typically only a handful of photos end up as annotated anchors (one per
genuinely distinct zoom/angle grouping is usually enough), but
normalize/annotate.py's tool lets you page through and review every photo
in a sequence, not just a suggested few -- see align.py's
normalize_sequence(..., control_points=...) for how a regular (non-anchor,
non-rejected) photo gets aligned to the *nearest* anchor automatically, then
composed with that anchor's manually-verified transform into the
reference's frame.

A candidate anchor can also be explicitly rejected ("this photo doesn't
belong in the sequence at all, or is too poor an angle/zoom to bother
annotating") instead of annotated -- normalize_sequence excludes a rejected
photo from the aligned output entirely, whether it would otherwise have been
used as an anchor or just matched automatically against the reference/other
anchors.

The file is a plain JSON dict, meant to be committed to the repo (unlike
output/, which is regeneratable and gitignored) since the clicks (and
rejections) it records represent real, non-regeneratable manual work:

{
  "reference": "<reference photo filename>",
  "anchors": {
    "<anchor photo filename>": {
      "points_reference": [[x, y], ...],  # >= 4 points, in the reference photo
      "points_anchor":    [[x, y], ...]   # the same points, in the anchor photo
    },
    ...
  },
  "rejected": ["<photo filename>", ...]
}
"""

import json
from pathlib import Path

import cv2
import numpy as np

MIN_CONTROL_POINTS = 4


def empty(reference_name):
    return {"reference": reference_name, "anchors": {}, "rejected": []}


def load(path):
    """Load a control points file, or an empty one if path doesn't exist yet."""
    path = Path(path)
    if not path.exists():
        return {"reference": None, "anchors": {}, "rejected": []}
    data = json.loads(path.read_text())
    data.setdefault("rejected", [])
    return data


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def set_anchor_points(data, anchor_name, points_reference, points_anchor):
    """Record (or overwrite) one anchor's control points in a loaded control
    points dict. Doesn't write to disk -- pair with save().
    """
    if len(points_reference) != len(points_anchor):
        raise ValueError(
            f"points_reference has {len(points_reference)} point(s) but points_anchor has "
            f"{len(points_anchor)} -- they must be the same length, one correspondence per pair"
        )
    if len(points_reference) < MIN_CONTROL_POINTS:
        raise ValueError(
            f"need at least {MIN_CONTROL_POINTS} point pairs for a homography, "
            f"got {len(points_reference)}"
        )
    data["anchors"][anchor_name] = {
        "points_reference": [list(p) for p in points_reference],
        "points_anchor": [list(p) for p in points_anchor],
    }
    if anchor_name in data.get("rejected", []):
        data["rejected"].remove(anchor_name)


def reject(data, name):
    """Mark a photo as not belonging in the sequence at all (or too poor an
    angle/zoom to be worth annotating as an anchor). Removes any existing
    control points for it, if it had previously been annotated -- reject and
    annotate are mutually exclusive.
    """
    data.setdefault("rejected", [])
    if name not in data["rejected"]:
        data["rejected"].append(name)
    data["anchors"].pop(name, None)


def anchor_transform(data, anchor_name):
    """The 3x3 homography mapping anchor_name's own pixel coordinates onto
    the reference's, built from that anchor's manually-clicked control
    points. Suitable for warp_to_reference (align.py).
    """
    anchor = data["anchors"][anchor_name]
    src = np.float32(anchor["points_anchor"])
    dst = np.float32(anchor["points_reference"])
    # method=0 (plain least squares, not RANSAC): these are hand-verified
    # correspondences, not noisy automated matches, so there's no outlier to
    # guard against -- RANSAC's reprojection-threshold tuning would just be
    # an extra knob with nothing to protect against.
    matrix, _ = cv2.findHomography(src, dst, method=0)
    return matrix.astype(np.float32)
