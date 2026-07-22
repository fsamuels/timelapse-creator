import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Fixed UTC-8 offset, not IANA "America/Los_Angeles" — filenames stay strictly
# monotonic across DST transitions, which the lexical-sort-based stale
# detection below depends on. Timestamps are off by an hour from true local
# time during PDT (summer); that's the deliberate tradeoff.
PACIFIC = timezone(timedelta(hours=-8), name="PT-08")


# Filename format used by save_frame below and parsed back out by
# parse_frame_time — kept as one constant so the two can't drift apart.
FRAME_TIME_FORMAT = "%Y-%m-%dT%H-%M-%S-%f%z"


def frame_hash(data):
    return hashlib.sha256(data).hexdigest()


def parse_frame_time(path):
    """Recover the capture time from a frame's filename (inverse of save_frame).

    Frames are named by their PACIFIC-offset timestamp; the offset is embedded
    in the name (``-0800``), so the returned datetime is timezone-aware.
    """
    return datetime.strptime(Path(path).stem, FRAME_TIME_FORMAT)


def latest_frame(cam_dir):
    files = sorted(cam_dir.rglob("*.jpg"))
    return files[-1] if files else None


def is_stale(data, cam_dir):
    prev = latest_frame(cam_dir)
    if prev is None:
        return False
    return frame_hash(data) == frame_hash(prev.read_bytes())


def save_frame(data, cam_dir):
    now = datetime.now(PACIFIC)
    month_dir = cam_dir / now.strftime("%Y/%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{now.strftime(FRAME_TIME_FORMAT)}.jpg"
    path.write_bytes(data)
    return path
