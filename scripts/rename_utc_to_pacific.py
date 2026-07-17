"""One-off migration: rename archive frames from UTC-named files to the
fixed UTC-8 offset scheme used by capture/archive.py since PR #2.

Usage:
    python -m scripts.rename_utc_to_pacific [--dry-run]

Safe to re-run: files already in the new `...-0800.jpg` / `...-0700.jpg` (or
any other explicit-offset) form are skipped.
"""

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

from capture.archive import PACIFIC

ARCHIVE_ROOT = Path(__file__).parent.parent / "archive"

# Old scheme: 2026-07-16T20-10-05-166615Z.jpg
OLD_NAME_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d{6})Z\.jpg$")


def plan_rename(old_path):
    match = OLD_NAME_RE.match(old_path.name)
    if not match:
        return None

    utc_dt = datetime.strptime(match.group("ts"), "%Y-%m-%dT%H-%M-%S-%f").replace(tzinfo=UTC)
    pacific_dt = utc_dt.astimezone(PACIFIC)

    cam_dir = old_path.parent.parent.parent
    new_month_dir = cam_dir / pacific_dt.strftime("%Y/%m")
    new_name = f"{pacific_dt.strftime('%Y-%m-%dT%H-%M-%S-%f%z')}.jpg"
    return new_month_dir / new_name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for old_path in sorted(ARCHIVE_ROOT.rglob("*.jpg")):
        new_path = plan_rename(old_path)
        if new_path is None:
            continue
        if new_path.exists():
            raise FileExistsError(f"{new_path} already exists, refusing to overwrite")

        print(f"{old_path.relative_to(ARCHIVE_ROOT)} -> {new_path.relative_to(ARCHIVE_ROOT)}")
        if not args.dry_run:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(new_path)


if __name__ == "__main__":
    main()
