"""Persisted, append-only capture log (JSONL) — kept separate from archive.py so
that archive.py's hash/stale-detection logic (the one piece of this codebase where
a silent regression is easy to ship, per CLAUDE.md) stays untouched and isolated.
"""

import json
from datetime import datetime
from pathlib import Path

from capture.archive import PACIFIC


def append_capture_log(log_path, cam, outcome, detail=""):
    """Append one JSON object (as a line) recording a single cam's capture outcome.

    ``log_path`` may be a str or Path; its parent directory is created if missing.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(PACIFIC).isoformat(),
        "cam": cam,
        "outcome": outcome,
        "detail": detail,
    }

    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
