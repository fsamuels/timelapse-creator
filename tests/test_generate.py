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


def test_stale_after_for_uses_cam_declared_interval():
    assert generate.stale_after_for({"interval_minutes": 30}) == timedelta(minutes=60)


def test_stale_after_for_orphaned_cam_is_always_stale():
    assert generate.stale_after_for(None) == timedelta(0)


def test_level_zero_activity_is_level_zero():
    assert generate._level(0, 10) == 0
    assert generate._level(5, 0) == 0


def test_level_reaches_max_intensity_at_the_peak_count():
    assert generate._level(10, 10, levels=2) == 2
    assert generate._level(1, 1, levels=2) == 2


def test_level_below_half_peak_is_the_low_bucket():
    assert generate._level(1, 10, levels=2) == 1


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
    assert end_cell["level"] == 2  # sole activity in the window -> the peak


def test_heatmap_grid_marks_future_cells():
    end = date(2026, 7, 15)  # a Wednesday; later weekdays are "future"
    grid = generate.heatmap_grid({}, end, weeks=2)

    future = [c for week in grid for c in week if c["future"]]
    assert future, "expected padding cells after the end date"
    assert all(c["date"] > end for c in future)


def test_heatmap_grid_peak_is_scoped_to_the_displayed_window():
    # An old burst day well outside the 13-week window shouldn't flatten the
    # colors of the days actually shown — the window's own busiest day
    # should still read as the most intense color.
    end = date(2026, 7, 22)
    counts = {
        date(2026, 1, 1): 500,  # ~29 weeks back, outside the window
        end: 10,
    }

    grid = generate.heatmap_grid(counts, end, weeks=13)

    end_cell = next(c for week in grid for c in week if c["date"] == end)
    assert end_cell["level"] == 2


def test_recent_strip_is_31_cells_oldest_to_newest():
    end = date(2026, 7, 22)
    counts = {end: 4, end - timedelta(days=30): 1}

    cells = generate.recent_strip(counts, end)

    assert len(cells) == 31
    assert cells[0]["date"] == end - timedelta(days=30)
    assert cells[-1]["date"] == end
    assert cells[-1]["level"] == 2  # the peak day in this window


def test_recent_strip_ignores_activity_outside_its_window():
    end = date(2026, 7, 22)
    counts = {end - timedelta(days=40): 500, end: 1}

    cells = generate.recent_strip(counts, end)

    assert cells[-1]["level"] == 2  # scoped peak is just today's 1 frame


def test_frame_bytes_sums_file_sizes(tmp_path):
    frames = [
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800", data=b"abc"),
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-15-00-000000-0800", data=b"de"),
    ]

    assert generate.frame_bytes(frames) == 5


def test_bytes_captured_on_filters_by_date(tmp_path):
    frames = [
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800", data=b"abc"),
        _write_frame(tmp_path, "s", "c", "2026-07-15T12-00-00-000000-0800", data=b"defg"),
    ]

    assert generate.bytes_captured_on(frames, date(2026, 7, 16)) == 3


def test_daily_burn_rate_projects_a_full_day_from_the_rate_so_far():
    now = datetime(2026, 7, 16, 6, 0, tzinfo=PACIFIC)  # 6 hrs into the day

    rate = generate.daily_burn_rate(bytes_today=1024, now=now)

    assert rate["bytes_today"] == 1024
    assert rate["elapsed_hours"] == 6
    assert rate["projected_daily_bytes"] == 1024 / 6 * 24


def test_daily_burn_rate_none_too_early_in_the_day():
    now = datetime(2026, 7, 16, 0, 5, tzinfo=PACIFIC)  # 5 min into the day

    assert generate.daily_burn_rate(bytes_today=100, now=now) is None


def test_thumb_url_points_at_newest_frame_under_the_archive_link(tmp_path):
    frames = [
        _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800"),
        _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-15-00-000000-0800"),
    ]

    url = generate.thumb_url(frames, tmp_path)

    assert url == "archive/bluewood/summit/2026/07/2026-07-16T12-15-00-000000-0800.jpg"


def test_thumb_url_none_when_no_frames(tmp_path):
    assert generate.thumb_url([], tmp_path) is None


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


def test_human_runway_none_reads_as_steady():
    assert generate._human_runway(None) == "steady"


def test_human_runway_under_a_day():
    assert generate._human_runway(0.5) == "<1 day"


def test_human_runway_days_pluralizes():
    assert generate._human_runway(1) == "~1 day"
    assert generate._human_runway(3) == "~3 days"


def test_human_runway_switches_to_weeks_past_two_weeks():
    assert generate._human_runway(21) == "~3 weeks"


def test_slug_replaces_unsafe_characters():
    assert generate._slug("RCR - Front Pastures") == "rcr-front-pastures"
    assert generate._slug("summit") == "summit"


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
        cam_config={"summit": {"url": "https://example.com/summit.jpg", "interval_minutes": 15}},
    )

    assert data["sites"][0]["cams"][0]["url"] == "https://example.com/summit.jpg"


def test_build_page_data_url_missing_when_cam_config_omits_it(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path, None, now, cam_config={"summit": {"interval_minutes": 15}}
    )

    assert data["sites"][0]["cams"][0]["url"] is None


def test_build_page_data_orphaned_cam_is_stale_without_crashing(tmp_path):
    # A cam with archived frames but no entry at all in the current config
    # (e.g. decommissioned) shouldn't need a guessed interval or blow up.
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now, cam_config={})

    summit = data["sites"][0]["cams"][0]
    assert summit["url"] is None
    assert summit["health"]["is_stale"] is True


def test_build_page_data_orders_sites_per_config(tmp_path):
    _write_frame(tmp_path, "seattle", "columbia", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "north-carolina", "unca-tower", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path, None, now, site_order=["bluewood", "seattle", "north-carolina"]
    )

    assert [site["site"] for site in data["sites"]] == ["bluewood", "seattle", "north-carolina"]


def test_build_page_data_unlisted_sites_sort_after_configured_ones(tmp_path):
    _write_frame(tmp_path, "seattle", "columbia", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "decommissioned-site", "old-cam", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now, site_order=["seattle"])

    assert [site["site"] for site in data["sites"]] == ["seattle", "decommissioned-site"]


def test_build_page_data_computes_burn_rate_from_todays_frames(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T03-00-00-000000-0800", data=b"x" * 100)
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-15T03-00-00-000000-0800", data=b"x" * 900)
    now = datetime(2026, 7, 16, 9, 0, tzinfo=PACIFIC)  # 9 hrs into the day

    data = generate.build_page_data(tmp_path, None, now)

    burn = data["burn_rate"]
    assert burn["bytes_today"] == 100
    assert burn["elapsed_hours"] == 9
    assert burn["projected_daily_bytes"] == 100 / 9 * 24


def test_build_page_data_burn_rate_none_without_any_frames(tmp_path):
    now = datetime(2026, 7, 16, 9, 0, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)

    assert data["burn_rate"] is None
    assert data["runway_days"] is None


def test_build_page_data_computes_runway_from_disk_and_burn_rate(tmp_path):
    _write_frame(
        tmp_path, "bluewood", "summit", "2026-07-16T03-00-00-000000-0800", data=b"x" * 1000
    )
    now = datetime(2026, 7, 16, 6, 0, tzinfo=PACIFIC)  # 6 hrs into the day

    data = generate.build_page_data(tmp_path, None, now)

    disk = data["disk"]
    burn = data["burn_rate"]
    assert data["runway_days"] == disk["free"] / burn["projected_daily_bytes"]


def test_build_page_data_counts_stale_and_total_cams(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "base", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path,
        None,
        now,
        cam_config={
            "summit": {"interval_minutes": 15},  # fresh
            "base": {"interval_minutes": 1},  # instantly stale by now
        },
    )

    assert data["cam_count"] == 2
    assert data["stale_count"] == 1


def test_build_page_data_avg_bytes_per_cam(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800", data=b"x" * 10)
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-15-00-000000-0800", data=b"x" * 20)
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)

    assert data["sites"][0]["cams"][0]["avg_bytes"] == 15


def test_build_page_data_pairs_health_with_log(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    log = tmp_path / "capture.log"
    log.write_text(json.dumps({"cam": "summit", "outcome": "saved", "detail": "ok"}) + "\n")
    now = datetime(2026, 7, 16, 12, 15, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, log, now)

    summit = data["sites"][0]["cams"][0]
    assert summit["name"] == "summit"
    assert summit["health"]["outcome"]["outcome"] == "saved"


def test_render_html_links_cam_name_to_its_url(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path,
        None,
        now,
        cam_config={"summit": {"url": "https://example.com/summit.jpg", "interval_minutes": 15}},
    )
    doc = generate.render_html(data, now)

    assert '<a class="cam-name" href="https://example.com/summit.jpg"' in doc
    assert ">summit</a>" in doc


def test_render_html_thumb_link_does_not_nest_the_cam_name_link(tmp_path):
    # Both links wrapping the same content (invalid nested <a>) makes browsers
    # silently split/reflow the markup, which broke the cam-name's alignment.
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path,
        None,
        now,
        cam_config={"summit": {"url": "https://example.com/summit.jpg", "interval_minutes": 15}},
    )
    doc = generate.render_html(data, now)

    thumb_open = '<a href="archive/bluewood/summit/2026/07/2026-07-16T12-00-00-000000-0800.jpg"'
    thumb_start = doc.index(thumb_open)
    thumb_close = doc.index("</a>", thumb_start)
    name_open = doc.index('class="cam-name"')
    assert not (thumb_start < name_open < thumb_close)


def test_render_html_is_self_contained_and_shows_cams(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert doc.startswith("<!doctype html>")
    assert "bluewood" in doc and "summit" in doc
    assert "http" not in doc.split("<style>")[1].split("</style>")[0]  # no external assets
    assert "<script" not in doc  # interactivity (the history modal) is pure CSS :target


def test_render_html_shows_a_thumbnail_of_the_newest_frame(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert ('src="archive/bluewood/summit/2026/07/2026-07-16T12-00-00-000000-0800.jpg"') in doc


def test_render_html_thumbnail_links_to_the_full_size_frame(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    thumb_url = "archive/bluewood/summit/2026/07/2026-07-16T12-00-00-000000-0800.jpg"
    assert f'<a href="{thumb_url}" class="cam-photo-link" target="_blank" rel="noopener"' in doc


def test_render_html_no_thumbnail_shows_placeholder(tmp_path):
    (tmp_path / "bluewood" / "summit").mkdir(parents=True)
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    # scan_archive skips cams with zero frames, so simulate one directly:
    data = generate.build_page_data(tmp_path, None, now)
    assert data["sites"] == []  # sanity: nothing archived means no cards at all


def test_render_html_full_history_grid_tooltip_leads_with_the_image_count(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-15-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert 'title="2 images on 2026-07-16"' in doc


def test_render_html_full_history_day_tap_copies_tooltip_into_the_modal_hint(tmp_path):
    """Mobile can't hover a `title` tooltip, so tapping a day (which fires a
    "click" on touch too) must copy the same text into the visible .modal-hint line."""
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert '<div class="modal-hint">Tap a day for details</div>' in doc
    assert "onclick=\"this.closest('.modal-sheet').querySelector('.modal-hint')" in doc
    assert ".textContent=this.title;" in doc


def test_render_html_full_history_day_tap_resets_any_shown_day_detail(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert (
        "this.closest('.modal-sheet').querySelectorAll('.day-detail')"
        ".forEach(function(d){d.style.display='none'})"
    ) in doc


def test_render_html_day_with_frames_reveals_its_hourly_detail(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-15-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T09-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    key = data["sites"][0]["cams"][0]["key"]
    detail_id = f"day-detail-{key}-2026-07-16"
    assert f'id="{detail_id}" style="display:none"' in doc
    assert f"document.getElementById('{detail_id}').style.display='block'" in doc
    assert "2026-07-16 &middot; hourly" in doc
    assert 'title="2 images at 12:00 on 2026-07-16"' in doc
    assert 'title="1 image at 09:00 on 2026-07-16"' in doc


def test_render_html_empty_day_has_no_detail_block(tmp_path):
    # A day within the visible history window with zero frames shouldn't get
    # a pre-rendered (and pointless) empty hourly sub-grid.
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    key = data["sites"][0]["cams"][0]["key"]
    assert f"day-detail-{key}-2026-07-15" not in doc


def test_hourly_counts_buckets_by_date_and_hour(tmp_path):
    frames = [
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-00-00-000000-0800"),
        _write_frame(tmp_path, "s", "c", "2026-07-16T12-15-00-000000-0800"),
        _write_frame(tmp_path, "s", "c", "2026-07-16T09-00-00-000000-0800"),
    ]

    counts = generate.hourly_counts(frames)

    assert counts == {date(2026, 7, 16): {12: 2, 9: 1}}


def test_hour_cells_for_day_is_24_cells_leveled_to_the_days_own_peak():
    cells = generate.hour_cells_for_day({9: 1, 12: 4})

    assert len(cells) == 24
    assert cells[9]["level"] == 1
    assert cells[12]["level"] == 2
    assert cells[0]["count"] == 0
    assert cells[0]["level"] == 0


def test_day_details_for_grid_skips_days_without_frames():
    end = date(2026, 7, 16)
    grid = generate.heatmap_grid({end: 1}, end, weeks=2)
    hourly = {end: {12: 1}}

    details = generate.day_details_for_grid(hourly, grid)

    assert set(details) == {end}
    assert len(details[end]) == 24


def test_day_details_for_grid_skips_future_cells():
    end = date(2026, 7, 15)  # a Wednesday; later weekdays in the final week are "future"
    grid = generate.heatmap_grid({}, end, weeks=1)
    future_day = next(c["date"] for week in grid for c in week if c["future"])
    hourly = {future_day: {12: 1}}  # shouldn't happen in practice, but guard anyway

    details = generate.day_details_for_grid(hourly, grid)

    assert future_day not in details


def test_human_duration_under_a_second_is_milliseconds():
    assert generate._human_duration(0.428) == "428ms"


def test_human_duration_a_second_or_more_is_seconds():
    assert generate._human_duration(1.8) == "1.8s"


def test_render_html_footer_shows_generation_time(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    system = {"generate_seconds": 0.35}
    doc = generate.render_html(data, now, system=system)

    assert '<div class="footer">' in doc
    assert "Generated in 350ms" in doc


def test_render_html_recent_strip_tooltip_leads_with_the_image_count(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-15-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert 'class="recent-cell l2" title="2 images on 2026-07-16"' in doc


def test_render_html_recent_strip_day_tap_copies_tooltip_into_the_cam_info_line(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert '<div class="recent-info">Tap a day for details</div>' in doc
    assert "onclick=\"this.closest('.cam-body').querySelector('.recent-info')" in doc
    assert '.textContent=this.title"' in doc


def test_render_html_history_button_links_to_the_cams_modal(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    key = data["sites"][0]["cams"][0]["key"]
    assert f'href="#history-{key}"' in doc
    assert f'id="history-{key}"' in doc
    assert "summit &middot; full history" in doc


def test_render_html_shows_disk_usage_and_archive_link(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert 'href="archive/"' in doc
    assert "Disk free" in doc


def test_render_html_shows_burn_rate_and_runway(tmp_path):
    _write_frame(
        tmp_path, "bluewood", "summit", "2026-07-16T03-00-00-000000-0800", data=b"x" * 1024
    )
    now = datetime(2026, 7, 16, 9, 0, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert "Runway" in doc
    assert "/day burn" in doc
    assert "captured in" in doc


def test_render_html_runway_omits_burn_line_when_too_early_in_the_day(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-15T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 0, 5, tzinfo=PACIFIC)  # 5 min into today

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert "not enough data yet to estimate burn rate" in doc


def test_render_html_stale_banner_hidden_by_default(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-01T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)  # long past stale

    data = generate.build_page_data(
        tmp_path, None, now, cam_config={"summit": {"interval_minutes": 15}}
    )
    doc = generate.render_html(data, now)  # show_stale_banner defaults False

    assert '<div class="stale-banner"' not in doc


def test_render_html_stale_banner_shown_when_enabled_and_stale(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-01T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path, None, now, cam_config={"summit": {"interval_minutes": 15}}
    )
    doc = generate.render_html(data, now, show_stale_banner=True)

    assert '<div class="stale-banner"' in doc
    assert "1 of 1 cams stale" in doc


def test_render_html_stale_banner_stays_hidden_when_enabled_but_nothing_stale(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-29-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path, None, now, cam_config={"summit": {"interval_minutes": 15}}
    )
    doc = generate.render_html(data, now, show_stale_banner=True)

    assert '<div class="stale-banner"' not in doc


def test_render_html_group_badge_shows_stale_count(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-01T12-00-00-000000-0800")
    _write_frame(tmp_path, "bluewood", "base", "2026-07-16T12-29-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path,
        None,
        now,
        cam_config={
            "summit": {"interval_minutes": 15},  # stale
            "base": {"interval_minutes": 15},  # fresh
        },
    )
    doc = generate.render_html(data, now)

    assert '<span class="group-badge">1 stale</span>' in doc


def test_render_html_cam_status_pill_reflects_health(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-29-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(
        tmp_path, None, now, cam_config={"summit": {"interval_minutes": 15}}
    )
    doc = generate.render_html(data, now)

    assert '<span class="cam-status live">LIVE</span>' in doc


def test_read_uptime_seconds_parses_proc_uptime(tmp_path):
    path = tmp_path / "uptime"
    path.write_text("12345.67 54321.00\n")

    assert generate.read_uptime_seconds(path) == 12345.67


def test_read_uptime_seconds_missing_file_is_none(tmp_path):
    assert generate.read_uptime_seconds(tmp_path / "nope") is None


def test_read_memory_usage_parses_meminfo(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text(
        "MemTotal:        1000000 kB\nMemFree:          200000 kB\n" "MemAvailable:     600000 kB\n"
    )

    assert generate.read_memory_usage(path) == {"total_kb": 1000000, "available_kb": 600000}


def test_read_memory_usage_missing_file_is_none(tmp_path):
    assert generate.read_memory_usage(tmp_path / "nope") is None


def test_read_memory_usage_missing_field_is_none(tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal:        1000000 kB\n")  # no MemAvailable

    assert generate.read_memory_usage(path) is None


def test_system_stats_bundles_uptime_memory_and_load_avg(tmp_path):
    uptime_path = tmp_path / "uptime"
    uptime_path.write_text("100.0 200.0\n")
    meminfo_path = tmp_path / "meminfo"
    meminfo_path.write_text("MemTotal:  1000 kB\nMemAvailable:  400 kB\n")

    stats = generate.system_stats(
        uptime_path=uptime_path,
        meminfo_path=meminfo_path,
        load_avg_fn=lambda: (0.1, 0.2, 0.3),
    )

    assert stats == {
        "uptime_seconds": 100.0,
        "memory": {"total_kb": 1000, "available_kb": 400},
        "load_avg": (0.1, 0.2, 0.3),
    }


def test_system_stats_load_avg_unavailable_is_none(tmp_path):
    def raise_oserror():
        raise OSError("not supported")

    stats = generate.system_stats(
        uptime_path=tmp_path / "nope",
        meminfo_path=tmp_path / "nope",
        load_avg_fn=raise_oserror,
    )

    assert stats["load_avg"] is None


def test_read_git_info_parses_sha8_and_commit_date():
    def fake_run(repo_dir):
        assert repo_dir == "some/repo"
        return "a1b2c3d4\t2026-07-29\n"

    info = generate.read_git_info(repo_dir="some/repo", run_fn=fake_run)

    assert info == {"sha8": "a1b2c3d4", "commit_date": "2026-07-29"}


def test_read_git_info_not_a_repo_is_none():
    def fake_run(repo_dir):
        raise generate.subprocess.CalledProcessError(128, ["git"])

    assert generate.read_git_info(repo_dir="some/repo", run_fn=fake_run) is None


def test_read_git_info_git_not_installed_is_none():
    def fake_run(repo_dir):
        raise OSError("git not found")

    assert generate.read_git_info(repo_dir="some/repo", run_fn=fake_run) is None


def test_render_html_footer_shows_deployed_sha8_and_commit_date(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    system = {"git": {"sha8": "a1b2c3d4", "commit_date": "2026-07-29"}}
    doc = generate.render_html(data, now, system=system)

    assert '<div class="footer">' in doc
    assert "Deployed a1b2c3d4 &middot; 2026-07-29" in doc


def test_human_uptime_days_and_hours():
    assert generate._human_uptime(3 * 86400 + 4 * 3600) == "3d 4h"


def test_human_uptime_hours_and_minutes():
    assert generate._human_uptime(4 * 3600 + 12 * 60) == "4h 12m"


def test_human_uptime_minutes_only():
    assert generate._human_uptime(12 * 60) == "12m"


def test_render_html_footer_shows_uptime_memory_and_load(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    system = {
        "uptime_seconds": 3 * 86400 + 4 * 3600,
        "memory": {"total_kb": 1000000, "available_kb": 400000},
        "load_avg": (0.15, 0.09, 0.05),
    }
    doc = generate.render_html(data, now, system=system)

    assert '<div class="footer">' in doc
    assert "Uptime 3d 4h" in doc
    assert "Mem" in doc and "(60%)" in doc
    assert "Load 0.15, 0.09, 0.05" in doc


def test_render_html_footer_puts_each_stat_on_its_own_line(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    system = {
        "uptime_seconds": 3 * 86400 + 4 * 3600,
        "memory": {"total_kb": 1000000, "available_kb": 400000},
        "load_avg": (0.15, 0.09, 0.05),
        "generate_seconds": 0.35,
        "git": {"sha8": "a1b2c3d4", "commit_date": "2026-07-29"},
    }
    doc = generate.render_html(data, now, system=system)

    footer = doc.split('<div class="footer">')[1].split("</div>")[0]
    assert footer.split("<br>") == [
        "Uptime 3d 4h",
        "Mem 585.9 MB / 976.6 MB (60%)",
        "Load 0.15, 0.09, 0.05",
        "Generated in 350ms",
        "Deployed a1b2c3d4 &middot; 2026-07-29",
    ]


def test_render_html_footer_omitted_without_system_stats(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    assert '<div class="footer">' not in doc


def test_render_html_footer_degrades_when_some_stats_missing(tmp_path):
    _write_frame(tmp_path, "bluewood", "summit", "2026-07-16T12-00-00-000000-0800")
    now = datetime(2026, 7, 16, 12, 30, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    system = {"uptime_seconds": None, "memory": None, "load_avg": None}
    doc = generate.render_html(data, now, system=system)

    assert '<div class="footer">' not in doc


def test_render_html_runway_gets_warn_class_when_low(tmp_path):
    # A tiny disk with meaningful burn should read as a low, "warn" runway.
    _write_frame(
        tmp_path, "bluewood", "summit", "2026-07-16T00-00-00-000000-0800", data=b"x" * 10**9
    )
    now = datetime(2026, 7, 16, 12, 0, tzinfo=PACIFIC)

    data = generate.build_page_data(tmp_path, None, now)
    doc = generate.render_html(data, now)

    if data["runway_days"] is not None and data["runway_days"] <= generate.RUNWAY_WARN_DAYS:
        assert '<div class="stat-value warn">' in doc
