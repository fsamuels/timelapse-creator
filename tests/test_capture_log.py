import json
from datetime import datetime, timedelta, timezone

from capture.capture_log import (
    append_capture_log,
    bucket,
    is_due,
    latest_outcomes,
    read_capture_log,
)

PT = timezone(timedelta(hours=-8))


def test_append_capture_log_creates_file_and_missing_parents(tmp_path):
    log_path = tmp_path / "nested" / "dir" / "capture.log"

    append_capture_log(log_path, "summit", "saved", detail="archive/summit/x.jpg")

    assert log_path.exists()


def test_append_capture_log_writes_one_jsonl_line(tmp_path):
    log_path = tmp_path / "capture.log"

    append_capture_log(log_path, "summit", "saved", detail="archive/summit/x.jpg")

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["cam"] == "summit"
    assert entry["outcome"] == "saved"
    assert entry["detail"] == "archive/summit/x.jpg"
    assert "ts" in entry


def test_append_capture_log_appends_across_calls(tmp_path):
    log_path = tmp_path / "capture.log"

    append_capture_log(log_path, "summit", "saved", detail="archive/summit/x.jpg")
    append_capture_log(log_path, "base", "stale")
    append_capture_log(log_path, "summit", "fetch_failed", detail="timeout")

    lines = log_path.read_text().splitlines()
    assert len(lines) == 3

    entries = [json.loads(line) for line in lines]
    assert entries[0]["cam"] == "summit"
    assert entries[0]["outcome"] == "saved"
    assert entries[1]["cam"] == "base"
    assert entries[1]["outcome"] == "stale"
    assert entries[1]["detail"] == ""
    assert entries[2]["cam"] == "summit"
    assert entries[2]["outcome"] == "fetch_failed"
    assert entries[2]["detail"] == "timeout"


def test_read_capture_log_missing_file_is_empty(tmp_path):
    assert read_capture_log(tmp_path / "capture.log") == []
    assert read_capture_log(None) == []


def test_read_capture_log_tolerates_bad_trailing_line(tmp_path):
    log = tmp_path / "capture.log"
    log.write_text(json.dumps({"cam": "summit", "outcome": "saved"}) + "\n" + '{"cam": "base",')

    entries = read_capture_log(log)

    assert entries == [{"cam": "summit", "outcome": "saved"}]


def test_latest_outcomes_keeps_last_entry_per_cam():
    entries = [
        {"cam": "summit", "outcome": "saved"},
        {"cam": "base", "outcome": "fetch_failed"},
        {"cam": "summit", "outcome": "stale"},
    ]

    latest = latest_outcomes(entries)

    assert latest["summit"]["outcome"] == "stale"
    assert latest["base"]["outcome"] == "fetch_failed"


def test_bucket_same_interval_window_is_equal():
    a = datetime(2026, 1, 1, 10, 5, 0, tzinfo=PT)
    b = datetime(2026, 1, 1, 10, 12, 0, tzinfo=PT)

    assert bucket(a, 15) == bucket(b, 15)


def test_bucket_crossing_boundary_differs():
    a = datetime(2026, 1, 1, 10, 14, 59, tzinfo=PT)
    b = datetime(2026, 1, 1, 10, 15, 0, tzinfo=PT)

    assert bucket(a, 15) != bucket(b, 15)


def test_bucket_ignores_run_finish_drift_across_the_boundary():
    # A run that finishes 10s late still lands in the same bucket as an
    # on-time one would have, once the clock crosses into the next interval.
    last_run = datetime(2026, 1, 1, 10, 0, 10, tzinfo=PT)  # finished 10s late
    next_tick = datetime(2026, 1, 1, 11, 0, 0, tzinfo=PT)  # exactly 1hr later

    assert bucket(last_run, 60) != bucket(next_tick, 60)


def test_is_due_with_no_prior_entry():
    now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=PT)

    assert is_due(60, None, now) is True


def test_is_due_false_within_same_bucket():
    last_entry = {"ts": datetime(2026, 1, 1, 10, 0, 10, tzinfo=PT).isoformat()}
    now = datetime(2026, 1, 1, 10, 30, 0, tzinfo=PT)

    assert is_due(60, last_entry, now) is False


def test_is_due_true_once_a_new_bucket_starts_despite_late_finish():
    # Reproduces the drift bug: a run that completes 10s late must not push
    # the next due tick out by a full extra interval.
    last_entry = {"ts": datetime(2026, 1, 1, 10, 0, 10, tzinfo=PT).isoformat()}
    now = datetime(2026, 1, 1, 11, 0, 0, tzinfo=PT)

    assert is_due(60, last_entry, now) is True
