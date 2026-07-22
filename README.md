# timelapse-creator

Tools for building timelapse videos from the [Ski Bluewood webcams](https://bluewood.com/webcams/)
(Dayton, WA) — and eventually any public webcam.

**Status: capture pipeline is live on two platforms.** A GitHub Actions cron job has been
fetching the two Bluewood cams every 15 minutes since 2026-07-16 and committing them to
`archive/` on `main`. A Raspberry Pi Zero W (hostname `timelapse-pi`) is now deployed and
capturing all four cams (the two Bluewood cams plus two Seattle dev cams) on a systemd
timer, with a home-network status page live at `http://timelapse-pi.local:8080/`. The two
capture paths run in parallel during the hand-off trial (see `docs/open-questions.md` #1).
The video builder (turning the archive into an mp4) is not built yet.

## The idea

Bluewood publishes two webcams (Summit and Base). This project will:

1. **Capture** frames from those cams on a regular schedule, all season long.
2. **Archive** every frame raw, with timestamped filenames.
3. **Build** timelapse videos from the archive on demand (daily clips, a season-long video,
   or arbitrary date ranges).

The archive is organized `archive/<site>/<cam>/YYYY/MM/` — cameras grouped by source
location (`bluewood/`, `seattle/`).

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
- `capture/config.pi.yaml` — the Pi's config: all four cams (two Seattle KING 5 cams, added
  to keep developing the pipeline while Bluewood was off-grid, plus the two Bluewood cams for
  the hand-off trial), plus a `capture_log` path
- `capture/fetch.py` — fetches an image (or grabs a frame from a stream via ffmpeg, unused so far — both cams are plain images)
- `capture/archive.py` — SHA-256 stale/duplicate detection, timestamped file writes
- `capture/main.py` — entrypoint: takes an optional `--config` (defaults to `capture/config.yaml`,
  preserving today's behavior), fetches each cam, skips failures/stale frames, saves new ones, and
  appends to a persisted capture log when the config provides a `capture_log` path
- `capture/capture_log.py` — appends one JSONL line per cam per run (timestamp, outcome, detail)
- `web/generate.py` — regenerates a single static status page (health/status table per cam,
  each cam name linked to its live image, plus per-cam and total disk usage, + a GitHub-style
  activity heatmap) from the archive filenames and the capture log; also symlinks the raw
  archive in next to the page so it's directly browsable
- `.github/workflows/capture.yml` — runs `capture/main.py` with no args every 15 minutes, commits new Bluewood frames to `archive/`
- `deploy/pi/` — systemd units (capture timer/service + web-server service) and a bring-up
  doc; **deployed and running** on the Pi (`timelapse-pi`), capturing all four cams and
  serving the status page

## Not implemented yet

- The video builder (archive → mp4)
- Long-term storage / cloud backup — Pi frames live on local disk and GitHub Actions frames
  in git; the `rclone` bucket sync isn't set up yet (see `docs/open-questions.md` #5)

## Quick summary of decisions so far

- **Language/tools:** Python for the capture job; **ffmpeg** planned for the not-yet-built video builder.
- **Cadence:** every 15 minutes (decided; see `docs/open-questions.md` #2).
- **Archive:** raw JPEGs named by cam and a fixed UTC-8 (Pacific, no DST) timestamp; never
  filtered at capture time.
- **Outages:** failed fetches are logged and skipped; *stale* frames (cam down but still
  serving its last cached image) are detected by content hash and discarded.
- **Capture platform:** a Raspberry Pi Zero W (`timelapse-pi`) now captures all four cams via
  a systemd timer; GitHub Actions still captures Bluewood in parallel during the hand-off
  trial, to be disabled once the Pi proves reliable (see `docs/open-questions.md` #1).
- **Frame storage:** local disk on the Pi, synced to a cloud bucket (provider still open —
  AWS S3 vs. Backblaze B2 vs. Google Drive, see `docs/open-questions.md` #5).
- **Web interface:** a status/activity dashboard is **built and deployed** (`web/generate.py`)
  — home-network-only, a statically-regenerated Python page (no app server) reusing the
  archive's own filenames for the activity graph, plus the persisted capture log for health
  status. Regenerated after each capture run and served under systemd on the Pi, live at
  `http://timelapse-pi.local:8080/` (see `docs/open-questions.md` #8).

Still genuinely open — see [docs/open-questions.md](docs/open-questions.md): what the video
builder's output looks like (season video / daily clips / on-demand CLI), how outages should
appear in the rendered video, and which bucket provider to use for frame backup.
