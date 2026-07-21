import argparse
import logging
from pathlib import Path

import yaml

from capture.archive import is_stale, save_frame
from capture.capture_log import append_capture_log
from capture.fetch import fetch_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("capture")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def parse_args():
    parser = argparse.ArgumentParser(description="Capture webcam frames into the archive.")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to a capture config YAML file (default: %(default)s)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = yaml.safe_load(args.config.read_text())
    archive_root = Path(config["archive_dir"])
    capture_log_path = config.get("capture_log")

    for name, cam in config["cams"].items():
        cam_dir = archive_root / name
        try:
            data = fetch_frame(cam)
        except Exception as exc:
            log.info("%s: fetch failed (%s)", name, exc)
            if capture_log_path:
                append_capture_log(capture_log_path, name, "fetch_failed", detail=str(exc))
            continue

        if is_stale(data, cam_dir):
            log.info("%s: stale frame, skipped", name)
            if capture_log_path:
                append_capture_log(capture_log_path, name, "stale")
            continue

        path = save_frame(data, cam_dir)
        log.info("%s: saved %s", name, path)
        if capture_log_path:
            append_capture_log(capture_log_path, name, "saved", detail=str(path))


if __name__ == "__main__":
    main()
