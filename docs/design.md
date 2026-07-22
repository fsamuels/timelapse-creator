# Design

## Context

- Source: [Ski Bluewood snowcams](https://bluewood.com/webcams/) — two cameras, **Summit**
  and **Base**, near Dayton, WA.
- Bluewood is **100% off-grid**: the webcams are powered by the resort's generator and go
  dark whenever it isn't running — nights, closed days, and the entire off-season. Downtime
  is the normal state, not a failure mode.
- [OnTheSnow archives daily images from Bluewood](https://www.onthesnow.com/washington/bluewood/webcams),
  which could serve as a backfill source for days the capture job misses.
- **How the cams are served (resolved):** both are hosted on **CameraFTP** (DriveHQ) via a
  "last image" REST endpoint — a plain JPEG, no scraping or stream-grabbing needed:
  - Summit: `https://cameraftpapi.drivehq.com/api/Camera/LastImageaspx/shareID17403860/bwdsummit.jpg?`
  - Base: `https://cameraftpapi.drivehq.com/api/Camera/LastImageaspx/shareID17403629/bwdbase.jpg?`

  `capture/fetch.py` still supports the "grab one frame from a video stream via ffmpeg" case
  (`fetch_stream_frame`) for a future cam that isn't a plain image, but neither current cam
  needs it.
- **Bonus find:** the cams are live in July (off-season) because the resort is doing
  maintenance and replacing the old 3-person lift with a high-speed quad — a second,
  time-sensitive timelapse subject alongside the season-long snow one.
- **Cams are now generalized beyond Bluewood.** `capture/main.py` takes a `--config` flag
  (default: `capture/config.yaml`, unchanged), and a second config,
  `capture/config.pi.yaml`, which the Pi runs. It started with two Seattle (KING 5) cams —
  added to keep developing the pipeline while Bluewood was dark — and now also includes the
  two Bluewood cams (`summit`, `base`), so the Pi captures all four. GitHub Actions keeps
  capturing Bluewood in parallel via `config.yaml` during the hand-off trial
  (`docs/open-questions.md` #1).
- **North Carolina cams added (Pi-only).** Two WLOS-hosted PNG snapshot cams — the UNCA
  tower cam and the Nantahala Outdoor Center cam — were added to `capture/config.pi.yaml`
  under a new `north-carolina` site, bringing the Pi to six cams total. No config.yaml /
  GitHub Actions equivalent; these are Pi-only from the start, unrelated to the Bluewood
  hand-off trial. Fetched the same way as every other `type: image` cam (`fetch_image`) —
  the fact that these happen to be PNGs rather than JPEGs doesn't matter to the fetch or
  stale-detection path, since both just compare raw bytes.

## Architecture: two decoupled pieces

```
┌─────────────┐   every N minutes   ┌──────────────┐   on demand    ┌─────────────┐
│ Bluewood    │ ──────────────────► │ Frame archive │ ─────────────► │ Timelapse   │
│ webcams     │    capture job      │ (raw JPEGs)   │  video builder │ videos (mp4)│
└─────────────┘                     └──────────────┘                └─────────────┘
```

The capture job and the video builder share nothing but the archive layout. This is the
core design principle: **archive everything raw, filter at build time.** Night frames,
near-duplicates, and ugly weather all get stored; deciding what belongs in a video is the
builder's job. A bad filtering idea can be re-run; a frame never captured is gone forever.

## Component 1: the capture job

**Implemented** in `capture/` (`fetch.py`, `archive.py`, `main.py`, `config.yaml`), running
as a GitHub Actions cron job (`.github/workflows/capture.yml`) every 15 minutes since
2026-07-16. Each run:

1. For each cam (summit, base): fetch one frame.
2. On **fetch failure** (timeout, HTTP error, DNS): log it and move on. No retries beyond
   one quick attempt, no alerting by default — the cam being down is expected.
3. On success, run **stale-frame detection** (see below). If the frame is new, write it to
   the archive; if stale, discard it.

### Stale-frame detection — the real outage problem

The subtle failure mode is not HTTP errors. A downed webcam (or its CDN/cache) often keeps
serving the **last image it captured**, indefinitely. Naively archiving every successful
download would fill the archive with hundreds of identical copies of the moment before the
generator shut down — and the timelapse would freeze on that frame.

Defense in layers:

1. **Content hash:** compute a SHA-256 of the downloaded bytes; if it equals the hash of the
   previous archived frame for that cam, discard. Exact-duplicate detection is cheap and
   catches the common cached-image case.
2. **`Last-Modified` / `ETag` headers:** if the server provides them, send
   `If-Modified-Since` / `If-None-Match` and treat a `304` as "no new frame" without even
   downloading the body.
3. *(Optional, later)* **Perceptual near-duplicate detection** if the cam re-encodes the same
   stale image with different bytes (rare, but happens with some webcam software). Only add
   this if the archive shows it's needed.

### Archive layout

```
archive/
  bluewood/          # one directory per site (source location)
    summit/
      2026/07/       # one directory per month keeps directory sizes sane
        2026-07-16T13-10-04-544533-0800.jpg
        2026-07-16T13-25-01-118203-0800.jpg
    base/
      2026/07/
        ...
  seattle/
    columbia/
      2026/07/
        ...
    queenanne/
      2026/07/
        ...
  north-carolina/
    unca-tower/
      2026/07/
        ...
    nantahala-outdoor-center/
      2026/07/
        ...
```

- The archive is grouped `archive/<site>/<cam>/YYYY/MM/`. Each cam declares its
  `site` in the config (`config.yaml`'s cams are `bluewood`; `config.pi.yaml`'s are
  `seattle` and `north-carolina`), and `capture/main.py` writes to `archive_root / site /
  name`. Grouping by site keeps the two Bluewood cams together and separate from the
  Seattle pipeline-development cams and the North Carolina cams — and lets a single config
  capture multiple sites at once (the Pi hand-off, `docs/open-questions.md` #1) without them
  colliding in one flat namespace.
- Filenames are timestamps with microsecond precision (avoids collisions if two frames for
  the same cam are ever saved within the same second) at a **fixed UTC-8 offset** — not
  IANA `America/Los_Angeles` — so they read close to Pacific local time without adopting
  daylight saving time. A real PST/PDT clock repeats an hour of wall-clock time every
  November, which would make the lexical-sort-is-chronological-sort property (the builder
  needs no database, just a glob) silently false for one hour a year. The fixed offset means
  timestamps run an hour behind true local time during PDT (summer) but stay strictly
  monotonic year-round. The offset is embedded in the filename (`-0800`) so it's
  unambiguous and self-describing.
- **Implemented:** a persistent `capture.log` (`capture/capture_log.py`), one JSONL line per
  cam per run (`ts`, `cam`, `outcome`, `detail`). Kept in its own module rather than folded
  into `archive.py`, to keep that file's hash/stale-detection logic isolated (see CLAUDE.md's
  testing rule). `capture/main.py` writes to it only when the loaded config has a
  `capture_log` path key — `capture/config.yaml` (GitHub Actions) has none, so that path is
  unchanged; `capture/config.pi.yaml` sets one, since the web interface's health/status view
  and outage history need to outlive the GitHub Actions run log's ~90-day window.

### Handing capture off to the Pi

**Deployed and running** (see `docs/open-questions.md` #1): a systemd timer runs the same
`capture/` code every 15 minutes on the Pi (hostname `timelapse-pi`), writing to local disk
at `/var/lib/timelapse/archive` instead of committing to git (see storage below), and
capturing all six cams. GitHub Actions keeps running in parallel for a ~1-2 week trial to
confirm the Pi is reliable, then its schedule is disabled (manual `workflow_dispatch` stays
available as an emergency fallback). Still to do before the trial ends: migrate the existing
git-committed frames onto the Pi's storage so the archive has one home going forward, and
stop tracking `archive/` in git.

The systemd units live under `deploy/pi/` (`timelapse-capture.service`,
`timelapse-capture.timer`, `timelapse-web.service`) with a bring-up doc
(`deploy/pi/README.md`), pointed at `capture/config.pi.yaml`. The capture service also
regenerates the status page after each run via an `ExecStartPost` (see Component 3).

## Component 2: the video builder

**Implemented** (first pass) in `video/` (`frames.py`, `encode.py`, `main.py`). A CLI that
turns a directory of frames into an mp4. Everything it does is a pure function of its
inputs, so it can be re-run with different settings at any time — nothing here mutates the
archive or normalize output.

```
python -m video.main archive/bluewood/summit -o summit.mp4 --fps 24 --from 2026-01-05 --to 2026-02-20
python -m video.main normalized/drone-shots -o drone.mp4 --proportional --duration 30
```

### Two frame sources, one pipeline

The builder is deliberately source-agnostic: `video/frames.py`'s `load_frames` accepts any
directory and decides how to recover each frame's real capture timestamp by what it finds:

- If the directory has a `manifest.json` (written by `normalize/align.py`, see Component 4
  below), timestamps come from there.
- Otherwise, timestamps are parsed from the archive's own filenames via
  `capture.archive.parse_frame_time` — this is what makes an `archive/<site>/<cam>/`
  directory usable directly, no separate export step.

Everything downstream (date-range filtering, dark-frame/duplicate dropping, duration
computation) works on the same `[(Path, datetime), ...]` list either way.

Pipeline:

1. **Select**: `load_frames` (by source, above), then `filter_date_range` (`--from`/`--to`,
   both bounds inclusive).
2. **Filter** (each stage optional, off by default):
   - `--drop-dark` / `--dark-threshold`: drop frames below a mean-brightness threshold
     (PIL grayscale mean) — night frames on an otherwise-lit webcam.
   - `--dedupe`: drop frames whose bytes exactly match the immediately preceding *kept*
     frame — residual near-duplicates that slipped past capture-time stale detection (e.g.
     frames pulled in from more than one source). Exact-hash only, matching
     `capture/archive.py`'s existing stale check; perceptual near-duplicate detection is
     still deferred (see Component 1) — add it only if the archive shows it's needed.
   - Subsampling (e.g. "one frame per day at noon" for a season-long video) is **not
     implemented** — a natural addition to `frames.py` when a season-long preset is built,
     not needed for the on-demand case this first pass targets.
3. **Time** each frame, one of two modes:
   - **Uniform** (`--fps`, default 24): every frame gets equal screen time, `1/fps` seconds.
     The right mode for the webcams' fixed 15-minute cadence.
   - **Proportional** (`--proportional --duration N`): each frame is held for a time
     proportional to the real time-gap before the next frame, scaled so the (pre-clamp)
     total matches the requested `--duration`, then clamped to `[--min-hold, --max-hold]`
     per frame (defaults 0.05s/2.0s). This is for irregularly-spaced batches — a week with
     4 drone photos reads as 4x more coverage than a week with 1, without one outlier gap
     (an overnight webcam outage, two weeks between drone flights) swallowing the whole
     video. Clamping means the actual rendered length is a target, not a guarantee — `video/
     main.py` logs a warning when clamping pushes the total more than half a second off
     `--duration`.
4. **Encode**: `video/encode.py` builds an ffmpeg **concat demuxer** script — one `file` /
   `duration` pair per frame — rather than a fixed `-r fps`, since that's what makes
   per-frame variable durations possible in both modes through the same code path. (ffmpeg's
   concat demuxer has a documented quirk where the *last* `duration` directive is ignored;
   the workaround, applied here, is to repeat the last frame's `file` line once more with no
   trailing duration.) Output is H.264 (`libx264`, `yuv420p`) mp4, matching the
   universal-playback decision below — unchanged from the original design.

**Not yet built** (documented as follow-on, not this pass):
- A title/date-range card at the start (`drawtext`) — cheap to add, deferred for scope.
- Any resolution/downscale controls — output resolution is whatever the input frames
  already are (webcam frames are fixed-size per cam; drone batches are already resized via
  `normalize`'s `--size`).

### Daily-clip and season-video presets (implemented)

Thin wrappers over the same `frames.py`/`encode.py` machinery above, for the two use cases
`video/main.py`'s on-demand CLI doesn't make convenient on its own: an unattended nightly job,
and a full-season video short enough to actually watch.

- **`video/daily_clip.py`** — one day's clip from a single cam directory:
  `python -m video.daily_clip archive/bluewood/summit -o daily/bluewood/summit [--date
  YYYY-MM-DD]`. `--date` defaults to yesterday (Pacific, matching `capture/archive.py`'s own
  timestamp convention) so it's runnable unattended every night with no arguments. Drops dark
  (night) frames by default (`--no-drop-dark` to keep them) — a "sunrise-to-sunset" clip —
  then encodes at a fixed `--fps` (default 24). Output is named `<date>.mp4` in the given
  output directory.
- **`video/season_video.py`** — a single video spanning a cam's whole date range (or
  `--from`/`--to` slice), subsampled to one frame per calendar day via the new
  `frames.subsample_daily` (picks whichever frame is closest to `--at-hour`, default noon;
  ties go to the earlier frame) so a multi-month archive collapses to a watchable length
  instead of hours of near-identical frames. Same two timing modes as `video/main.py`: fixed
  `--fps` (default 8, since the frame count is already ~1/day) or `--proportional
  --duration N` (a multi-day capture outage reads as a pause in the video rather than being
  invisible, since the subsampled frames' real day-to-day gaps still vary when a day is
  missing).

Both reuse `frames.load_frames`/`filter_date_range`/`uniform_durations`/
`proportional_durations` and a new `encode.encode_frames` helper (the
build-script/tempdir/run_ffmpeg wiring `video/main.py` also uses) rather than duplicating any
of Component 2's core pipeline.

### How outages appear in the output (decided)

**Decided: skip gaps silently, no overlay.** Frames jump seamlessly across a gap with no
burned-in timestamp — a deliberate reversal of this doc's earlier lean toward a
`drawtext` overlay; the overlay idea was set aside as unwanted polish, not because of any
technical problem with it. A placeholder "power out" card option (synthesizing frames from
capture-log gaps) remains a documented possibility, not built.

## Component 3: the web interface

**Implemented** as `web/generate.py` (see `docs/open-questions.md` #9). Two goals: confirm
the capture pipeline is still working, and show a GitHub-style activity graph of images
downloaded per day.

- **Runs on the Pi**, home network only. `web/generate.py` regenerates a single
  self-contained static HTML page (inline CSS, no external assets) — served by
  `python -m http.server` under `deploy/pi/timelapse-web.service`, no persistent app
  server. It reuses `capture/archive.py`'s `parse_frame_time` (the inverse of
  `save_frame`'s naming) for the timestamps. This matches the data's own cadence (it only
  changes every 15 minutes) and the project's batch-job shape rather than adding an
  always-on service to a single-core, 512MB Pi Zero W.
- **Theme:** a Dark/Light/System dropdown, defaulting to dark on every load (an inline
  `onchange` attribute flips a `data-theme` attribute on `<html>`, no `<script>` tag,
  matching the "no external assets" self-contained requirement enforced by
  `tests/test_generate.py`). System still tracks `prefers-color-scheme` if picked. No
  persistence — deliberately, since the page already reloads from scratch every 15 minutes.
- **Regeneration:** the capture service runs it as an `ExecStartPost` after each capture,
  so the page refreshes every run. Because the generator reads `archive_dir` from the
  config, it shows exactly the frames in that directory — on the Pi, only Pi-captured
  frames (the "Pi-era only" activity scope), with no source-era filtering logic of its own.
- **Activity heatmap:** derived directly from archive filenames — no new data source needed.
  One contribution-style grid per cam, grouped under its site. Each cell's tooltip reads
  "N images on YYYY-MM-DD" (count leads, date follows).
- **Thumbnail:** the per-cam block shows the newest frame to the right of its heatmap — an
  `<img>` reading straight from the `archive/` symlink, no copy step. Chosen over the status
  table (already the densest part of the page); placing it beside the heatmap grid is safe
  because that grid is fixed at 13 weeks regardless of archive size, so it never actually
  grows.
- **Health/status view:** last frame per cam, how long ago, a staleness flag (`--stale-hours`,
  default 1), the last-run outcome, and per-cam + total disk usage (`shutil.disk_usage` on
  `archive_dir`). The outcome needs the persisted `capture.log` from Component 1 — status
  can't be derived from successful frames alone, since a stuck/failing cam produces *no*
  new archive entries.
- **Browsing the archive:** each cam name links to its live image, and the generator
  symlinks `www/archive` to `archive_dir` on every run so the full frame archive is
  reachable as a plain directory listing at `/archive/` — no copying, and no new serving
  code (`http.server` follows the symlink). Same home-network/no-auth trust model as the
  rest of the page; see `docs/open-questions.md` #8. A proper gallery view (paginated by
  day/cam with thumbnails, generated the same way as the heatmap) is a natural next step on
  top of this rather than a new access decision.
- **Remote access:** not built now; Tailscale is the documented future option, and would also
  cover remote SSH to the Pi for maintenance, not just this page.

## Component 4: drone photo normalization

**Implemented** in `normalize/` (`align.py`, `main.py`). A separate build-time input path
from the fixed webcams: drone photos aren't captured on a schedule by this project, they're
an existing batch of images with slightly varying position, angle, and altitude between
shots, which would make a naive frame-concat timelapse look shaky. Normalization aligns and
crops a directory of them onto a common frame so they cut together smoothly, before handing
off to the video builder (Component 2).

```
python -m normalize.main path/to/drone-photos path/to/normalized --size 1920x1080
```

Pipeline, entirely local (OpenCV + Pillow + numpy, no network calls, no AI model):

1. **Order** photos by EXIF capture time (`DateTimeOriginal`, falling back to the `DateTime`
   tag, falling back to file mtime if a photo has no EXIF data at all) — not filename, since
   drone photo filenames aren't necessarily chronological.
2. **Detect features** (ORB) in a reference frame (first photo in that capture-time order,
   unless `--reference` overrides it) and in each other photo.
3. **Match and estimate a similarity transform** (rotation + uniform scale + translation —
   deliberately not a full projective homography, since drone frames are slightly
   shifted/tilted/zoomed versions of roughly the same shot rather than different viewing
   angles; a homography would over-fit and risk keystone distortion), then check how many of
   those matches actually agree with one consistent transform (RANSAC inliers). A photo whose
   inlier count is below `--min-matches` (default 10) — a low-texture scene like open snow or
   sky, or simply an unrelated photo that doesn't belong in this sequence — is skipped and
   reported rather than forced through a bad alignment. This is the tolerance knob: raise
   `--min-matches` to more strictly exclude photos that don't clearly match the reference
   (useful when pointing it at a directory with unrelated shots mixed in, so they don't have
   to be sorted out by hand first), lower it to be more lenient.
4. **Warp** each photo into the reference's coordinate space, tracking which pixels are real
   image data vs. the black border the warp introduces.
5. **Crop to the common region**: intersect every frame's valid-pixel mask, then shrink an
   axis-aligned box border-by-border (whichever edge has the fewest valid pixels) until it's
   fully valid — a simple, deterministic way to guarantee no black edges without solving for
   the true largest inscribed rectangle.
6. **Resize** (optional, `--size`) to a final fixed output size.
7. **Write a manifest**: `manifest.json` in the output directory, mapping each aligned
   frame's filename to its EXIF capture timestamp (ISO 8601). This exists because step 4's
   `cv2.imwrite` silently strips EXIF from the warped/cropped output — without the manifest,
   every timestamp recovered in step 1 would be lost the moment the frame is written back
   out. The video builder (Component 2) reads this manifest to recover each frame's real
   capture time, which its proportional-duration timing mode depends on. Frames
   `normalize_sequence` skipped (step 2/3) are correctly absent from the manifest.

This is intentionally a standalone preprocessing step rather than folded into
`capture/archive.py` — it's a different pipeline shape (batch import vs. scheduled capture)
and matches the project's "archive raw, filter/normalize at build time" principle: the
normalization choices here (similarity vs. homography, the crop heuristic, match threshold)
are exactly the kind of decision that should be re-runnable, not baked into capture.

## Storage: frames and bucket sync

**Decided** (see `docs/open-questions.md` #5): frames are written to local disk on the Pi at
capture time, then synced periodically to a cloud bucket via `rclone` as an off-device
backup. Bucket provider (AWS S3 vs. Backblaze B2 vs. Google Drive) is still open — `rclone`
backs all three with the same sync command, so the choice doesn't change this mechanism.
Google Photos was considered and set aside for *raw frame* storage specifically — its
album/browsing model and 2025 API restrictions to app-created content are a poor fit for the
exact-byte round-tripping that stale-frame hash detection depends on — but remains a good fit
for finished videos (see deferred ideas below), which are naturally photo-library-shaped.

**SD card capacity (documented, not yet executed):** the Pi currently boots from a 4GB card.
With six cams now capturing (up from four), that card's runway is shorter than originally
planned — see `docs/open-questions.md` #11 for the growth estimate. The migration process
to a 64GB card is written up as a runbook in **`docs/sd-card-migration.md`**: a fresh OS
install on the new card, `rsync` the existing archive over, verify, then physically swap
cards — preferred over a full-disk clone since it also re-validates `deploy/pi/README.md`'s
bring-up steps and avoids resizing a cloned partition table. Not yet executed.

## Deferred / follow-on ideas

- **Upload finished videos to Google Photos** automatically (the original impetus behind
  the `gphotos-uploader` repo name) — a better fit for finished videos than for raw frames,
  see storage section above.
- **Backfill** missed days from OnTheSnow's daily-image archive.
- Generalize beyond Bluewood: cams defined in a small config file (name, URL or stream,
  fetch method), so adding a third camera is a config change, not a code change.
- **Tailscale** for remote access to the web interface and Pi SSH, once wanted beyond the
  home network.
