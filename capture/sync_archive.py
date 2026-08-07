"""Mirror a cam's archive from the Pi's directory-listing server to local disk.

The Pi serves its archive as a plain directory listing at
``http://timelapse-pi.local:8080/archive/<site>/<cam>/`` (see
docs/open-questions.md #8) in the same ``YYYY/MM/`` layout
capture/archive.py writes locally. This walks that listing recursively and
downloads any frame not already cached under --archive-dir, so re-running
it only fetches what's new since the last sync.

    python -m capture.sync_archive washington kalaloch-lodge
"""

import argparse
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

from capture.fetch import USER_AGENT

log = logging.getLogger("sync_archive")

DEFAULT_BASE_URL = "http://timelapse-pi.local:8080"
HREF_RE = re.compile(r'href="([^"]+)"')


def list_dir(url, timeout=15):
    """Names linked from a directory-listing page: subdirs end in '/'."""
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return [href for href in HREF_RE.findall(resp.text) if href not in ("../", "/")]


def sync_dir(url, local_dir, timeout=15):
    """Recursively mirror url's directory listing into local_dir.

    Returns (downloaded, skipped) counts. Frames already present locally
    are left untouched -- filenames are immutable capture timestamps, so an
    existing file is always the same content, and skipping it is what
    makes re-running this cheap.
    """
    downloaded = skipped = 0
    for name in list_dir(url, timeout=timeout):
        if name.endswith("/"):
            sub_downloaded, sub_skipped = sync_dir(
                urljoin(url, name), local_dir / name, timeout=timeout
            )
            downloaded += sub_downloaded
            skipped += sub_skipped
            continue

        dest = local_dir / name
        if dest.exists():
            skipped += 1
            continue

        resp = requests.get(urljoin(url, name), timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        downloaded += 1
    return downloaded, skipped


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", help="e.g. washington")
    parser.add_argument("cam", help="e.g. kalaloch-lodge")
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="Pi status-page origin (default: %(default)s)"
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("archive"),
        help="Local archive root to mirror into (default: %(default)s)",
    )
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    args = parse_args()
    remote_url = f"{args.base_url}/archive/{args.site}/{args.cam}/"
    local_dir = args.archive_dir / args.site / args.cam
    log.info("syncing %s -> %s", remote_url, local_dir)
    downloaded, skipped = sync_dir(remote_url, local_dir)
    log.info("done: %d downloaded, %d already cached", downloaded, skipped)


if __name__ == "__main__":
    main()
