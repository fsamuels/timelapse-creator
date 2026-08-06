from unittest.mock import MagicMock, patch

from capture import fetch


def test_resolve_youtube_stream_url_calls_yt_dlp():
    fake_result = MagicMock(stdout="https://example.com/stream.m3u8\n")
    with patch("capture.fetch.subprocess.run", return_value=fake_result) as run:
        url = fetch.resolve_youtube_stream_url("https://www.youtube.com/watch?v=abc")

    args = run.call_args[0][0]
    assert args[0] == "yt-dlp"
    assert "https://www.youtube.com/watch?v=abc" in args
    assert url == "https://example.com/stream.m3u8"


def test_fetch_youtube_frame_resolves_then_grabs_a_frame():
    with (
        patch(
            "capture.fetch.resolve_youtube_stream_url",
            return_value="https://example.com/stream.m3u8",
        ) as resolve,
        patch("capture.fetch.fetch_stream_frame", return_value=b"jpegbytes") as grab,
    ):
        data = fetch.fetch_youtube_frame("https://www.youtube.com/watch?v=abc")

    resolve.assert_called_once_with("https://www.youtube.com/watch?v=abc", timeout=20)
    grab.assert_called_once_with("https://example.com/stream.m3u8", timeout=20)
    assert data == b"jpegbytes"


def test_fetch_frame_dispatches_youtube_type():
    cam = {"type": "youtube", "url": "https://www.youtube.com/watch?v=abc"}
    with patch("capture.fetch.fetch_youtube_frame", return_value=b"data") as fn:
        result = fetch.fetch_frame(cam)

    fn.assert_called_once_with("https://www.youtube.com/watch?v=abc")
    assert result == b"data"


def test_fetch_frame_dispatches_image_type_by_default():
    cam = {"type": "image", "url": "https://example.com/cam.jpg"}
    with patch("capture.fetch.fetch_image", return_value=b"data") as fn:
        result = fetch.fetch_frame(cam)

    fn.assert_called_once_with("https://example.com/cam.jpg")
    assert result == b"data"
