"""Nightly preset: build a single day's clip from a webcam archive cam directory.

video/main.py's on-demand CLI already does everything this needs (--from/--to,
--drop-dark, uniform fps) -- this preset just fixes the choices that make it
runnable unattended every night: default to yesterday's date (Pacific,
matching capture/archive.py's own timestamps), drop dark/night frames by
default (a "sunrise-to-sunset" clip), and name the output file by date. See
docs/design.md Component 2 and docs/open-questions.md #3.

    python -m video.daily_clip archive/bluewood/summit -o daily/bluewood/summit
    python -m video.daily_clip archive/bluewood/summit -o daily/bluewood/summit --date 2026-07-20
"""

import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from capture.archive import PACIFIC
from video import encode, frames

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("video.daily_clip")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build one day's clip from a webcam archive cam directory."
    )
    parser.add_argument(
        "input", type=Path, help="Archive cam directory (e.g. archive/bluewood/summit)"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write <date>.mp4 into (created if missing)",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Day to build (YYYY-MM-DD); default: yesterday (Pacific)",
    )
    parser.add_argument("--fps", type=float, default=24.0, help="Frame rate (default: %(default)s)")
    parser.add_argument(
        "--no-drop-dark",
        action="store_false",
        dest="drop_dark",
        help="Keep night/dark frames instead of dropping them (dropped by default)",
    )
    parser.add_argument(
        "--dark-threshold",
        type=float,
        default=40.0,
        help="Mean brightness (0-255 grayscale) below which a frame counts as dark "
        "(default: %(default)s)",
    )
    return parser.parse_args()


def default_target_date():
    return (datetime.now(PACIFIC) - timedelta(days=1)).date()


def main():
    args = parse_args()
    target_date = args.date or default_target_date()

    selected = frames.load_frames(args.input)
    selected = frames.filter_date_range(selected, since=target_date, until=target_date)

    if args.drop_dark:
        before = len(selected)
        selected = frames.drop_dark_frames(selected, args.dark_threshold)
        log.info("dropped %d dark frame(s)", before - len(selected))

    if not selected:
        log.error("no frames for %s in %s", target_date, args.input)
        raise SystemExit(1)

    durations = frames.uniform_durations(selected, args.fps)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"{target_date.isoformat()}.mp4"

    log.info("encoding %d frame(s) for %s -> %s", len(selected), target_date, output_path)
    encode.encode_frames(
        [(path, duration) for (path, _ts), duration in zip(selected, durations)], output_path
    )
    log.info("wrote %s", output_path)


if __name__ == "__main__":
    main()
