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
    parser.add_argument(
        "output",
        type=Path,
        help="Directory to write normalized frames into (e.g. output/normalized/<name>)",
    )
    parser.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="Photo to align every other frame to (default: first frame, sorted by filename)",
    )
    parser.add_argument(
        "--method",
        choices=("ecc", "orb"),
        default="ecc",
        help="Alignment algorithm (default: %(default)s). 'ecc' aligns by directly optimizing "
        "against pixel intensity gradients across the whole image -- the better choice for "
        "low-texture scenes (grass/dirt fields) where 'orb' (sparse feature matching) "
        "struggles to find enough reliable keypoints. 'orb' is faster and still available "
        "for more texture-rich scenes.",
    )
    parser.add_argument(
        "--min-matches",
        type=int,
        default=10,
        help=(
            "orb only: minimum feature matches that must agree on a single alignment for a "
            "photo to count as part of this sequence (default: %(default)s). This is the "
            "tolerance knob: raise it to more strictly exclude photos that don't clearly "
            "match --reference (e.g. unrelated shots mixed into the input directory), "
            "lower it to be more lenient. Photos that don't clear the bar are skipped "
            "and reported, not silently dropped."
        ),
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.2,
        help="ecc only: minimum ECC correlation coefficient (roughly 0-1) for a photo to "
        "count as part of this sequence (default: %(default)s). Plays the same tolerance-knob "
        "role as --min-matches does for orb -- raise to be stricter, lower to be more lenient.",
    )
    parser.add_argument(
        "--crop",
        choices=("none", "intersection"),
        default="none",
        help="How to handle each frame's black border from being warped into the reference's "
        "coordinate space (default: %(default)s). 'none' keeps every frame at the reference's "
        "full size with real black wherever that frame's own alignment didn't reach. "
        "'intersection' shrinks every frame to the one rectangle valid across ALL aligned "
        "frames, guaranteeing no black edges -- but that shrinks fast as more frames are "
        "aligned (each frame's own border, intersected against every other frame's), so it "
        "can end up discarding most of the frame on a large batch.",
    )
    parser.add_argument(
        "--size",
        type=str,
        default=None,
        help="Resize final frames to WxH (e.g. 1920x1080); default: keep the cropped/full size",
    )
    parser.add_argument(
        "--features",
        type=int,
        default=20000,
        help="orb only: ORB keypoints per image (default: %(default)s). Raise on low-texture "
        "scenes (grass/dirt fields) where the default keypoint cap gets diluted by repetitive "
        "texture, crowding out distinctive structural keypoints (fences, buildings) that "
        "actually produce reliable matches. Lower to speed up alignment on more "
        "feature-rich scenes.",
    )
    parser.add_argument(
        "--control-points",
        type=Path,
        default=None,
        help="Path to a normalize/control_points.py JSON file (see normalize/annotate.py) of "
        "manually-clicked anchor photos. When given, every non-anchor photo is aligned "
        "automatically against the reference and every anchor, keeping whichever match scores "
        "best and composing it with that anchor's manually-verified transform -- more accurate "
        "on a batch spanning several distinct zoom/angle groupings than aligning everything "
        "against one global reference. A photo listed as rejected in the file is excluded "
        "entirely.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    result = normalize_sequence(
        args.input,
        args.output,
        reference=args.reference,
        method=args.method,
        min_matches=args.min_matches,
        min_confidence=args.min_confidence,
        output_size=parse_size(args.size),
        nfeatures=args.features,
        crop=args.crop,
        control_points=args.control_points,
    )
    log.info("aligned %d frame(s), skipped %d", len(result["aligned"]), len(result["skipped"]))
    for name, reason in result["skipped"]:
        log.info("skipped %s: %s", name, reason)
    log.info("common crop box (top, left, bottom, right): %s", result["crop_box"])


if __name__ == "__main__":
    main()
