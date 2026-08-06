import subprocess

import requests

USER_AGENT = "Mozilla/5.0 (compatible; timelapse-creator/1.0)"


def fetch_image(url, timeout=15):
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.content


def fetch_stream_frame(url, timeout=20):
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            url,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
        ],
        capture_output=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout


def resolve_youtube_stream_url(url, timeout=20):
    """Resolve a YouTube watch/channel URL to a direct stream URL ffmpeg can open.

    ffmpeg can't parse a youtube.com page itself (YouTube's stream URLs are
    signed/cipher-protected and change), so yt-dlp does that resolution step.
    """
    result = subprocess.run(
        ["yt-dlp", "-g", "-f", "best", url],
        capture_output=True,
        timeout=timeout,
        check=True,
        text=True,
    )
    return result.stdout.strip().splitlines()[0]


def fetch_youtube_frame(url, timeout=20):
    stream_url = resolve_youtube_stream_url(url, timeout=timeout)
    return fetch_stream_frame(stream_url, timeout=timeout)


def fetch_frame(cam):
    if cam["type"] == "stream":
        return fetch_stream_frame(cam["url"])
    if cam["type"] == "youtube":
        return fetch_youtube_frame(cam["url"])
    return fetch_image(cam["url"])
