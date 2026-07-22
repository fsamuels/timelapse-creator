import pytest

from video import encode


def test_build_concat_script_rejects_empty_input():
    with pytest.raises(ValueError):
        encode.build_concat_script([])


def test_build_concat_script_has_one_duration_per_frame(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.touch()
    b.touch()

    script = encode.build_concat_script([(a, 0.5), (b, 1.25)])

    assert script.count("duration") == 2
    assert "duration 0.500000" in script
    assert "duration 1.250000" in script


def test_build_concat_script_repeats_last_frame_with_no_trailing_duration(tmp_path):
    # ffmpeg's concat demuxer ignores the last "duration" line, so the
    # workaround is to repeat that frame's file line once more with no
    # duration after it.
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.touch()
    b.touch()

    script = encode.build_concat_script([(a, 0.5), (b, 1.25)])
    lines = script.strip().splitlines()

    assert lines[-1] == f"file '{b.resolve()}'"
    assert lines.count(f"file '{b.resolve()}'") == 2


def test_build_concat_script_escapes_single_quotes_in_paths(tmp_path):
    weird_dir = tmp_path / "it's a dir"
    weird_dir.mkdir()
    frame = weird_dir / "a.jpg"
    frame.touch()

    script = encode.build_concat_script([(frame, 1.0)])

    assert "it'\\''s a dir" in script
