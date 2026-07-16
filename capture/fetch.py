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
            "ffmpeg", "-y", "-i", url,
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
        ],
        capture_output=True, timeout=timeout, check=True,
    )
    return result.stdout


def fetch_frame(cam):
    if cam["type"] == "stream":
        return fetch_stream_frame(cam["url"])
    return fetch_image(cam["url"])
