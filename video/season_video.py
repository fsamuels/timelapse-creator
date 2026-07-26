"""Season-long preset: subsample a webcam archive cam directory to one frame
per day and build a single video spanning the whole date range.

Same machinery as the on-demand video/main.py CLI, plus the one thing that's
genuinely new for this preset: subsampling to one frame/day
(frames.subsample_daily) so a multi-month archive collapses to a watchable
length instead of hours of near-identical frames. See docs/design.md
Component 2 and docs/open-questions.md #3.

    python -m video.season_video archive/bluewood/summit -o season.mp4 --proportional --duration 60
    python -m video.season_video archive/bluewood/summit -o season.mp4 --fps 8 --at-hour 12
"""

import argparse
import logging
from datetime import date
from pathlib import Path

from video import encode, frames

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("video.season_video")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a season-long timelapse, subsampled to one frame/day."
    )
    parser.add_argument(
        "input", type=Path, help="Archive cam directory (e.g. archive/bluewood/summit)"
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output mp4 path")
    parser.add_argument(
        "--from",
        dest="since",
        type=date.fromisoformat,
        default=None,
        help="Only include frames on/after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to",
        dest="until",
        type=date.fromisoformat,
        default=None,
        help="Only include frames on/before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--at-hour",
        type=float,
        default=12.0,
        help="Pick each day's frame closest to this hour of day, 0-23 (default: %(default)s, noon)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=8.0,
        help="Frame rate for uniform mode (default: %(default)s); ignored with --proportional",
    )
    parser.add_argument(
        "--proportional",
        action="store_true",
        help="Hold each day's frame proportional to the real gap before the next one -- e.g. a "
        "multi-day capture outage reads as a pause -- instead of a fixed frame rate",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Target total video length in seconds (required with --proportional)",
    )
    parser.add_argument(
        "--min-hold",
        type=float,
        default=0.05,
        help="Proportional mode: minimum seconds any one frame is held (default: %(default)s)",
    )
    parser.add_argument(
        "--max-hold",
        type=float,
        default=2.0,
        help="Proportional mode: maximum seconds any one frame is held (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.proportional and args.duration is None:
        parser.error("--proportional requires --duration")
    return args


def main():
    args = parse_args()
    selected = frames.load_frames(args.input)
    selected = frames.filter_date_range(selected, since=args.since, until=args.until)
    selected = frames.subsample_daily(selected, at_hour=args.at_hour)
    log.info("subsampled to %d day(s)", len(selected))

    if not selected:
        log.error("no frames selected -- check --from/--to and the input directory")
        raise SystemExit(1)

    if args.proportional:
        durations = frames.proportional_durations(
            selected, args.duration, min_hold=args.min_hold, max_hold=args.max_hold
        )
    else:
        durations = frames.uniform_durations(selected, args.fps)

    log.info("encoding %d frame(s) (%.1fs) -> %s", len(selected), sum(durations), args.output)
    encode.encode_frames(
        [(path, duration) for (path, _ts), duration in zip(selected, durations)], args.output
    )
    log.info("wrote %s", args.output)


if __name__ == "__main__":
    main()
