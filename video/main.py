"""On-demand CLI: turn a slice of a frame directory into an mp4.

Works over either frame source video.frames.load_frames understands:
scheduled webcam archive directories (capture/archive.py's
<site>/<cam>/YYYY/MM/ layout, timestamps parsed from filenames) or
normalize/align.py's normalized drone-photo output (timestamps from its
manifest.json). Uniform mode (--fps, the default) gives every frame equal
screen time; --proportional gives each frame screen time proportional to
the real time-gap before the next one, capped so no single gap dominates
-- see docs/design.md Component 2.

Output videos are build artifacts, not source -- by convention (not
enforced here) they live under output/, which is gitignored.

    python -m video.main archive/bluewood/summit -o output/summit.mp4 --fps 24
    python -m video.main output/normalized/drone-shots -o output/drone.mp4 \\
        --proportional --duration 30
"""

import argparse
import logging
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

from video import color, encode, frames, labels

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("video")

LABEL_PADDING = 24
LABEL_GAP = 12
DATE_LABEL_FONT_SIZE = 64
FILENAME_LABEL_FONT_SIZE = 40


def parse_patch(value):
    parts = value.split(",")
    if len(parts) != 4:
        raise ValueError("expected 4 comma-separated integers: top,left,bottom,right")
    return tuple(int(p) for p in parts)


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
    parser.add_argument(
        "--label-date",
        action="store_true",
        help="Burn each frame's capture date into the bottom-left corner (bold white text on "
        "a black background), stacked above --label-filename if both are given -- an "
        "alignment-QA aid, not meant to stay in a final render",
    )
    parser.add_argument(
        "--label-filename",
        action="store_true",
        help="Burn each frame's source filename into the bottom-left corner, smaller than "
        "--label-date -- an alignment-QA aid, not meant to stay in a final render",
    )
    parser.add_argument(
        "--white-balance",
        action="store_true",
        help="Correct each frame's color cast (e.g. a warm/red evening shot) by sampling a "
        "fixed patch (--white-balance-patch) and scaling per-channel to match that same "
        "patch's color in --white-balance-target. Only meaningful over already-aligned frames "
        "(normalize/align.py output), since the patch location has to mean the same physical "
        "spot in every frame. Requires --white-balance-patch and --white-balance-target.",
    )
    parser.add_argument(
        "--white-balance-patch",
        type=parse_patch,
        default=None,
        help="top,left,bottom,right pixel box (in the frames' shared aligned coordinate "
        "space) of a fixed, roughly neutral-colored surface (e.g. a roof) visible across the "
        "batch -- required with --white-balance",
    )
    parser.add_argument(
        "--white-balance-target",
        type=Path,
        default=None,
        help="Path to the photo whose --white-balance-patch color other frames are corrected "
        "to match -- pick one with lighting you want the rest of the batch to look like. "
        "Required with --white-balance",
    )
    args = parser.parse_args()
    if args.proportional and args.duration is None:
        parser.error("--proportional requires --duration")
    if args.white_balance and (
        args.white_balance_patch is None or args.white_balance_target is None
    ):
        parser.error("--white-balance requires --white-balance-patch and --white-balance-target")
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


def apply_labels(selected, output_dir, label_date, label_filename):
    """Write a labeled copy of every frame in selected into output_dir,
    returning a new [(path, timestamp), ...] pointing at the copies (input
    order/timestamps preserved).

    Both labels anchor bottom-left: filename at the very bottom, date
    stacked directly above it with LABEL_GAP between them.
    """
    labeled = []
    for path, timestamp in selected:
        image = Image.open(path).convert("RGB")
        if label_filename:
            labels.draw_label(
                image, path.name, "bottom-left", FILENAME_LABEL_FONT_SIZE, LABEL_PADDING
            )
        if label_date:
            filename_box_h = (
                labels.label_box_size(path.name, FILENAME_LABEL_FONT_SIZE)[1] + LABEL_GAP
                if label_filename
                else 0
            )
            labels.draw_label(
                image,
                labels.format_date(timestamp),
                "bottom-left",
                DATE_LABEL_FONT_SIZE,
                LABEL_PADDING,
                stack_offset=filename_box_h,
            )
        out_path = output_dir / path.name
        image.save(out_path)
        labeled.append((out_path, timestamp))
    return labeled


def apply_white_balance(selected, output_dir, patch, target_patch_color):
    """Write a white-balance-corrected copy of every frame in selected into
    output_dir (see video/color.py), returning a new [(path, timestamp), ...]
    pointing at the copies.

    A frame whose patch reading is too dark to trust (color.compute_gains
    returns None -- e.g. the patch fell in deep shadow for that one frame)
    is written through unchanged rather than skipped, so an unreliable
    reading drops a frame's correction, not the frame itself. Otherwise, the
    patch-implied gain is further capped (color.limit_gains_for_headroom) so
    it can't blow out that frame's own highlights even when the gain itself
    was within the normal clamp range -- e.g. a dim early-morning patch
    reading next to an already-bright, sunlit part of the same frame.
    """
    corrected = []
    for path, timestamp in selected:
        image = np.array(Image.open(path).convert("RGB"))
        gains = color.compute_gains(color.sample_patch(image, patch), target_patch_color)
        if gains is not None:
            gains = color.limit_gains_for_headroom(image, gains)
            image = color.apply_gains(image, gains)
        out_path = output_dir / path.name
        Image.fromarray(image).save(out_path)
        corrected.append((out_path, timestamp))
    return corrected


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
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        if args.white_balance:
            target_image = np.array(Image.open(args.white_balance_target).convert("RGB"))
            target_patch_color = color.sample_patch(target_image, args.white_balance_patch)
            log.info("white-balance target patch color (R,G,B): %s", target_patch_color.round(1))
            selected = apply_white_balance(
                selected, Path(tmp_dir), args.white_balance_patch, target_patch_color
            )
        if args.label_date or args.label_filename:
            selected = apply_labels(selected, Path(tmp_dir), args.label_date, args.label_filename)
        script = encode.build_concat_script(
            [(path, duration) for (path, _ts), duration in zip(selected, durations)]
        )
        encode.run_ffmpeg(script, args.output, tmp_dir)
    log.info("wrote %s", args.output)


if __name__ == "__main__":
    main()
