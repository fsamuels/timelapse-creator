# timelapse-creator

Tools for building timelapse videos from the [Ski Bluewood webcams](https://bluewood.com/webcams/)
(Dayton, WA) — and eventually any public webcam.

**Status: capture pipeline is live, Pi hand-off trial underway.** A GitHub Actions cron job
has been fetching frames from both Bluewood cams every 15 minutes since 2026-07-16 and
committing them straight to `archive/` on `main`. A Raspberry Pi Zero W has since arrived and
is now also capturing on a systemd timer — Seattle (KING 5) cams first (added while Bluewood
was off-grid), and Bluewood as of the trial start, running in parallel with GitHub Actions per
the hand-off plan in `docs/open-questions.md`. The video builder (turning the archive into an
mp4) is not built yet.

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

- `capture/config.yaml` — the two Bluewood cams, as direct CameraFTP JPEG URLs (used by GitHub Actions)
- `capture/config.pi.yaml` — a second config, for the Pi: two Seattle (KING 5) cams (added to
  keep developing the pipeline while Bluewood was off-grid) plus Bluewood itself (added once
  the Pi was confirmed reliable, starting the GitHub Actions/Pi trial), and a `capture_log` path
- `capture/fetch.py` — fetches an image (or grabs a frame from a stream via ffmpeg, unused so far — both cams are plain images)
- `capture/archive.py` — SHA-256 stale/duplicate detection, timestamped file writes
- `capture/main.py` — entrypoint: takes an optional `--config` (defaults to `capture/config.yaml`,
  preserving today's behavior), fetches each cam, skips failures/stale frames, saves new ones, and
  appends to a persisted capture log when the config provides a `capture_log` path
- `capture/capture_log.py` — appends one JSONL line per cam per run (timestamp, outcome, detail)
- `.github/workflows/capture.yml` — runs `capture/main.py` with no args every 15 minutes, commits new Bluewood frames to `archive/`
- `deploy/pi/` — systemd service + timer units and a bring-up doc for running capture on the Pi

## Not implemented yet

- The video builder (archive → mp4)
- The bucket sync (`rclone`) and the status/activity web dashboard
- Long-term storage (frames are living in git as a deliberate short-term stopgap) — this ends
  once the GitHub Actions/Pi trial concludes and `archive/` stops being tracked in git

## Quick summary of decisions so far

- **Language/tools:** Python for the capture job; **ffmpeg** planned for the not-yet-built video builder.
- **Cadence:** every 15 minutes (decided; see `docs/open-questions.md` #2).
- **Archive:** raw JPEGs named by cam and a fixed UTC-8 (Pacific, no DST) timestamp; never
  filtered at capture time.
- **Outages:** failed fetches are logged and skipped; *stale* frames (cam down but still
  serving its last cached image) are detected by content hash and discarded.
- **Capture platform:** GitHub Actions and a Raspberry Pi Zero W (via systemd timer) are both
  running now, in parallel, for the hand-off trial — GitHub Actions' schedule will be disabled
  once the trial concludes (see `docs/open-questions.md` #1).
- **Frame storage:** local disk on the Pi, synced to a cloud bucket (provider still open —
  AWS S3 vs. Backblaze B2 vs. Google Drive, see `docs/open-questions.md` #5).
- **Web interface:** a status/activity dashboard is planned — home-network-only, a
  statically-regenerated Python page (no app server) reusing the archive's own filenames for
  the activity graph, plus a new persisted capture log for health status (see
  `docs/open-questions.md` #9 and #10).

Still genuinely open — see [docs/open-questions.md](docs/open-questions.md): what the video
builder's output looks like (season video / daily clips / on-demand CLI), how outages should
appear in the rendered video, and which bucket provider to use for frame backup.
