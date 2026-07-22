import json
from datetime import date, datetime, timedelta

from capture.archive import PACIFIC
from web import generate


def _write_frame(archive_dir, site, cam, stamp, data=b"x"):
    """Create a frame file named like save_frame does, under site/cam/YYYY/MM."""
    dt = datetime.strptime(stamp, "%Y-%m-%dT%H-%M-%S-%f%z")
    month_dir = archive_dir / site / cam / dt.strftime("%Y/%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{stamp}.jpg"
    path.write_bytes(data)
    return path


def test_scan_archive_groups_by_site_and_cam(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "base", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "seattle", "columbia", "2026-07-16T12-00-00-000000-0800")

    sites = generate.scan_archive(tmp_path)

    assert set(sites) == {"bluewood", "seattle"}
    assert set(sites["bluewood"]) == {"summit", "base"}
    assert set(sites["seattle"]) == {"columbia"}


def test_scan_archive_missing_dir_is_empty(tmp_path):
    assert generate.scan_archive(tmp_path / "nope") == {}


def test_scan_archive_skips_cams_with_no_frames(tmp_path):
    (tmp_path / "bluewood" / "summit").mkdir(parents=True)  # empty cam dir
    _write_frame(tmp_path, "bluewood", "base", "2026-07-16T12-00-00-000000-0800")

    sites = generate.scan_archive(tmp_path)

    assert set(sites["bluewood"]) == {"base"}


def test_daily_counts_buckets_by_capture_date(tmp_path):
    frames = [
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800"),
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-15-00-000000-0800"),
        _write_frame(tmp_path, "s", "c", "2026-07-17T09-00-00-000000-0800"),
    ]

    counts = generate.daily_counts(frames)

    assert counts == {date(2026, 7, 16): 2, date(2026, 7, 17): 1}


def test_read_capture_log_missing_file_is_empty(tmp_path):
    assert generate.read_capture_log(tmp_path / "capture.log") == []
    assert generate.read_capture_log(None) == []


def test_read_capture_log_tolerates_bad_trailing_line(tmp_path):
    log = tmp_path / "capture.log"
    log.write_text(json.dumps({"cam": "summit", "outcome": "saved"}) + "\n" + '{"cam": "base",')

    entries = generate.read_capture_log(log)

    assert entries == [{"cam": "summit", "outcome": "saved"}]


def test_latest_outcomes_keeps_last_entry_per_cam():
    entries = [
        {"cam": "summit", "outcome": "saved"},
        {"cam": "base", "outcome": "fetch_failed"},
        {"cam": "summit", "outcome": "stale"},
    ]

    latest = generate.latest_outcomes(entries)

    assert latest["summit"]["outcome"] == "stale"
    assert latest["base"]["outcome"] == "fetch_failed"


def test_cam_health_flags_stale_when_last_frame_old(tmp_path):
    now = datetime(2026, 7, 17, 12, 0, tzinfo=PACIFIC)
    frames = [_write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800")]

    health = generate.cam_health(frames, None, now, timedelta(hours=1))

    assert health["is_stale"] is True
    assert health["frame_count"] == 1


def test_cam_health_live_when_recent(tmp_path):
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)
    frames = [_write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800")]

    health = generate.cam_health(frames, None, now, timedelta(hours=1))

    assert health["is_stale"] is False


def test_cam_health_no_frames_is_stale():
    now = datetime(2026, 7, 16, 12, 0, tzinfo=PACIFIC)

    health = generate.cam_health([], None, now, timedelta(hours=1))

    assert health["is_stale"] is True
    assert health["last_time"] is None


def test_heatmap_grid_shape_and_end_alignment():
    counts = {date(2026, 7, 16): 5}
    end = date(2026, 7, 16)

    grid = generate.heatmap_grid(counts, end, weeks=13)

    assert len(grid) == 13
    assert all(len(week) == 7 for week in grid)
    # the end date must appear in the final week
    last_week_dates = [cell["date"] for cell in grid[-1]]
    assert end in last_week_dates
    # and its count carries through
    end_cell = next(c for c in grid[-1] if c["date"] == end)
    assert end_cell["count"] == 5
    assert end_cell["level"] >= 1


def test_heatmap_grid_marks_future_cells():
    end = date(2026, 7, 15)  # a Wednesday; later weekdays are "future"
    grid = generate.heatmap_grid({}, end, weeks=2)

    future = [c for week in grid for c in week if c["future"]]
    assert future, "expected padding cells after the end date"
    assert all(c["date"] > end for c in future)


def test_frame_bytes_sums_file_sizes(tmp_path):
    frames = [
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800", data=b"abc"),
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-15-00-000000-0800", data=b"de"),
    ]

    assert generate.frame_bytes(frames) == 5


def test_disk_usage_missing_dir_is_none(tmp_path):
    assert generate.disk_usage(tmp_path / "nope") is None


def test_disk_usage_returns_total_used_free(tmp_path):
    usage = generate.disk_usage(tmp_path)

    assert usage.keys() == {"total", "used", "free"}
    assert usage["total"] > 0


def test_human_bytes_formats_by_magnitude():
    assert generate._human_bytes(0) == "0 B"
    assert generate._human_bytes(2048) == "2.0 KB"
    assert generate._human_bytes(3 * 1024**3) == "3.0 GB"


def test_ensure_archive_link_creates_symlink(tmp_path):
    archive_dir = tmp_path / "archive"
    www_dir = tmp_path / "www"
    archive_dir.mkdir()
    www_dir.mkdir()

    generate.ensure_archive_link(www_dir, archive_dir)

    link = www_dir / "archive"
    assert link.is_symlink()
    assert link.resolve() == archive_dir.resolve()


def test_ensure_archive_link_is_idempotent(tmp_path):
    archive_dir = tmp_path / "archive"
    www_dir = tmp_path / "www"
    archive_dir.mkdir()
    www_dir.mkdir()

    generate.ensure_archive_link(www_dir, archive_dir)
    generate.ensure_archive_link(www_dir, archive_dir)  # should not raise

    assert (www_dir / "archive").resolve() == archive_dir.resolve()


def test_ensure_archive_link_skips_missing_archive(tmp_path):
    www_dir = tmp_path / "www"
    www_dir.mkdir()

    generate.ensure_archive_link(www_dir, tmp_path / "nope")

    assert not (www_dir / "archive").exists()


def test_ensure_archive_link_does_not_clobber_existing_path(tmp_path):
    archive_dir = tmp_path / "archive"
    www_dir = tmp_path / "www"
    archive_dir.mkdir()
    www_dir.mkdir()
    (www_dir / "archive").mkdir()  # a real directory, not a symlink

    generate.ensure_archive_link(www_dir, archive_dir)

    assert not (www_dir / "archive").is_symlink()


def test_build_page_data_looks_up_cam_url(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path,
        None,
        now,
        timedelta(hours=1),
        cam_config={"summit": {"url": "https://example.com/summit.jpg"}},
    )

    assert data["sites"][0]["cams"][0]["url"] == "https://example.com/summit.jpg"


def test_build_page_data_url_missing_for_unconfigured_cam(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now, timedelta(hours=1))

    assert data["sites"][0]["cams"][0]["url"] is None


def test_render_html_links_cam_name_to_its_url(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path,
        None,
        now,
        timedelta(hours=1),
        cam_config={"summit": {"url": "https://example.com/summit.jpg"}},
    )
    doc = generate.render_html(data, now, timedelta(hours=1))

    assert '<a href="https://example.com/summit.jpg"' in doc
    assert ">summit</a>" in doc


def test_render_html_is_self_contained_and_shows_cams(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now, timedelta(hours=1))
    doc = generate.render_html(data, now, timedelta(hours=1))

    assert doc.startswith("<!doctype html>")
    assert "bluewood" in doc and "summit" in doc
    assert "http" not in doc.split("<style>")[1].split("</style>")[0]  # no external assets
    assert "<script" not in doc


def test_render_html_defaults_to_dark_theme_with_a_selector(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now, timedelta(hours=1))
    doc = generate.render_html(data, now, timedelta(hours=1))

    assert '<html lang="en" data-theme="dark">' in doc
    assert '<option value="dark" selected>Dark</option>' in doc
    assert '<option value="light">Light</option>' in doc
    assert '<option value="system">System</option>' in doc
    assert "localStorage" not in doc  # no persistence, by design


def test_render_html_shows_disk_usage_and_archive_link(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now, timedelta(hours=1))
    doc = generate.render_html(data, now, timedelta(hours=1))

    assert '<a href="archive/">browse the full archive</a>' in doc
    assert "free of" in doc
    assert "<th>Disk</th>" in doc


def test_build_page_data_pairs_health_with_log(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    log = tmp_path / "capture.log"
    log.write_text(json.dumps({"cam": "summit", "outcome": "saved", "detail": "ok"}) + "\n")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, log, now, timedelta(hours=1))

    summit = data["sites"][0]["cams"][0]
    assert summit["name"] == "summit"
    assert summit["health"]["outcome"]["outcome"] == "saved"
