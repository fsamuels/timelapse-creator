# timelapse-creator

Tools for building timelapse videos from the [Ski Bluewood webcams](https://bluewood.com/webcams/)
(Dayton, WA) — and eventually any public webcam.

**Status: capture pipeline is live on the Pi.** A Raspberry Pi Zero W (hostname
`timelapse-pi`) is deployed and capturing four cams (the two Bluewood cams and two Seattle
dev cams) on a systemd timer, with a home-network status page live at
`http://timelapse-pi.local:8080/`. The two North Carolina cams are configured but
commented out in `capture/config.pi.yaml` as of 2026-07-25, pending the SD card migration
(`docs/sd-card-migration.md`) — re-enable them once the Pi is on the 64GB card. GitHub
Actions no longer captures on a schedule —
the earlier Bluewood-only cron job has been retired now that the Pi hand-off trial is
complete (see `docs/open-questions.md` #1); `workflow_dispatch` remains as a manual
emergency-capture fallback. The video builder (turning frames into an mp4) now has a first
pass built — see `video/` below.

## The idea

Bluewood publishes two webcams (Summit and Base). This project will:

1. **Capture** frames from those cams on a regular schedule, all season long.
2. **Archive** every frame raw, with timestamped filenames.
3. **Build** timelapse videos from the archive on demand (daily clips, a season-long video,
   or arbitrary date ranges).

The archive is organized `archive/<site>/<cam>/YYYY/MM/` — cameras grouped by source
location (`bluewood/`, `seattle/`, `north-carolina/`).

The defining constraint: Bluewood is **100% off-grid**. The webcams only work while the
resort's generator is running, so outages are the norm — every night, every closed day, the
whole off-season. The system must treat "cam is down" as ordinary operation, not an error.

## Documents

| Document | Contents |
| --- | --- |
| [docs/design.md](docs/design.md) | Architecture: the capture job, the video builder, drone-photo normalization (alignment methods, manual anchors), video build-time QA labels/color correction, storage layout, and outage/stale-frame handling |
| [docs/open-questions.md](docs/open-questions.md) | Decisions made so far and what's still open (output format, gap handling in video, long-term storage), with options and recommendations |
| [docs/sd-card-migration.md](docs/sd-card-migration.md) | Runbook for migrating the Pi's SD card from 4GB to 64GB (documented, not yet executed) |

## What's implemented

- `capture/config.yaml` — the two Bluewood cams, as direct CameraFTP JPEG URLs (used by the
  `workflow_dispatch` manual emergency-capture fallback in GitHub Actions; not on a schedule
  anymore)
- `capture/config.pi.yaml` — the Pi's config: two Seattle KING 5 cams, added to keep
  developing the pipeline while Bluewood was off-grid, and the two Bluewood cams for the
  hand-off trial, plus a `capture_log` path. Two North Carolina cams — WLOS-hosted PNG
  snapshots of the UNCA tower and the Nantahala Outdoor Center, Pi-only — are defined but
  commented out pending the SD card migration (`docs/sd-card-migration.md`)
- `capture/fetch.py` — fetches an image (or grabs a frame from a stream via ffmpeg, unused so far — both cams are plain images)
- `capture/archive.py` — SHA-256 stale/duplicate detection, timestamped file writes
- `capture/main.py` — entrypoint: takes an optional `--config` (defaults to `capture/config.yaml`,
  preserving today's behavior), fetches each cam, skips failures/stale frames, saves new ones, and
  appends to a persisted capture log when the config provides a `capture_log` path
- `capture/capture_log.py` — appends one JSONL line per cam per run (timestamp, outcome, detail)
- `web/generate.py` — regenerates a single static status page (health/status table per cam
  including average image size, each cam name linked to its live image, plus per-cam and
  total disk usage, a projected daily disk burn rate extrapolated from today's capture rate
  so far along with an estimated days-until-full at that rate, + a GitHub-style activity
  heatmap with tap-friendly tooltips (day counts show in a line below the grid, not just an
  unreachable-on-mobile hover title), + a per-cam thumbnail linking to the full-size frame,
  + a Dark/Light/System theme picker defaulting to dark) from the archive filenames and the
  capture log; also symlinks the raw archive in next to the page so it's directly browsable
- `.github/workflows/capture.yml` — manual-only (`workflow_dispatch`) now that the Pi is the
  sole scheduled capture platform; runs `capture/main.py` with no args as an emergency
  fallback
- `deploy/pi/` — systemd units (capture timer/service + web-server service) and a bring-up
  doc; **deployed and running** on the Pi (`timelapse-pi`), capturing all four cams and
  serving the status page
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
  are skipped silently, no timestamp overlay. Daily-clip/season-video presets and a
  subsampling stage are documented follow-ons, not built.
- `output/` — build artifacts: `normalize/`'s aligned-frame batches and `video/`'s rendered
  mp4s. Gitignored and regeneratable from `archive/`, not source.

## Not implemented yet

- Daily-clip / season-video presets and subsampling on top of the video builder — the
  on-demand CLI (`video/`) is built; these are follow-ons on the same machinery
- Long-term storage / cloud backup — Pi frames live on local disk and GitHub Actions frames
  in git; the `rclone` bucket sync isn't set up yet (see `docs/open-questions.md` #5)
- SD card migration (4GB → 64GB) — process is documented
  ([docs/sd-card-migration.md](docs/sd-card-migration.md)) but not yet executed (see
  `docs/open-questions.md` #11)

## Quick summary of decisions so far

- **Language/tools:** Python throughout, including the video builder (`video/`), which
  shells out to **ffmpeg** for the actual encode.
- **Cadence:** every 15 minutes (decided; see `docs/open-questions.md` #2).
- **Archive:** raw JPEGs named by cam and a fixed UTC-8 (Pacific, no DST) timestamp; never
  filtered at capture time.
- **Outages:** failed fetches are logged and skipped; *stale* frames (cam down but still
  serving its last cached image) are detected by content hash and discarded.
- **Capture platform:** a Raspberry Pi Zero W (`timelapse-pi`) captures all six cams via a
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
  (see `docs/open-questions.md` #3/#4).

Still genuinely open — see [docs/open-questions.md](docs/open-questions.md): daily-clip and
season-video presets on top of the video builder, and which bucket provider to use for frame
backup.
