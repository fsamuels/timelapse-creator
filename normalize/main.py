import argparse
import logging
from pathlib import Path

from normalize.align import normalize_sequence

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("normalize")


def parse_size(value):
    if value is None:
        return None
    width, height = value.lower().split("x")
    return int(width), int(height)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Align and crop a directory of drone photos into a "
        "timelapse-ready sequence with a consistent frame."
    )
    parser.add_argument("input", type=Path, help="Directory of source photos")
    parser.add_argument("output", type=Path, help="Directory to write normalized frames into")
    parser.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="Photo to align every other frame to (default: first frame, sorted by filename)",
    )
    parser.add_argument(
        "--min-matches",
        type=int,
        default=10,
        help="Minimum good feature matches required to trust an alignment (default: %(default)s)",
    )
    parser.add_argument(
        "--size",
        type=str,
        default=None,
        help="Resize final cropped frames to WxH (e.g. 1920x1080); default: keep the cropped size",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    result = normalize_sequence(
        args.input,
        args.output,
        reference=args.reference,
        min_matches=args.min_matches,
        output_size=parse_size(args.size),
    )
    log.info("aligned %d frame(s), skipped %d", len(result["aligned"]), len(result["skipped"]))
    for name, reason in result["skipped"]:
        log.info("skipped %s: %s", name, reason)
    log.info("common crop box (top, left, bottom, right): %s", result["crop_box"])


if __name__ == "__main__":
    main()
