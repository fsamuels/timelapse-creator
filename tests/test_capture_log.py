import json

from capture.capture_log import append_capture_log


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
