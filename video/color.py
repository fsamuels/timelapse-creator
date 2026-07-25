"""Per-frame color-cast correction ("white balance") using a fixed neutral
reference patch.

Built for batches where lighting drifts across frames (e.g. shots at
different times of day, some warm/red at evening) but scene content varies
too much between frames for a generic whole-image histogram match to be
safe -- e.g. this project's drone-photo timelapses, where forcing every
frame's color statistics to match one reference would also drag a
snow-covered frame's whites toward whatever hue a lush-green reference
happens to have.

Instead, this samples one small, fixed pixel region -- a patch on some
physically neutral-ish surface (a roof, a fence rail) that's visible across
the batch at a consistent location, which only works because the frames are
already aligned into a shared coordinate space (see normalize/align.py).
Comparing that one patch's color to what it looks like in a chosen "target"
frame gives a per-channel gain that corrects the *lighting's* color cast,
without touching the actual color relationships elsewhere in the frame the
way a full histogram match would.
"""

import numpy as np

# Below this mean patch brightness (0-255), a frame's patch reading is
# treated as too dark to trust for a gain estimate (e.g. it fell in deep
# shadow) -- compute_gains returns None rather than let an unreliable read
# drive a confident-looking correction.
MIN_PATCH_BRIGHTNESS = 20

# Per-channel gain is clamped to this range regardless of what the patch
# comparison implies, so one frame with an unusually shadowed/blown-out
# patch can't produce a wild, obviously-wrong correction.
DEFAULT_GAIN_CLAMP = (0.5, 2.0)

# limit_gains_for_headroom caps each channel's gain so its
# HIGHLIGHT_PERCENTILE-th percentile value lands at HIGHLIGHT_CEILING
# (out of 255) rather than being pushed past it. A percentile rather than
# the flat max so a handful of already-blown-out specular highlight pixels
# (sun glare, reflective metal) don't single-handedly zero out the gain.
HIGHLIGHT_CEILING = 250
HIGHLIGHT_PERCENTILE = 99


def sample_patch(image, patch):
    """Mean color (one value per channel) of image within patch, a
    (top, left, bottom, right) pixel box -- matches align.py's crop_box
    convention.
    """
    top, left, bottom, right = patch
    return image[top:bottom, left:right].reshape(-1, image.shape[-1]).mean(axis=0)


def compute_gains(frame_patch, target_patch, clamp=DEFAULT_GAIN_CLAMP):
    """Per-channel multiplicative gain that would bring frame_patch's color
    to target_patch's, clamped to `clamp`. None if frame_patch is too dark
    to trust (see MIN_PATCH_BRIGHTNESS).
    """
    if frame_patch.mean() < MIN_PATCH_BRIGHTNESS:
        return None
    gains = target_patch / np.clip(frame_patch, 1, None)
    return np.clip(gains, *clamp)


def limit_gains_for_headroom(
    image, gains, ceiling=HIGHLIGHT_CEILING, percentile=HIGHLIGHT_PERCENTILE
):
    """Scale gains down (never up) so applying them won't push image's own
    highlights past ceiling, even if the reference-patch comparison alone
    implied a much larger correction.

    compute_gains's clamp bounds the *ratio* implied by the patch, but a
    frame can still have a plausible, in-range gain that's simply too large
    for that particular frame's own content -- e.g. a dim early-morning
    patch reading next to a bright, already-sunlit arena elsewhere in the
    same frame. Applying the patch-implied gain uncapped there blows out
    real detail across nearly half the image; this keeps the correction
    physically sane by checking it against the frame it's actually being
    applied to, not just the patch.
    """
    highlights = np.percentile(image.reshape(-1, image.shape[-1]), percentile, axis=0)
    max_gains = ceiling / np.clip(highlights, 1, None)
    return np.minimum(gains, max_gains)


def apply_gains(image, gains):
    """image with gains (one multiplier per channel) applied, clipped back
    into image's own dtype range (e.g. uint8's 0-255).
    """
    corrected = image.astype(np.float32) * gains
    info = np.iinfo(image.dtype)
    return np.clip(corrected, info.min, info.max).astype(image.dtype)
