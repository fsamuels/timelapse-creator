"""Frame selection and timing logic for the video builder.

Two frame sources feed this module, distinguished only by how a frame's
real capture timestamp is recovered:

- Scheduled webcam archive directories (capture/archive.py's
  ``<site>/<cam>/YYYY/MM/`` layout) encode the timestamp in the filename,
  recovered via ``capture.archive.parse_frame_time``.
- Normalized drone-photo batches (normalize/align.py's output) carry a
  ``manifest.json`` (filename -> ISO 8601 timestamp) instead, since
  cv2.imwrite strips the source EXIF the timestamp would otherwise come
  from.

Everything downstream (filtering, duration computation) works on a single
``[(Path, datetime), ...]`` list regardless of which source produced it.
"""

import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageStat

from capture.archive import frame_hash, parse_frame_time
from normalize.align import MANIFEST_FILENAME

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _load_manifest(input_dir):
    manifest_path = Path(input_dir) / MANIFEST_FILENAME
    if not manifest_path.exists():
        return None
    raw = json.loads(manifest_path.read_text())
    return {name: datetime.fromisoformat(ts) for name, ts in raw.items()}


def load_frames(input_dir):
    """Every frame in input_dir as (path, timestamp) pairs, sorted by time.

    Prefers a manifest.json (normalized drone-photo batches) when present;
    falls back to parsing the archive's timestamped filenames otherwise.
    Frames a manifest doesn't mention (e.g. photos normalize_sequence
    skipped) are left out, matching the "skipped, not silently forced in"
    behavior of normalize_sequence itself.
    """
    input_dir = Path(input_dir)
    manifest = _load_manifest(input_dir)

    found = []
    for path in sorted(input_dir.rglob("*")):
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if manifest is not None:
            if path.name in manifest:
                found.append((path, manifest[path.name]))
        else:
            found.append((path, parse_frame_time(path)))

    found.sort(key=lambda item: item[1])
    return found


def filter_date_range(frame_list, since=None, until=None):
    """Keep only frames whose timestamp's date falls in [since, until]
    (either bound optional, both inclusive).
    """

    def in_range(ts):
        if since is not None and ts.date() < since:
            return False
        if until is not None and ts.date() > until:
            return False
        return True

    return [(path, ts) for path, ts in frame_list if in_range(ts)]


def mean_brightness(path):
    with Image.open(path) as image:
        return ImageStat.Stat(image.convert("L")).mean[0]


def drop_dark_frames(frame_list, threshold):
    """Drop frames whose mean brightness (0-255 grayscale) is below
    threshold -- e.g. night frames on an otherwise-lit webcam.
    """
    return [(path, ts) for path, ts in frame_list if mean_brightness(path) >= threshold]


def drop_duplicate_frames(frame_list):
    """Drop frames whose bytes exactly match the immediately preceding kept
    frame -- residual near-duplicates that slipped past capture-time
    stale-frame detection (e.g. frames pulled in from more than one source).
    """
    kept = []
    prev_hash = None
    for path, ts in frame_list:
        digest = frame_hash(Path(path).read_bytes())
        if digest == prev_hash:
            continue
        kept.append((path, ts))
        prev_hash = digest
    return kept


def uniform_durations(frame_list, fps):
    """Every frame gets equal screen time: 1/fps seconds."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    return [1.0 / fps for _ in frame_list]


def proportional_durations(frame_list, target_duration, min_hold=0.05, max_hold=2.0):
    """Each frame's hold time is proportional to the real time-gap before
    the next frame, scaled so the (pre-clamp) total matches
    target_duration, then clamped to [min_hold, max_hold] per frame.

    Clamping is the deliberate tradeoff that makes this usable: without a
    ceiling, one outsized gap (an overnight webcam outage, two weeks between
    drone flights) would dominate the entire video; without a floor, a
    burst of same-minute frames would collapse to imperceptible slivers.
    The cost is that the rendered video's actual total length can end up
    short of or longer than target_duration once clamps kick in -- callers
    that care should compare sum(result) against target_duration
    afterward, since this is a target, not a guarantee.
    """
    if not frame_list:
        return []
    if len(frame_list) == 1:
        return [target_duration]

    gaps = [
        (frame_list[i + 1][1] - frame_list[i][1]).total_seconds()
        for i in range(len(frame_list) - 1)
    ]
    gaps.append(gaps[-1])  # last frame: hold it for the same span as the gap before it

    total_gap = sum(gaps)
    if total_gap <= 0:
        raise ValueError("frame timestamps must be strictly increasing to time proportionally")

    scale = target_duration / total_gap
    return [min(max(gap * scale, min_hold), max_hold) for gap in gaps]
