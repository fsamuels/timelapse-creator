"""ffmpeg invocation for the video builder.

build_concat_script is pure (list of (path, duration) in, script text out)
and unit-tested; run_ffmpeg shells out to the real ffmpeg binary and isn't
-- matching how capture/fetch.py's network calls aren't unit-tested either.
"""

import subprocess
from pathlib import Path


def _quote(path):
    # ffmpeg's concat demuxer uses its own quoting, not shell quoting: a
    # value wrapped in single quotes, with any embedded single quote
    # escaped as '\''.
    return "'" + str(Path(path).resolve()).replace("'", "'\\''") + "'"


def build_concat_script(frames_with_durations):
    """Build an ffmpeg concat-demuxer script from [(path, duration), ...].

    ffmpeg's concat demuxer ignores the last "duration" directive in the
    file (a documented quirk of the format) -- so the last frame's path is
    repeated once more with no trailing duration, which is the standard
    workaround to make its hold time actually take effect.
    """
    if not frames_with_durations:
        raise ValueError("no frames to encode")

    lines = []
    for path, duration in frames_with_durations:
        lines.append(f"file {_quote(path)}")
        lines.append(f"duration {duration:.6f}")
    lines.append(f"file {_quote(frames_with_durations[-1][0])}")
    return "\n".join(lines) + "\n"


def run_ffmpeg(concat_script, output_path, tmp_dir):
    """Write concat_script to tmp_dir and encode it to output_path as an
    H.264/yuv420p mp4 -- universal playback compatibility, matching
    docs/design.md's decided output format.
    """
    concat_path = Path(tmp_dir) / "concat.txt"
    concat_path.write_text(concat_script)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vsync",
            "vfr",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(output_path),
        ],
        capture_output=True,
        check=True,
    )
