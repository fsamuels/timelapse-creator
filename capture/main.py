import logging
from pathlib import Path

import yaml

from capture.archive import is_stale, save_frame
from capture.fetch import fetch_frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("capture")

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def main():
    config = yaml.safe_load(CONFIG_PATH.read_text())
    archive_root = Path(config["archive_dir"])

    for name, cam in config["cams"].items():
        cam_dir = archive_root / name
        try:
            data = fetch_frame(cam)
        except Exception as exc:
            log.info("%s: fetch failed (%s)", name, exc)
            continue

        if is_stale(data, cam_dir):
            log.info("%s: stale frame, skipped", name)
            continue

        path = save_frame(data, cam_dir)
        log.info("%s: saved %s", name, path)


if __name__ == "__main__":
    main()
