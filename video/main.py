"""On-demand CLI: turn a slice of a frame directory into an mp4.

Works over either frame source video.frames.load_frames understands:
scheduled webcam archive directories (capture/archive.py's
<site>/<cam>/YYYY/MM/ layout, timestamps parsed from filenames) or
normalize/align.py's normalized drone-photo output (timestamps from its
manifest.json). Uniform mode (--fps, the default) gives every frame equal
screen time; --proportional gives each frame screen time proportional to
the real time-gap before the next one, capped so no single gap dominates
-- see docs/design.md Component 2.

    python -m video.main archive/bluewood/summit -o summit.mp4 --fps 24
    python -m video.main normalized/drone-shots -o drone.mp4 \\
        --proportional --duration 30
"""

import argparse
import logging
import tempfile
from datetime import date
from pathlib import Path

from video import encode, frames

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("video")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build an mp4 timelapse from a directory of frames."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Directory of frames: an archive cam directory, or a normalize/align.py output dir",
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
        "--fps",
        type=float,
        default=24.0,
        help="Frame rate for uniform mode (default: %(default)s); ignored with --proportional",
    )
    parser.add_argument(
        "--proportional",
        action="store_true",
        help="Hold each frame for a time proportional to the real gap before the next frame, "
        "instead of a fixed rate -- e.g. for irregularly-spaced drone photo batches",
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
    parser.add_argument(
        "--drop-dark",
        action="store_true",
        help="Drop frames below --dark-threshold mean brightness (e.g. night frames)",
    )
    parser.add_argument(
        "--dark-threshold",
        type=float,
        default=40.0,
        help="Mean brightness (0-255 grayscale) below which a frame counts as dark "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--dedupe",
        action="store_true",
        help="Drop frames byte-identical to the immediately preceding kept frame",
    )
    args = parser.parse_args()
    if args.proportional and args.duration is None:
        parser.error("--proportional requires --duration")
    return args


def select_frames(args):
    selected = frames.load_frames(args.input)
    selected = frames.filter_date_range(selected, since=args.since, until=args.until)

    if args.drop_dark:
        before = len(selected)
        selected = frames.drop_dark_frames(selected, args.dark_threshold)
        log.info("dropped %d dark frame(s)", before - len(selected))

    if args.dedupe:
        before = len(selected)
        selected = frames.drop_duplicate_frames(selected)
        log.info("dropped %d duplicate frame(s)", before - len(selected))

    return selected


def main():
    args = parse_args()
    selected = select_frames(args)

    if not selected:
        log.error("no frames selected -- check --from/--to and the input directory")
        raise SystemExit(1)

    if args.proportional:
        durations = frames.proportional_durations(
            selected, args.duration, min_hold=args.min_hold, max_hold=args.max_hold
        )
        actual = sum(durations)
        if abs(actual - args.duration) > 0.5:
            log.info(
                "target duration %.1fs, actual %.1fs after --min-hold/--max-hold clamping",
                args.duration,
                actual,
            )
    else:
        durations = frames.uniform_durations(selected, args.fps)

    log.info("encoding %d frame(s) (%.1fs) -> %s", len(selected), sum(durations), args.output)
    script = encode.build_concat_script(
        [(path, duration) for (path, _ts), duration in zip(selected, durations)]
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        encode.run_ffmpeg(script, args.output, tmp_dir)
    log.info("wrote %s", args.output)


if __name__ == "__main__":
    main()
