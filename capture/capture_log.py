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


def read_capture_log(log_path):
    """Parse the JSONL capture log into a list of entries; [] if missing."""
    if not log_path:
        return []
    log_path = Path(log_path)
    if not log_path.is_file():
        return []
    entries = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # tolerate a partially written trailing line
    return entries


def latest_outcomes(entries):
    """Map each cam to its most recent log entry (the log is append-only)."""
    latest = {}
    for entry in entries:
        cam = entry.get("cam")
        if cam is not None:
            latest[cam] = entry
    return latest


def bucket(ts, interval_minutes):
    """Which fixed-size time bucket ``ts`` falls into.

    Anchored to the Unix epoch rather than to any previous run's finish time,
    so a run that completes a few seconds late still lands in the same
    bucket as an on-time one — immune to run-to-run processing drift.
    """
    return int(ts.timestamp() // (interval_minutes * 60))


def is_due(interval_minutes, last_entry, now):
    """Whether a cam should be captured this tick, given its last log entry (or None)."""
    if last_entry is None:
        return True
    last_ts = datetime.fromisoformat(last_entry["ts"])
    return bucket(now, interval_minutes) != bucket(last_ts, interval_minutes)
