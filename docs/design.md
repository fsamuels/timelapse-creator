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

  `capture/fetch.py` also supports the "grab one frame from a video stream via ffmpeg" case
  (`fetch_stream_frame`, `type: stream`) for a cam that isn't a plain image — neither Bluewood
  cam needs it, but the Hawaii cams below do, via `type: youtube` (see below).
- **Bonus find:** the cams are live in July (off-season) because the resort is doing
  maintenance and replacing the old 3-person lift with a high-speed quad — a second,
  time-sensitive timelapse subject alongside the season-long snow one.
- **Cams are now generalized beyond Bluewood.** `capture/main.py` takes a `--config` flag
  (default: `capture/config.yaml`, unchanged), and a second config,
  `capture/config.pi.yaml`, which the Pi runs. It started with two Seattle (KING 5) cams —
  added to keep developing the pipeline while Bluewood was dark — and now also includes the
  two Bluewood cams (`summit`, `base`), so the Pi captures all four. GitHub Actions captured
  Bluewood in parallel via `config.yaml` during the hand-off trial (`docs/open-questions.md`
  #1); that trial is complete, and `config.yaml` now only runs via the manual
  `workflow_dispatch` emergency fallback.
- **North Carolina cams added (Pi-only).** Two WLOS-hosted PNG snapshot cams — the UNCA
  tower cam and the Nantahala Outdoor Center cam — were added to `capture/config.pi.yaml`
  under a new `north-carolina` site, bringing the Pi to six cams total. No config.yaml /
  GitHub Actions equivalent; these are Pi-only from the start, unrelated to the Bluewood
  hand-off trial. Fetched the same way as every other `type: image` cam (`fetch_image`) —
  the fact that these happen to be PNGs rather than JPEGs doesn't matter to the fetch or
  stale-detection path, since both just compare raw bytes.
- **Per-cam capture interval.** The two North Carolina cams grew fast enough (heaviest
  average frame size of all six cams) to threaten the Pi's SD card, so each cam now declares
  its own required `interval_minutes` in config (no code-side default — every cam must set
  it explicitly). `capture/main.py` still runs on a single systemd timer at the finest
  cadence (15 min) and, on each tick, skips any cam that isn't due yet: due-ness compares
  `now` against the cam's most recent `capture_log` entry (any outcome — saved, stale, or
  fetch_failed all count as "checked"), using fixed epoch-aligned time buckets
  (`capture/capture_log.py`'s `bucket`/`is_due`) rather than "elapsed time since last run."
  The epoch-bucket approach specifically avoids a drift bug: comparing against elapsed time
  since the last run means a few seconds of processing latency can push a cam just past its
  interval threshold, deferring it a full tick and permanently inflating its effective
  interval (an "hourly" cam settling into running every 75 minutes). Bucketing by absolute
  time is immune to this since due-ness never depends on when the previous run finished. The
  North Carolina cams are set to `interval_minutes: 60`; everything else stays at 15.
- **Card migration to 64GB, then more cams (2026-07-30).** With the SD card migration
  (`docs/open-questions.md` #11) done, the UNCA tower cam was re-enabled, SeaTac was added
  under `seattle`, and a new `washington` site was created for Washington-state cams that
  aren't Seattle-specific: Mount Rainier (moved out of `seattle`) and Kalaloch Lodge, both
  `interval_minutes: 15`. Speculative additions while there's card headroom — expect some to
  get trimmed later.
- **Hawaii cams added (Pi-only), first use of `type: youtube`.** Two Mauna Kea observatory
  cams — CFHT (Canada-France-Hawaii Telescope) and the Subaru Telescope — under a new
  `hawaii` site. Both are YouTube live streams, not plain images, which needed a new capture
  type: `capture/fetch.py`'s `fetch_youtube_frame` resolves the configured YouTube watch/
  channel URL to a direct stream URL via `yt-dlp -g -f best`, then feeds that URL to the
  existing `fetch_stream_frame` (ffmpeg) exactly as `type: stream` does — `yt-dlp` is the only
  new piece, added as a dependency in `requirements.txt`, and `ffmpeg` itself is now a
  documented system dependency in `deploy/pi/README.md` rather than a currently-unused one.
  `cfht`'s configured URL is a fixed watch URL (a long-running 24/7 stream); `subaru-
  telescope`'s is a channel `/live` URL, since that channel's live video ID changes over
  time. Both `interval_minutes: 15`, though this is a heavier per-fetch than a plain image GET
  (yt-dlp resolution + ffmpeg decode vs. one HTTP GET), worth revisiting if Pi CPU load
  becomes a problem. The Pi now captures ten cams total.

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
    sea-tac/
      2026/07/
        ...
  washington/
    mount-rainier/
      2026/07/
        ...
    kalaloch-lodge/
      2026/07/
        ...
  hawaii/
    cfht/
      2026/08/
        ...
    subaru-telescope/
      2026/08/
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
  `seattle`, `washington`, `hawaii`, and `north-carolina`), and `capture/main.py` writes to
  `archive_root / site /
  name`. Grouping by site keeps the two Bluewood cams together and separate from the
  Seattle pipeline-development cams, the Washington-state cams, the Hawaii cams, and the
  North Carolina cams — and lets a single config
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
`capture/` code on the Pi (hostname `timelapse-pi`), writing to local disk at
`/var/lib/timelapse/archive` instead of committing to git (see storage below), and capturing
all ten active cams (most every 15 minutes, unca-tower hourly). GitHub Actions ran in
parallel for a ~1-2 week trial to confirm the
Pi was reliable; that trial is complete, its schedule trigger is disabled, and manual
`workflow_dispatch` stays available as an emergency fallback. The existing git-committed
frames were migrated onto the Pi's storage so the archive has one home, and `archive/` is no
longer tracked in git.

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
python -m video.main archive/bluewood/summit -o output/summit.mp4 --fps 24 --from 2026-01-05 --to 2026-02-20
python -m video.main output/normalized/drone-shots -o output/drone.mp4 --proportional --duration 30
```

Output videos are build artifacts (regeneratable from `archive/`, not source), so by
convention they're written under `output/`, which is gitignored.

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
- **Redesigned (2026-07)** as a mobile-friendly, dark-only card layout (design handoff:
  `reference.html`, a static prototype recreated in `web/generate.py`'s own
  string-templated HTML — no frontend framework introduced, matching the project's existing
  no-build-step approach). The old health table + full-width heatmap-per-cam layout was
  replaced by stat tiles + grouped cam cards; see below. No Dark/Light/System picker
  anymore — the reference design is dark-only, and the theme switcher (along with the
  Google Fonts–hosted JetBrains Mono the reference specified) was dropped in favor of a
  self-hosted monospace font stack, preserving the "no external assets" self-contained
  requirement enforced by `tests/test_generate.py`.
- **Regeneration:** the capture service runs it as an `ExecStartPost` after each capture,
  so the page refreshes every run. Because the generator reads `archive_dir` from the
  config, it shows exactly the frames in that directory — on the Pi, only Pi-captured
  frames (the "Pi-era only" activity scope), with no source-era filtering logic of its own.
- **Stat tiles:** "Disk free" (with a used-space progress bar) and "Runway" (an estimated
  days-of-space-left figure, `disk_free_bytes / projected_daily_bytes`, flagged in warning
  red at or below `RUNWAY_WARN_DAYS`, currently 14). "Runway" reads "steady" rather than a
  number when there's no burn-rate data yet, instead of dividing by zero or fabricating a
  figure.
- **Cam cards, grouped by site:** each card shows the live thumbnail with a status pill
  (`LIVE`/`STALE`) and last-frame time overlaid — the last-frame time is followed by its
  configured capture cadence (e.g. "5m ago · every 15 min"), so it's easy to judge whether
  that gap is normal for the cam rather than a sign of trouble; omitted for a cam with no
  `interval_minutes` in the current config (e.g. decommissioned) — then avg frame size /
  frame count / disk usage, then a 31-day recent-activity strip (a quieter, lower-detail
  cousin of the full heatmap — see below) and a "full history →" link. An optional per-cam
  `display_name` config key, if set, renders as a small caption above the cam's slug in the
  thumbnail overlay (e.g. "Canada-France-Hawaii Telescope" above `cfht`) — added for the
  Hawaii cams, whose slugs alone aren't obviously identifiable; a cam with no `display_name`
  set shows just its slug, unchanged from before. A group header shows
  a stale-count badge when any of its cams are stale (hidden otherwise). An optional,
  off-by-default `web_show_stale_banner: true` config key additionally surfaces an amber "N
  of M cams stale" banner at the top of the page — off by default because some cams are
  intentionally disabled for stretches and the team didn't want that flagged on every visit.
  Site groups
  are ordered by the config's top-level `site_order` list (e.g. `[bluewood, seattle,
  washington, hawaii, north-carolina]`); a site with archived frames but no entry in
  `site_order`
  (e.g. a
  decommissioned site) sorts after the listed ones, alphabetically.
- **Full-history modal:** "full history →" opens a bottom-sheet with the same 13-week
  GitHub-style contribution grid the old design showed inline, now per-cam and hidden until
  opened. Built with **CSS `:target`** (a `<a href="#history-{cam}">` / `#history-{cam}"`
  overlay pair) rather than JavaScript, keeping the page's "no `<script>` tag" constraint —
  each cam's grid is pre-rendered server-side into its own hidden overlay `<div>`, so there's
  no client-side calendar logic to duplicate. The close link/scrim both point at `href="#!"`
  rather than `href="#"` — a bare `#` also targets the document top and yanks the page's
  scroll position, which `#!` (matching no element) avoids.
- **Daily drill-down:** clicking a day cell in the full-history grid reveals that day's
  hourly (24-cell) breakdown directly below the grid, in the same modal — same `_level()`
  bucketing as the other heatmaps, but scoped to that single day's own busiest hour rather
  than the cam's all-time peak. Built the same no-`<script>`-tag way as the rest of the
  page's interactivity: every non-empty day's sub-grid is pre-rendered server-side into a
  hidden `<div>`, and a small inline `onclick` (matching the existing tap-to-reveal-tooltip
  pattern) toggles it visible and hides whichever day was previously shown. Days with zero
  frames get no sub-grid at all — nothing to show, and it keeps output size proportional to
  actual archive data rather than a fixed ~91-day cost per cam regardless of how sparse the
  archive is.
- **Activity leveling:** both the 31-day strip and the full-history grid use the same
  `_level()` bucketing, now a simple off/low/high (3-color) scale rather than the old
  5-color one — visually quieter, matching the reference's intent for the strip to read as
  secondary information, not a primary alert.
- **Health/status:** last frame per cam, how long ago, and a staleness flag drive the
  `LIVE`/`STALE` pill and card border. Each cam's stale threshold is `STALE_MULTIPLIER` (2)
  × its own configured `interval_minutes` — not a single global cutoff — since cams can run
  on different cadences (see Component 1's per-cam interval note). A cam with archived
  frames but no entry in the current config (decommissioned) always reads as stale rather
  than guessing an interval for it. Status needs the persisted `capture.log` from
  Component 1 — it can't be derived from successful frames alone, since a stuck/failing cam
  produces *no* new archive entries.
- **Burn rate:** feeds the "Runway" stat tile — sums every cam's bytes captured so far today
  and projects a full day from the elapsed hours since midnight
  (`bytes_today / elapsed_hours * 24`). Suppressed for the first 15 minutes of the day, where
  a single frame divided by a near-zero elapsed time would spike to a meaningless number;
  `None` in that window (and whenever there are no frames at all) rather than a misleading
  figure.
- **Browsing the archive:** each cam's overlaid name links to its live image, and the
  generator symlinks `www/archive` to `archive_dir` on every run so the full frame archive is
  reachable as a plain directory listing at `/archive/` — no copying, and no new serving
  code (`http.server` follows the symlink). Same home-network/no-auth trust model as the
  rest of the page; see `docs/open-questions.md` #8. A proper gallery view (paginated by
  day/cam with thumbnails, generated the same way as the heatmap) is a natural next step on
  top of this rather than a new access decision.
- **Pulling frames off the Pi for local video builds:** `capture/sync_archive.py` is the
  client side of that same `/archive/` listing — it recursively mirrors one cam's directory
  tree from the Pi into a local `archive/<site>/<cam>/` (the same layout `video/main.py`
  already expects), skipping any frame already present locally so re-running it after the
  first sync only fetches what's new: `python -m capture.sync_archive <site> <cam>`.
  Deliberately just a downloader, not a new archive format or protocol — the directory
  listing above is the only interface it needs.
- **Footer:** host stats (uptime, memory, load average, read straight from `/proc` rather
  than a library like `psutil` — a C-extension dependency isn't guaranteed to have a
  prebuilt wheel on a Pi Zero W's armv6), how long this run took to gather its data
  (`build_page_data` + `system_stats` + `read_git_info` — the archive scan, capture log,
  `/proc` reads, and git subprocess; excludes the page's own HTML-string assembly, which is
  pure CPU and comparatively negligible) so a slow regeneration can be diagnosed as I/O vs.
  added-feature overhead, and a deployment marker (commit sha8 + date, from `git log`) so
  it's obvious which commit is actually live given the Pi auto-updates from `main` on a
  10-minute timer. Each stat/marker is included only if its underlying read succeeded, so
  the footer degrades gracefully (e.g. a non-git deployment) rather than showing a
  fabricated value.
- **Favicon/logo (2026-07):** a small camera-outline mark with a clock face standing in for
  the lens (camera + time, for "timelapse") and a green flash dot matching the `LIVE` status
  pill's color. Defined once as `_LOGO_SVG_BODY` in `web/generate.py` and reused two ways:
  inline next to the "Capture Status" title, and percent-encoded into a `data:image/svg+xml,`
  `<link rel="icon">` href for the favicon — no sibling asset file, preserving the page's
  "single self-contained page, no external assets" design. Shapes are unfilled
  (`fill="none"`) so the mark reads on any background, not just the page's own dark one —
  it also has to work as a favicon on a light browser tab strip.
- **Remote access:** not built now; Tailscale is the documented future option, and would also
  cover remote SSH to the Pi for maintenance, not just this page.

## Component 4: drone photo normalization

**Implemented** in `normalize/` (`align.py`, `main.py`, `control_points.py`, `annotate.py`).
A separate build-time input path from the fixed webcams: drone photos aren't captured on a
schedule by this project, they're an existing batch of images with slightly varying
position, angle, and altitude between shots, which would make a naive frame-concat timelapse
look shaky. Normalization aligns and crops a directory of them onto a common frame so they
cut together smoothly, before handing off to the video builder (Component 2).

```
python -m normalize.main path/to/drone-photos output/normalized/drone-shots --size 1920x1080
```

Pipeline, entirely local (OpenCV + Pillow + numpy, no network calls, no AI model):

1. **Order** photos by EXIF capture time (`DateTimeOriginal`, falling back to the `DateTime`
   tag, falling back to file mtime if a photo has no EXIF data at all) — not filename, since
   drone photo filenames aren't necessarily chronological.
2. **Estimate an alignment** from a reference frame (first photo in that capture-time order,
   unless `--reference` overrides it) to each other photo. Three ways to do this exist, in
   increasing order of manual effort and precision — see "Three ways to align a photo" below.
3. **Warp** each photo into the reference's coordinate space, tracking which pixels are real
   image data vs. the black border the warp introduces.
4. **Handle each frame's black border** (`--crop`, default `none`): every warped frame has
   some black border where the warp didn't reach, and there are two ways to deal with that.
   `none` (default) leaves it as real black and keeps every frame at the reference's full
   size. `intersection` instead intersects every aligned frame's valid-pixel mask and shrinks
   an axis-aligned box border-by-border (whichever edge has the fewest valid pixels) until
   it's fully valid, guaranteeing no black edges anywhere — but that guarantee gets expensive
   fast: each frame's own border, intersected against every other frame's, compounds as more
   frames are aligned. On this project's actual 118-photo drone archive, the single
   best-aligned frame alone already lost ~46% of its area to its own border, and the region
   valid across all of them shrank to ~22% of the frame by the time all were combined — for a
   batch this size, `intersection` was discarding most of every photo. `none` is the default
   for exactly that reason.
5. **Resize** (optional, `--size`) to a final fixed output size.
6. **Write a manifest**: `manifest.json` in the output directory, mapping each aligned
   frame's filename to its EXIF capture timestamp (ISO 8601). This exists because step 3's
   `cv2.imwrite` silently strips EXIF from the warped/cropped output — without the manifest,
   every timestamp recovered in step 1 would be lost the moment the frame is written back
   out. The video builder (Component 2) reads this manifest to recover each frame's real
   capture time, which its proportional-duration timing mode depends on. Frames
   `normalize_sequence` skipped (step 2) are correctly absent from the manifest.

This is intentionally a standalone preprocessing step rather than folded into
`capture/archive.py` — it's a different pipeline shape (batch import vs. scheduled capture)
and matches the project's "archive raw, filter/normalize at build time" principle: the
normalization choices here (alignment method, the crop heuristic, match threshold) are
exactly the kind of decision that should be re-runnable, not baked into capture.

### Three ways to align a photo

Real usage on this project's ~118-photo drone archive (aerial farmland: mostly low-texture
grass/dirt, spanning two years of seasonal appearance change, several distinct drone
zoom/altitude settings) worked through three approaches, each a real tradeoff rather than a
strict upgrade path — pick the cheapest one that actually gets a clean result for the content
at hand:

| Method | `--method` | Cost | Best for | Watch out for |
| --- | --- | --- | --- | --- |
| Sparse feature matching | `orb` | Fast | Scenes with genuine texture/structure (buildings, rock, urban) | Starves on low-texture scenes (grass/dirt/pavement) — keypoint budget gets diluted by noisy texture, crowding out the few reliable structural keypoints |
| Intensity-based alignment | `ecc` (default) | Slower (coarse-to-fine pyramid per photo) | Low-texture scenes; survives large seasonal/appearance change (bare ground ↔ snow) as long as structure is still visible | A *global* correlation score can look good while still being locally imprecise on secondary structures — see the gotcha below. Also sensitive to how much destination-image resolution disagreement exists (see the shape-mismatch gotcha) |
| Manual control points (anchors) | `--control-points` | Most manual effort, but exact | Any photo automated matching can't precisely handle: a distinct zoom/altitude grouping, an extreme angle, a lighting condition too far from the reference | Only as good as the points clicked — see the tool guidance below |

On this project's actual footage, `orb` at its default settings aligned only ~7% of photos
(most got excluded by too few keypoints), `ecc` alone got to ~94%, and `ecc` + a modest set
of manually-annotated anchors (control points on ~15 photos, covering the distinct
zoom/altitude groupings and a few individually-troublesome shots) got effectively all
non-rejected photos aligning cleanly. The jump from "mostly works" to "actually looks right"
came from the anchors, not from tuning `ecc`'s thresholds further — a global match-confidence
score doesn't guarantee local precision, and no amount of threshold-tuning fixes that.

**Two concrete gotchas worth remembering** if this code gets touched again:
- `cv2.findTransformECC` silently produces a *wrong* transform (not an error) if the
  template and input images aren't the same pixel shape — it does not resample them to match
  internally, despite nothing in its signature suggesting that requirement.
  `estimate_alignment_ecc` resizes the target to the reference's exact dimensions before
  calling it for exactly this reason (the affine model's independent x/y scale absorbs any
  resulting aspect-ratio distortion).
- `cv2.findTransformECC`'s return value is easy to get backwards (it maps in the opposite
  direction the shape suggests at first read), and a reversed-direction bug can still produce
  plausible-looking small-scale warps in a quick visual check while being badly wrong on real
  rotation/scale.

Both are the kind of bug that passes a casual visual check and only shows up as a hard-to-place
"this doesn't quite look right" — verify any change to `estimate_alignment_ecc` against a
synthetic pair with a *known* transform (recover it exactly, not just "looks plausible"), not
just eyeballed overlays.

### Manual control points (anchors)

**Implemented** in `normalize/control_points.py` (data model + homography math) and
`normalize/annotate.py` (the tool). For photos automated matching can't precisely handle —
common when a batch spans several distinct zoom/altitude groupings, since even a
high-confidence match to the single global reference can be locally imprecise on secondary
structures the confidence score doesn't weight heavily — a person can click a handful of
point correspondences (e.g. the same barn corner) between the reference and one "anchor"
photo, once. Because a person picked them, the resulting alignment is exactly as accurate as
those clicks, not bounded by how much texture the scene happens to have.

```
python -m normalize.annotate path/to/drone-photos control_points/<name>.json \
    --reference path/to/drone-photos/some_photo.jpg
```

- **The tool** is a local `http.server` page (stdlib only, no new dependency) meant to be
  opened in your own browser, not driven by Claude — the reference photo and one other photo
  side by side; click the same physical point on both (bold, sharp landmarks — building
  corners, fence-post junctions — not open grass, which can't be clicked precisely, and not
  anything that moves between shots, like shadows or vehicles) to record a correspondence. At
  least 4 pairs turns the current photo into an anchor (`cv2.findHomography`, plain
  least-squares — these are hand-verified correspondences, not noisy automated matches, so
  there's no outlier to guard against with RANSAC); 5-8 spread across the frame is better than
  exactly 4, since 4 points fit with zero residual and one imprecise click warps the whole
  image. By default every photo in the input directory is browsable (Prev/Next paging, a
  jump-to dropdown, an "only unreviewed" filter) — not just a suggested few — so a full triage
  pass of a whole archive is practical. **Reject** marks a photo as not belonging in the
  sequence at all (or too poor an angle/zoom to be worth aligning) without needing points;
  `normalize_sequence` excludes a rejected photo entirely, whether it would have been an
  anchor or matched automatically.
- **Saves straight to disk** as you click — nothing to download or copy/paste, and the
  process can stop and resume anytime; the control points file just accumulates.
- **How a regular (non-anchor, non-rejected) photo uses the anchors**: `normalize_sequence`
  tries an automated match (`--method`) against the reference *and* every anchor, keeping
  whichever scores best, then composes that automated estimate with the winning anchor's
  manually-verified transform (chaining a 2x3 affine with the anchor's 3x3 homography via
  `warp_to_reference`, which dispatches to `cv2.warpAffine`/`warpPerspective` by matrix shape).
  This is more accurate than matching everything against one global reference on a batch
  spanning several distinct groupings: the automated step only has to close the smaller gap
  to the *nearest* anchor, and the anchor's own human-verified transform carries the rest of
  the way exactly.
- **The control points file** (`control_points/<name>.json`) is a plain JSON dict —
  `{"reference": ..., "anchors": {name: {points_reference, points_anchor}}, "rejected":
  [...]}` — meant to be committed to the repo. Unlike `output/` (regeneratable from
  `archive/`, gitignored), the clicks (and rejections) it records are real, non-regeneratable
  manual work: re-running `normalize.main` with `--control-points` later, as more photos are
  captured, doesn't require re-clicking anything already annotated. No image data is stored
  in it, only pixel coordinates and filenames, so it stays safe to check in even though
  `archive/`/`output/` (the actual photos) are not.

## Component 5: video build-time QA labels and color correction

**Implemented** in `video/labels.py` and `video/color.py`, wired into `video/main.py` as
opt-in flags. Both operate on already-selected frames right before encoding (Component 2's
pipeline), not on the archive or normalize output — regeneratable, build-time-only, matching
the project's "archive raw, filter/normalize at build time" principle.

### QA labels (`--label-date`, `--label-filename`)

Burns bold white-on-black text into the bottom-left corner of every frame: capture date
(`--label-date`, e.g. "July 24, 2026", larger) stacked above source filename
(`--label-filename`, smaller), with padding between them and from the frame edge. Meant to
be turned on while dialing in alignment/anchors/color-correction settings — so a frame that
looks wrong can be immediately traced to a specific date/file — and turned back off for the
actual final render; not something that's meant to stay in a finished video.

```
python -m video.main output/normalized/drone-shots -o output/preview.mp4 \
    --proportional --duration 30 --label-date --label-filename
```

Uses `PIL.ImageFont.load_default(size=...)` (Pillow's own bundled font — portable across the
dev machine and the Pi, no system font file dependency) with `stroke_width` as a fake-bold
technique (Pillow's default font has no bold variant of its own).

### Color-cast correction (`--white-balance`)

For a batch where lighting drifts across frames (e.g. some shots late in the day, warm/red;
others midday, neutral) but scene *content* varies too much between frames for a generic
whole-image histogram match to be safe. A histogram match forces every frame's color
statistics toward one reference's — which also drags a snow-covered frame's whites toward
whatever hue a lush-green reference happens to have, or strips a naturally green summer
frame's color entirely. **Decided against** for exactly that reason.

Instead: sample one small, fixed pixel region — a patch on some physically neutral-ish
surface (this project uses a metal roof) visible across the batch at a consistent location,
which only works because the frames are already aligned into a shared coordinate space
(Component 4). Comparing that one patch's color between a frame and a chosen "target" frame
(one with lighting you want the rest of the batch to look like) gives a per-channel gain that
corrects the *lighting's* color cast without touching color relationships elsewhere in the
frame the way a full histogram match would — safe for the snow frame, since its own patch
reading is already close to neutral and the correction stays gentle.

```
python -m video.main output/normalized/drone-shots -o output/out.mp4 \
    --proportional --duration 30 \
    --white-balance \
    --white-balance-patch 1050,920,1120,1000 \
    --white-balance-target output/normalized/drone-shots/some_well_lit_photo.jpg
```

- `--white-balance-patch top,left,bottom,right` — pixel box in the frames' shared aligned
  coordinate space (matching `align.py`'s crop_box convention). Sequence-specific: there's no
  sensible default, since the right patch location depends entirely on where a suitable
  neutral surface happens to sit in that particular reference frame.
- `--white-balance-target` — path to the photo whose patch color is the correction target.
- **Safety bounds** (`video/color.py`), both discovered from real failures on this project's
  footage, not designed in speculatively upfront:
  - `MIN_PATCH_BRIGHTNESS`: a patch reading too dark to trust (e.g. it fell in shadow for
    that one frame) skips correction for that frame entirely rather than dividing by a
    near-zero denominator into a wild gain.
  - `DEFAULT_GAIN_CLAMP` (0.5-2.0): bounds the *ratio* a patch comparison can imply,
    regardless of how extreme the raw comparison looks.
  - `limit_gains_for_headroom`: **the clamp above isn't sufficient by itself** — a frame can
    have a perfectly plausible, in-range gain that's still too large for that specific
    frame's own content. A real example: an early-morning frame's patch read dim next to an
    already-bright, sunlit arena elsewhere in the same frame; the patch-implied gain (~2x,
    within the clamp) blew out real detail across nearly half the image when applied
    uncapped. This function checks the *frame's own* highlights (99th percentile, so a
    handful of already-blown-out specular pixels don't distort the check) before applying a
    gain, and scales it down (never up) so correction can't push those highlights past
    saturation — the frame ends up correctly close to its original exposure instead of
    blown out, which is the right outcome when a strong correction and a well-exposed frame
    conflict.

## Storage: frames and bucket sync

**Decided** (see `docs/open-questions.md` #5): frames are written to local disk on the Pi at
capture time, then synced periodically to a cloud bucket via `rclone` as an off-device
backup. Bucket provider (AWS S3 vs. Backblaze B2 vs. Google Drive) is still open — `rclone`
backs all three with the same sync command, so the choice doesn't change this mechanism.
Google Photos was considered and set aside for *raw frame* storage specifically — its
album/browsing model and 2025 API restrictions to app-created content are a poor fit for the
exact-byte round-tripping that stale-frame hash detection depends on — but remains a good fit
for finished videos (see deferred ideas below), which are naturally photo-library-shaped.

**SD card capacity (executed, 2026-07-30):** the Pi originally booted from a 4GB card. With
six cams capturing (up from four), that card's runway had gotten shorter than originally
planned — see `docs/open-questions.md` #11 for the growth estimate. The migration to a 64GB
card followed the runbook in **`docs/sd-card-migration.md`**: a fresh OS install on the new
card, `rsync` the existing archive over, verify, then physically swap cards — preferred over
a full-disk clone since it also re-validated `deploy/pi/README.md`'s bring-up steps and
avoided resizing a cloned partition table. With the extra headroom, the UNCA tower cam was
re-enabled and several new speculative cams were added (see the Cams history above).

## Deferred / follow-on ideas

- **Upload finished videos to Google Photos** automatically (the original impetus behind
  the `gphotos-uploader` repo name) — a better fit for finished videos than for raw frames,
  see storage section above.
- **Backfill** missed days from OnTheSnow's daily-image archive.
- Generalize beyond Bluewood: cams defined in a small config file (name, URL or stream,
  fetch method), so adding a third camera is a config change, not a code change.
- **Tailscale** for remote access to the web interface and Pi SSH, once wanted beyond the
  home network.
