from capture import archive


def test_frame_hash_is_deterministic():
    data = b"some jpeg bytes"
    assert archive.frame_hash(data) == archive.frame_hash(data)


def test_frame_hash_differs_for_different_data():
    assert archive.frame_hash(b"a") != archive.frame_hash(b"b")


def test_latest_frame_returns_none_for_empty_dir(tmp_path):
    assert archive.latest_frame(tmp_path) is None


def test_latest_frame_returns_lexically_last_file(tmp_path):
    month_dir = tmp_path / "2026" / "07"
    month_dir.mkdir(parents=True)
    (month_dir / "2026-07-16T12-00-00-000000-0800.jpg").write_bytes(b"first")
    latest = month_dir / "2026-07-16T12-15-00-000000-0800.jpg"
    latest.write_bytes(b"second")

    assert archive.latest_frame(tmp_path) == latest


def test_latest_frame_stays_monotonic_across_dst_boundary(tmp_path):
    # Real Pacific local time repeats 1-2 AM on the November DST "fall back"
    # night; the fixed -0800 offset must not reproduce that ambiguity.
    month_dir = tmp_path / "2026" / "11"
    month_dir.mkdir(parents=True)
    (month_dir / "2026-11-01T01-30-00-000000-0800.jpg").write_bytes(b"first")
    latest = month_dir / "2026-11-01T02-00-00-000000-0800.jpg"
    latest.write_bytes(b"second")

    assert archive.latest_frame(tmp_path) == latest


def test_is_stale_false_when_archive_empty(tmp_path):
    assert archive.is_stale(b"new frame", tmp_path) is False


def test_is_stale_true_for_identical_bytes(tmp_path):
    month_dir = tmp_path / "2026" / "07"
    month_dir.mkdir(parents=True)
    (month_dir / "2026-07-16T12-00-00-000000-0800.jpg").write_bytes(b"same bytes")

    assert archive.is_stale(b"same bytes", tmp_path) is True


def test_is_stale_false_for_different_bytes(tmp_path):
    month_dir = tmp_path / "2026" / "07"
    month_dir.mkdir(parents=True)
    (month_dir / "2026-07-16T12-00-00-000000-0800.jpg").write_bytes(b"old frame")

    assert archive.is_stale(b"new frame", tmp_path) is False


def test_save_frame_writes_file_under_year_month_dir(tmp_path):
    path = archive.save_frame(b"jpeg bytes", tmp_path)

    assert path.read_bytes() == b"jpeg bytes"
    assert path.suffix == ".jpg"
    assert path.parent.parent.parent == tmp_path


def test_save_frame_is_findable_via_latest_frame(tmp_path):
    path = archive.save_frame(b"jpeg bytes", tmp_path)

    assert archive.latest_frame(tmp_path) == path


def test_save_frame_uses_fixed_pacific_offset_suffix(tmp_path):
    path = archive.save_frame(b"jpeg bytes", tmp_path)

    assert path.stem.endswith("-0800")


def test_parse_frame_time_roundtrips_save_frame(tmp_path):
    path = archive.save_frame(b"jpeg bytes", tmp_path)

    parsed = archive.parse_frame_time(path)
    assert parsed.strftime(archive.FRAME_TIME_FORMAT) == path.stem


def test_parse_frame_time_recovers_fields_and_offset():
    parsed = archive.parse_frame_time("2026-07-16T12-10-05-166615-0800.jpg")

    assert (parsed.year, parsed.month, parsed.day) == (2026, 7, 16)
    assert (parsed.hour, parsed.minute, parsed.second) == (12, 10, 5)
    assert parsed.utcoffset() == archive.PACIFIC.utcoffset(None)
