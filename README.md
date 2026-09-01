# timelapse-creator

Tools for building timelapse videos from the [Ski Bluewood webcams](https://bluewood.com/webcams/)
(Dayton, WA) — and eventually any public webcam.

**Status: capture pipeline is live on the Pi.** A Raspberry Pi Zero W (hostname
`timelapse-pi`) is deployed and capturing ten cams (the two Bluewood cams, three Seattle
cams, two Washington-state cams, the UNCA tower cam, and two Hawaii cams — Mauna Kea
observatories captured from their YouTube live streams via `yt-dlp`+`ffmpeg`) on a systemd
timer, with a home-network status page live at `http://timelapse-pi.local:8080/`. The
Nantahala Outdoor Center cam is still configured but commented out in
`capture/config.pi.yaml`. The UNCA tower cam was re-enabled and the Washington-state cams
(Mount Rainier, Kalaloch Lodge) added 2026-07-30 now that the Pi is on the 64GB card
(`docs/sd-card-migration.md`) — these are speculative additions while there's card headroom
and may get trimmed later. GitHub Actions no longer captures on a schedule —
the earlier Bluewood-only cron job has been retired now that the Pi hand-off trial is
complete (see `docs/open-questions.md` #1); `workflow_dispatch` remains as a manual
emergency-capture fallback. The video builder (turning frames into an mp4) now has a first
pass built, plus daily-clip and season-video presets on top of it — see `video/` below.

## The idea

Bluewood publishes two webcams (Summit and Base). This project will:

1. **Capture** frames from those cams on a regular schedule, all season long.
2. **Archive** every frame raw, with timestamped filenames.
3. **Build** timelapse videos from the archive on demand (daily clips, a season-long video,
   or arbitrary date ranges).

The archive is organized `archive/<site>/<cam>/YYYY/MM/` — cameras grouped by source
location (`bluewood/`, `seattle/`, `washington/`, `north-carolina/`).

The defining constraint: Bluewood is **100% off-grid**. The webcams only work while the
resort's generator is running, so outages are the norm — every night, every closed day, the
whole off-season. The system must treat "cam is down" as ordinary operation, not an error.

## Documents

| Document | Contents |
| --- | --- |
| [docs/design.md](docs/design.md) | Architecture: the capture job, the video builder, drone-photo normalization (alignment methods, manual anchors), video build-time QA labels/color correction, storage layout, and outage/stale-frame handling |
| [docs/scripts.md](docs/scripts.md) | Every locally-runnable script, its full option list, and copy-pasteable examples — start here to run this repo's tools yourself, without going through an assistant |
| [docs/open-questions.md](docs/open-questions.md) | Decisions made so far and what's still open (output format, gap handling in video, long-term storage), with options and recommendations |
| [docs/sd-card-migration.md](docs/sd-card-migration.md) | Runbook for migrating the Pi's SD card from 4GB to 64GB (executed 2026-07-30) |

## What's implemented

- `capture/config.yaml` — the two Bluewood cams, as direct CameraFTP JPEG URLs (used by the
  `workflow_dispatch` manual emergency-capture fallback in GitHub Actions; not on a schedule
  anymore)
- `capture/config.pi.yaml` — the Pi's config: three Seattle cams (the original two KING 5 dev
  cams, added to keep developing the pipeline while Bluewood was off-grid, plus SeaTac, added
  2026-07-30) and the two Bluewood cams (added for the now-completed Pi hand-off trial), plus
  a `capture_log` path. Two North Carolina cams — WLOS-hosted PNG snapshots of the UNCA tower
  and the Nantahala Outdoor Center, Pi-only — were added on top of that; the UNCA tower cam
  was re-enabled 2026-07-30 now that the Pi is on the 64GB card
  (`docs/sd-card-migration.md`), while the Nantahala Outdoor Center cam stays commented out.
  Two Washington-state cams — Mount Rainier (moved out of `seattle`, since it's not
  Seattle-specific) and Kalaloch Lodge, both under a new `washington` site — were also added
  2026-07-30 while there's card headroom; may get trimmed later. Two Hawaii cams — CFHT and
  Subaru Telescope, both Mauna Kea observatories — were added under a new `hawaii` site,
  captured via `type: youtube` (`yt-dlp` resolves the YouTube live stream to a direct URL,
  then `ffmpeg` grabs one frame)
- `capture/fetch.py` — fetches a plain image, or grabs a frame from a video stream via
  ffmpeg (`type: stream`, or `type: youtube` which resolves a YouTube watch/channel URL to a
  direct stream URL first via `yt-dlp`)
- `capture/archive.py` — SHA-256 stale/duplicate detection, timestamped file writes
- `capture/main.py` — entrypoint: takes an optional `--config` (defaults to `capture/config.yaml`,
  preserving today's behavior), fetches each cam, skips failures/stale frames, saves new ones, and
  appends to a persisted capture log when the config provides a `capture_log` path
- `capture/capture_log.py` — appends one JSONL line per cam per run (timestamp, outcome, detail)
- `capture/sync_archive.py` — mirrors one cam's frames from the Pi's `/archive/` directory
  listing (see `web/generate.py` below) down into a local `archive/<site>/<cam>/`, so the
  video builder can run against a laptop-local copy instead of the Pi. Skips frames already
  present locally, so re-running it only pulls what's new: `python -m capture.sync_archive
  <site> <cam>`
- `web/generate.py` — regenerates a single static status page (mobile-friendly card layout,
  dark-only, grouped by site in the config's `site_order`): disk-free/runway stat tiles up
  top (runway estimates days of free space left at today's projected burn rate), a per-cam
  card with its live thumbnail linked to the full-size frame, health status pill, last-frame
  time with its configured capture cadence alongside it (e.g. "5m ago · every 15 min"), avg
  frame size/frame count/disk usage, and a 31-day recent-activity strip with tap-friendly
  tooltips
  (day counts show in a line below the strip, not just an unreachable-on-mobile hover title),
  plus a "full history" link opening a GitHub-style multi-month contribution grid per cam as
  a bottom-sheet — implemented with CSS `:target`, no `<script>` tag — from the archive
  filenames and the capture log; clicking a day with frames in that grid drills down further
  into an hourly breakdown for just that day. Also symlinks the raw archive in next to the
  page so it's directly browsable, and a footer shows host stats (uptime, longest recorded
  uptime streak with its start/end dates, memory, load average), how long the page took to
  generate, and a deployment marker (commit sha8 + date) so it's clear which commit is
  actually live. A camera/clock favicon and matching header
  logo (inline SVG, no separate asset file) round it out
- `.github/workflows/capture.yml` — manual-only (`workflow_dispatch`) now that the Pi is the
  sole scheduled capture platform; runs `capture/main.py` with no args as an emergency
  fallback
- `deploy/pi/` — systemd units (capture timer/service + web-server service) and a bring-up
  doc; **deployed and running** on the Pi (`timelapse-pi`), capturing all ten active cams
  and serving the status page
- `normalize/` — aligns a directory of not-quite-fixed-position photos (e.g. drone shots) onto
  a common frame so they cut into a smooth timelapse; a separate, on-demand batch input path
  from the scheduled webcam capture above. Photos are processed in EXIF capture-time order,
  and any photo that doesn't match well enough (`--min-confidence`/`--min-matches`), or is
  manually rejected (see `annotate.py` below), is skipped and reported, so unrelated shots
  mixed into the input directory don't need to be sorted out by hand. Three ways to align a
  photo, in increasing order of manual effort and precision — pick the cheapest one that
  actually works for the content at hand (see `docs/design.md` Component 4 for a full
  comparison and what happened trying each on this project's actual archive):
  - `--method ecc` (default) — directly optimizes against pixel intensity across the whole
    image; the better fit for low-texture scenes like grass/dirt fields, survives large
    seasonal appearance change (bare ground ↔ snow).
  - `--method orb` — sparse feature matching; faster, needs more distinctive texture to work
    from (buildings, rock, urban scenes).
  - `--control-points path/to/control_points.json` — manually-clicked point correspondences
    on specific "anchor" photos (`normalize/annotate.py`, a local browser tool — see below),
    for photos automated matching can't precisely handle. A batch spanning several distinct
    zoom/altitude groupings is the common case: even a high-confidence automated match to one
    global reference can still be locally imprecise, and no amount of threshold-tuning fixes
    that the way a few manually-verified anchors do.

  Each warped frame's black border is handled by `--crop`: `none` (default) keeps every frame
  at the reference's full size with real black wherever that frame's own alignment didn't
  reach; `intersection` crops every frame down to the one rectangle valid across *all* aligned
  frames, guaranteeing no black edges but shrinking fast as more frames are aligned. Runs
  entirely locally (OpenCV, no network calls, no AI model): `python -m normalize.main
  <input-dir> <output-dir> [--method ecc|orb] [--min-confidence N | --min-matches N]
  [--control-points path] [--crop none|intersection] [--size WxH]`. Also writes a
  `manifest.json` (filename → EXIF capture timestamp) alongside the aligned frames, since the
  alignment/crop step strips EXIF — the video builder below reads it to time drone-photo
  clips proportionally.
- `normalize/annotate.py` — local browser tool (stdlib `http.server`, no new dependency;
  meant to be opened in your own browser, not driven by Claude) for clicking the manual
  control points `--control-points` uses: reference photo and one other photo side by side,
  click the same physical point on both (4+ pairs turns a photo into an anchor), or Reject a
  photo that doesn't belong at all. Every photo in the input directory is browsable by
  default (Prev/Next, jump-to dropdown, "only unreviewed" filter), not just a suggested few,
  so a full triage pass of a whole archive is practical. Saves straight to disk as you click
  — stop and resume anytime. `python -m normalize.annotate <input-dir> <control-points-path>
  --reference <path>`.
- `control_points/` — one JSON file per sequence, holding `annotate.py`'s manually-clicked
  points and rejections. Committed to the repo (unlike `output/`) since it's real,
  non-regeneratable manual work — re-running normalization later, as more photos are
  captured, doesn't require re-clicking anything already annotated. Contains only pixel
  coordinates and filenames, no image data, so it stays safe to check in even though the
  photos themselves are not.
- `video/` — the video builder: turns a directory of frames (a webcam archive cam directory,
  or a `normalize/` output directory) into an mp4, through the same code path either way —
  it reads timestamps from a `manifest.json` if present, otherwise parses them from the
  archive's own filenames. Two timing modes: uniform fps (`--fps`, the default — right for
  the webcams' fixed 15-minute cadence) or proportional (`--proportional --duration N` —
  each frame held for a time proportional to the real gap before the next one, capped by
  `--min-hold`/`--max-hold` so no single gap dominates; right for irregularly-spaced batches
  like drone photos, where some weeks have several flights and others have one). Optional
  `--from`/`--to` date filtering, `--drop-dark` (mean-brightness threshold) and `--dedupe`
  (drop residual exact-duplicate frames) filters. Two build-time-only, opt-in enhancements
  (see `docs/design.md` Component 5):
  - `--label-date`/`--label-filename` — burns the capture date and/or source filename into
    the bottom-left corner of each frame, for QA while dialing in alignment/color settings;
    not meant to stay in a final render.
  - `--white-balance` (+ `--white-balance-patch top,left,bottom,right` +
    `--white-balance-target path`) — corrects per-frame color cast (e.g. a warm/red evening
    shot) by sampling a fixed patch on some neutral-ish surface (visible at a consistent
    location only because the frames are already aligned) and scaling per-channel to match
    that patch's color in a chosen target frame. Safer than a whole-image histogram match on
    a batch with widely varying content (e.g. a snow frame next to lush-green summer ones),
    since it corrects the *lighting* without touching color relationships elsewhere in the
    frame.

  Encodes via ffmpeg's concat demuxer to H.264/`yuv420p` mp4: `python -m video.main
  <input-dir> -o output/out.mp4 [--fps N | --proportional --duration N] [--from YYYY-MM-DD]
  [--to YYYY-MM-DD] [--drop-dark] [--dedupe] [--label-date] [--label-filename]
  [--white-balance --white-balance-patch T,L,B,R --white-balance-target path]`. Outage gaps
  are skipped silently, no timestamp overlay. Two presets are built on top of the same
  `frames.py`/`encode.py` machinery: `video/daily_clip.py` (one day's clip from a cam
  directory, defaulting to yesterday (Pacific) with dark/night frames dropped, so it's
  runnable unattended: `python -m video.daily_clip archive/bluewood/summit -o
  daily/bluewood/summit [--date YYYY-MM-DD]`) and `video/season_video.py` (subsamples a cam
  directory to one frame/day, closest to `--at-hour` (default noon), then encodes the whole
  range as one video: `python -m video.season_video archive/bluewood/summit -o season.mp4
  [--fps N | --proportional --duration N]`).
- `output/` — build artifacts: `normalize/`'s aligned-frame batches and `video/`'s rendered
  mp4s. Gitignored and regeneratable from `archive/`, not source.

## Not implemented yet

- Long-term storage / cloud backup — Pi frames live on local disk only; the `rclone` bucket
  sync isn't set up yet (see `docs/open-questions.md` #5). The pre-Pi Bluewood frames GitHub
  Actions committed during the hand-off trial remain in git history, not the live archive.

## Quick summary of decisions so far

- **Language/tools:** Python throughout, including the video builder (`video/`), which
  shells out to **ffmpeg** for the actual encode.
- **Cadence:** every 15 minutes (decided; see `docs/open-questions.md` #2).
- **Archive:** raw JPEGs named by cam and a fixed UTC-8 (Pacific, no DST) timestamp; never
  filtered at capture time.
- **Outages:** failed fetches are logged and skipped; *stale* frames (cam down but still
  serving its last cached image) are detected by content hash and discarded.
- **Capture platform:** a Raspberry Pi Zero W (`timelapse-pi`) captures all ten active cams via a
  systemd timer — the sole scheduled capture platform now that the hand-off trial is
  complete (see `docs/open-questions.md` #1). GitHub Actions' schedule is disabled;
  `workflow_dispatch` remains as a manual emergency-capture fallback.
- **Frame storage:** local disk on the Pi, synced to a cloud bucket (provider still open —
  AWS S3 vs. Backblaze B2 vs. Google Drive, see `docs/open-questions.md` #5).
- **Web interface:** a status/activity dashboard is **built and deployed** (`web/generate.py`)
  — home-network-only, a statically-regenerated Python page (no app server) reusing the
  archive's own filenames for the activity graph, plus the persisted capture log for health
  status. Regenerated after each capture run and served under systemd on the Pi, live at
  `http://timelapse-pi.local:8080/` (see `docs/open-questions.md` #8).
- **Video builder:** a first pass is **built** (`video/`) — an on-demand CLI over either a
  webcam archive directory or a `normalize/` output directory, with uniform-fps and
  proportional (time-accurate) duration modes, optional dark-frame/dedupe filters, and an
  ffmpeg concat-demuxer H.264 encode. Outage gaps are skipped silently, no timestamp overlay
  (see `docs/open-questions.md` #3/#4). Daily-clip and season-video presets are **built** on
  top of the same machinery (`video/daily_clip.py`, `video/season_video.py`).

Still genuinely open — see [docs/open-questions.md](docs/open-questions.md): which bucket
provider to use for frame backup.
