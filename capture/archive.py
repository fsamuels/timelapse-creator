import hashlib
from datetime import datetime, timedelta, timezone

# Fixed UTC-8 offset, not IANA "America/Los_Angeles" — filenames stay strictly
# monotonic across DST transitions, which the lexical-sort-based stale
# detection below depends on. Timestamps are off by an hour from true local
# time during PDT (summer); that's the deliberate tradeoff.
PACIFIC = timezone(timedelta(hours=-8), name="PT-08")


def frame_hash(data):
    return hashlib.sha256(data).hexdigest()


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
    path = month_dir / f"{now.strftime('%Y-%m-%dT%H-%M-%S-%f%z')}.jpg"
    path.write_bytes(data)
    return path
