# timelapse-creator

Tools for building timelapse videos from the [Ski Bluewood webcams](https://bluewood.com/webcams/)
(Dayton, WA) — and eventually any public webcam.

**Status: capture pipeline is live.** A GitHub Actions cron job has been fetching frames
from both cams every 15 minutes since 2026-07-16 and committing them straight to
`archive/` on `main`. The video builder (turning the archive into an mp4) is not built yet.
A Raspberry Pi Zero W is in transit (ordered 2026-07-16, ETA ~12-18 days) to eventually take
over capture from GitHub Actions — see `docs/open-questions.md` for the plan.

## The idea

Bluewood publishes two webcams (Summit and Base). This project will:

1. **Capture** frames from those cams on a regular schedule, all season long.
2. **Archive** every frame raw, with timestamped filenames.
3. **Build** timelapse videos from the archive on demand (daily clips, a season-long video,
   or arbitrary date ranges).

The defining constraint: Bluewood is **100% off-grid**. The webcams only work while the
resort's generator is running, so outages are the norm — every night, every closed day, the
whole off-season. The system must treat "cam is down" as ordinary operation, not an error.

## Documents

| Document | Contents |
| --- | --- |
| [docs/design.md](docs/design.md) | Architecture: the capture job, the video builder, storage layout, and outage/stale-frame handling |
| [docs/open-questions.md](docs/open-questions.md) | Decisions made so far and what's still open (output format, gap handling in video, long-term storage), with options and recommendations |

## What's implemented

- `capture/config.yaml` — the two cams, as direct CameraFTP JPEG URLs
- `capture/fetch.py` — fetches an image (or grabs a frame from a stream via ffmpeg, unused so far — both cams are plain images)
- `capture/archive.py` — SHA-256 stale/duplicate detection, timestamped file writes
- `capture/main.py` — entrypoint: fetch each cam, skip failures/stale frames, save new ones
- `.github/workflows/capture.yml` — runs the above every 15 minutes, commits new frames to `archive/`

## Not implemented yet

- The video builder (archive → mp4)
- Anything running on the Pi (still in transit) — capture is 100% on GitHub Actions for now
- Long-term storage (frames are living in git as a deliberate short-term stopgap)

## Quick summary of decisions so far

- **Language/tools:** Python for the capture job; **ffmpeg** planned for the not-yet-built video builder.
- **Cadence:** every 15 minutes (decided; see `docs/open-questions.md` #2).
- **Archive:** raw JPEGs named by cam and a fixed UTC-8 (Pacific, no DST) timestamp; never
  filtered at capture time.
- **Outages:** failed fetches are logged and skipped; *stale* frames (cam down but still
  serving its last cached image) are detected by content hash and discarded.
- **Capture platform:** GitHub Actions now; a Raspberry Pi Zero W is in transit and will take
  over via a systemd timer, with GitHub Actions running in parallel for a short trial before
  being disabled (see `docs/open-questions.md` #1).
- **Frame storage:** local disk on the Pi, synced to a cloud bucket (provider still open —
  AWS S3 vs. Backblaze B2 vs. Google Drive, see `docs/open-questions.md` #5).
- **Web interface:** a status/activity dashboard is planned — home-network-only, a
  statically-regenerated Python page (no app server) reusing the archive's own filenames for
  the activity graph, plus a new persisted capture log for health status (see
  `docs/open-questions.md` #9 and #10).

Still genuinely open — see [docs/open-questions.md](docs/open-questions.md): what the video
builder's output looks like (season video / daily clips / on-demand CLI), how outages should
appear in the rendered video, and which bucket provider to use for frame backup.
