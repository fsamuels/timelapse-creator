import json
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image

from video import frames


def _write_jpeg(path, color=(200, 200, 200), size=(20, 20)):
    Image.new("RGB", size, color=color).save(path, format="JPEG")


def test_load_frames_uses_archive_filenames_when_no_manifest(tmp_path):
    month_dir = tmp_path / "2026" / "07"
    month_dir.mkdir(parents=True)
    _write_jpeg(month_dir / "2026-07-16T12-15-00-000000-0800.jpg")
    _write_jpeg(month_dir / "2026-07-16T12-00-00-000000-0800.jpg")

    result = frames.load_frames(tmp_path)

    assert [p.name for p, _ in result] == [
        "2026-07-16T12-00-00-000000-0800.jpg",
        "2026-07-16T12-15-00-000000-0800.jpg",
    ]
    assert result[0][1] < result[1][1]


def test_load_frames_prefers_manifest_when_present(tmp_path):
    _write_jpeg(tmp_path / "a.jpg")
    _write_jpeg(tmp_path / "b.jpg")
    manifest = {
        "a.jpg": "2026-01-01T09:00:00",
        "b.jpg": "2026-01-01T08:00:00",
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    result = frames.load_frames(tmp_path)

    # b.jpg has the earlier manifest timestamp despite sorting after a.jpg
    # by filename -- proves the manifest, not the name, drives ordering.
    assert [p.name for p, _ in result] == ["b.jpg", "a.jpg"]


def test_load_frames_with_manifest_omits_files_the_manifest_does_not_mention(tmp_path):
    _write_jpeg(tmp_path / "a.jpg")
    _write_jpeg(tmp_path / "skipped.jpg")
    (tmp_path / "manifest.json").write_text(json.dumps({"a.jpg": "2026-01-01T08:00:00"}))

    result = frames.load_frames(tmp_path)

    assert [p.name for p, _ in result] == ["a.jpg"]


def test_filter_date_range_keeps_both_bounds_inclusive():
    pacific = timezone(timedelta(hours=-8))
    frame_list = [(f"{d}.jpg", datetime(2026, 1, d, 12, 0, tzinfo=pacific)) for d in (1, 5, 10)]

    result = frames.filter_date_range(frame_list, since=date(2026, 1, 5), until=date(2026, 1, 10))

    assert [name for name, _ in result] == ["5.jpg", "10.jpg"]


def test_filter_date_range_with_no_bounds_is_a_no_op():
    frame_list = [("a.jpg", datetime(2026, 1, 1))]

    assert frames.filter_date_range(frame_list) == frame_list


def test_drop_dark_frames_keeps_bright_drops_dark(tmp_path):
    bright = tmp_path / "bright.jpg"
    dark = tmp_path / "dark.jpg"
    _write_jpeg(bright, color=(230, 230, 230))
    _write_jpeg(dark, color=(5, 5, 5))
    frame_list = [(bright, datetime(2026, 1, 1)), (dark, datetime(2026, 1, 2))]

    result = frames.drop_dark_frames(frame_list, threshold=40)

    assert [p.name for p, _ in result] == ["bright.jpg"]


def test_drop_duplicate_frames_drops_consecutive_exact_matches(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    c = tmp_path / "c.jpg"
    a.write_bytes(b"same bytes")
    b.write_bytes(b"same bytes")
    c.write_bytes(b"different bytes")
    frame_list = [
        (a, datetime(2026, 1, 1)),
        (b, datetime(2026, 1, 2)),
        (c, datetime(2026, 1, 3)),
    ]

    result = frames.drop_duplicate_frames(frame_list)

    assert [p.name for p, _ in result] == ["a.jpg", "c.jpg"]


def test_drop_duplicate_frames_keeps_non_consecutive_repeats(tmp_path):
    # a and c share bytes but aren't adjacent -- only *consecutive* repeats
    # are considered stale-frame residue.
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    c = tmp_path / "c.jpg"
    a.write_bytes(b"repeated")
    b.write_bytes(b"different")
    c.write_bytes(b"repeated")
    frame_list = [
        (a, datetime(2026, 1, 1)),
        (b, datetime(2026, 1, 2)),
        (c, datetime(2026, 1, 3)),
    ]

    result = frames.drop_duplicate_frames(frame_list)

    assert [p.name for p, _ in result] == ["a.jpg", "b.jpg", "c.jpg"]


def test_uniform_durations_gives_every_frame_equal_time():
    result = frames.uniform_durations(["a", "b", "c", "d"], fps=25)

    assert result == pytest.approx([0.04, 0.04, 0.04, 0.04])


def test_uniform_durations_rejects_non_positive_fps():
    with pytest.raises(ValueError):
        frames.uniform_durations(["a"], fps=0)


def test_proportional_durations_single_frame_gets_full_target():
    frame_list = [("a.jpg", datetime(2026, 1, 1))]

    assert frames.proportional_durations(frame_list, target_duration=10) == [10]


def test_proportional_durations_empty_list_returns_empty():
    assert frames.proportional_durations([], target_duration=10) == []


def test_proportional_durations_scales_relative_to_gap_size():
    # b..c is 6x the real-time gap of a..b; with no clamping in the way the
    # rendered hold times should preserve that same ratio.
    frame_list = [
        ("a.jpg", datetime(2026, 1, 1, 0, 0)),
        ("b.jpg", datetime(2026, 1, 1, 1, 0)),
        ("c.jpg", datetime(2026, 1, 1, 7, 0)),
    ]

    result = frames.proportional_durations(
        frame_list, target_duration=100, min_hold=0.0, max_hold=1000.0
    )

    assert result[1] == pytest.approx(result[0] * 6)


def test_proportional_durations_respects_target_when_unclamped():
    frame_list = [
        ("a.jpg", datetime(2026, 1, 1, 0, 0)),
        ("b.jpg", datetime(2026, 1, 1, 1, 0)),
        ("c.jpg", datetime(2026, 1, 1, 2, 0)),
    ]

    result = frames.proportional_durations(
        frame_list, target_duration=30, min_hold=0.0, max_hold=1000.0
    )

    assert sum(result) == pytest.approx(30)


def test_proportional_durations_max_hold_caps_a_dominant_gap():
    # A huge outlier gap (two weeks) next to routine 15-minute gaps --
    # max_hold keeps it from swallowing the whole video.
    frame_list = [
        ("a.jpg", datetime(2026, 1, 1, 0, 0)),
        ("b.jpg", datetime(2026, 1, 1, 0, 15)),
        ("c.jpg", datetime(2026, 1, 15, 0, 15)),
    ]

    result = frames.proportional_durations(frame_list, target_duration=10, max_hold=2.0)

    assert all(hold <= 2.0 for hold in result)


def test_proportional_durations_min_hold_floors_a_tiny_gap():
    frame_list = [
        ("a.jpg", datetime(2026, 1, 1, 0, 0, 0)),
        ("b.jpg", datetime(2026, 1, 1, 0, 0, 1)),
        ("c.jpg", datetime(2026, 1, 2, 0, 0, 1)),
    ]

    result = frames.proportional_durations(frame_list, target_duration=10, min_hold=0.5)

    assert result[0] >= 0.5


def test_proportional_durations_rejects_non_increasing_timestamps():
    frame_list = [
        ("a.jpg", datetime(2026, 1, 1)),
        ("b.jpg", datetime(2026, 1, 1)),
    ]

    with pytest.raises(ValueError):
        frames.proportional_durations(frame_list, target_duration=10)
