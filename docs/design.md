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
```

- The archive is grouped `archive/<site>/<cam>/YYYY/MM/`. Each cam declares its
  `site` in the config (`config.yaml`'s cams are `bluewood`; `config.pi.yaml`'s are
  `seattle`), and `capture/main.py` writes to `archive_root / site / name`. Grouping by
  site keeps the two Bluewood cams together and separate from the Seattle
  pipeline-development cams — and lets a single config eventually capture both sites at
  once (the Pi hand-off, `docs/open-questions.md` #1) without them colliding in one flat
  namespace.
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
capturing all four cams. GitHub Actions keeps running in parallel for a ~1-2 week trial to
confirm the Pi is reliable, then its schedule is disabled (manual `workflow_dispatch` stays
available as an emergency fallback). Still to do before the trial ends: migrate the existing
git-committed frames onto the Pi's storage so the archive has one home going forward, and
stop tracking `archive/` in git.

The systemd units live under `deploy/pi/` (`timelapse-capture.service`,
`timelapse-capture.timer`, `timelapse-web.service`) with a bring-up doc
(`deploy/pi/README.md`), pointed at `capture/config.pi.yaml`. The capture service also
regenerates the status page after each run via an `ExecStartPost` (see Component 3).

## Component 2: the video builder

**Not implemented yet.** Design below is the plan, not built code. A CLI that turns a slice
of the archive into an mp4. Everything it does is a pure function
of the archive, so it can be re-run with different settings at any time.

```
timelapse build --cam summit --from 2026-01-05 --to 2026-02-20 --fps 30 -o jan-feb.mp4
```

Pipeline:

1. **Select** frames by cam and date range (filename glob).
2. **Filter** (each stage optional, controlled by flags):
   - drop night/dark frames by mean-brightness threshold;
   - drop any residual near-duplicates;
   - subsample (e.g. "one frame per day at noon" for a season-long video).
3. **Annotate** (optional): burn a date/time stamp onto each frame (ffmpeg `drawtext`),
   so skipped gaps are visible as jumps in the timestamp.
4. **Encode** with ffmpeg: concat the selected frames at the requested fps into H.264 mp4
   (libx264, `yuv420p` for universal playback).

Presets built on top of the same machinery:

- **Daily clip:** yesterday's daylight frames for one cam at ~24 fps. Can be automated to
  run each night.
- **Season video:** all daylight frames (or noon-only frames) from opening day to closing
  day.

### How outages appear in the output

Three options were discussed (decision pending — see open questions):

| Option | Effect | Cost |
| --- | --- | --- |
| Skip gaps silently | Video jumps seamlessly across outages | none — it's the default behavior of frame concat |
| Timestamp overlay + skip *(leaning toward this)* | Same seamless jump, but the burned-in clock makes outages visible | one `drawtext` filter |
| Placeholder "power out" cards | Outages become visible events in the video | builder must synthesize card frames from gap detection in the capture log |

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
off to the (not-yet-built) video builder.

```
python -m normalize.main path/to/drone-photos path/to/normalized --size 1920x1080
```

Pipeline, entirely local (OpenCV + numpy, no network calls, no AI model):

1. **Detect features** (ORB) in a reference frame (first photo, sorted by filename, unless
   `--reference` overrides it) and in each other photo.
2. **Match and estimate a similarity transform** (rotation + uniform scale + translation —
   deliberately not a full projective homography, since drone frames are slightly
   shifted/tilted/zoomed versions of roughly the same shot rather than different viewing
   angles; a homography would over-fit and risk keystone distortion). A frame with too few
   good matches (`--min-matches`, default 10 — a low-texture scene like open snow or sky)
   is skipped and reported rather than forced through a bad alignment.
3. **Warp** each photo into the reference's coordinate space, tracking which pixels are real
   image data vs. the black border the warp introduces.
4. **Crop to the common region**: intersect every frame's valid-pixel mask, then shrink an
   axis-aligned box border-by-border (whichever edge has the fewest valid pixels) until it's
   fully valid — a simple, deterministic way to guarantee no black edges without solving for
   the true largest inscribed rectangle.
5. **Resize** (optional, `--size`) to a final fixed output size.

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

## Deferred / follow-on ideas

- **Upload finished videos to Google Photos** automatically (the original impetus behind
  the `gphotos-uploader` repo name) — a better fit for finished videos than for raw frames,
  see storage section above.
- **Backfill** missed days from OnTheSnow's daily-image archive.
- Generalize beyond Bluewood: cams defined in a small config file (name, URL or stream,
  fetch method), so adding a third camera is a config change, not a code change.
- **Tailscale** for remote access to the web interface and Pi SSH, once wanted beyond the
  home network.
