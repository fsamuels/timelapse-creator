import hashlib
from datetime import UTC, datetime


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
    now = datetime.now(UTC)
    month_dir = cam_dir / now.strftime("%Y/%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{now.strftime('%Y-%m-%dT%H-%M-%S-%f')}Z.jpg"
    path.write_bytes(data)
    return path
