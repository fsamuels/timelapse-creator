# Scripts

Every script here is a plain `python -m <module>` CLI, runnable locally against a checkout
of this repo — no need to route through an assistant. Run `--help` on any of them for the
authoritative, up-to-date option list; this doc exists so you don't have to.

Setup, once:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`ffmpeg` must also be on `PATH` (used by `capture/fetch.py`'s stream capture and by
`video/encode.py`'s render step). `yt-dlp` is additionally required for `type: youtube` cams.

Scripts are listed in the order you'd typically touch them: capture/sync frames in, build
video out. `normalize/` is a separate, on-demand path for non-webcam (e.g. drone) photo
batches — see `docs/design.md` Component 4 for when to reach for it over the webcam path.

## capture/main.py — run the capture job

Fetches every cam in a config, skips failures/stale frames, saves new ones to the archive,
and appends a capture-log entry if the config has one. This is what the Pi runs on a timer;
running it locally is mostly for testing a config change before deploying it.

```
usage: python -m capture.main [-h] [--config CONFIG]

options:
  --config CONFIG  Path to a capture config YAML file (default: capture/config.yaml)
```

```bash
python -m capture.main --config capture/config.pi.yaml
```

## capture/sync_archive.py — mirror the Pi's archive locally

Recursively mirrors one cam's frames from the Pi's `/archive/` directory listing into a
local `archive/<site>/<cam>/` — the same layout `video/main.py` expects. Frames already
present locally are left alone (filenames are immutable capture timestamps), so re-running
this after the first sync only downloads what's new.

```
usage: python -m capture.sync_archive [-h] [--base-url BASE_URL]
                                       [--archive-dir ARCHIVE_DIR]
                                       site cam

positional arguments:
  site                  e.g. washington
  cam                   e.g. kalaloch-lodge

options:
  --base-url BASE_URL      Pi status-page origin (default: http://timelapse-pi.local:8080)
  --archive-dir ARCHIVE_DIR  Local archive root to mirror into (default: archive)
```

```bash
python -m capture.sync_archive washington kalaloch-lodge
python -m capture.sync_archive bluewood base --archive-dir ~/timelapse-archive
```

## video/main.py — build an mp4 from a frame directory

Turns a directory of frames — a webcam archive cam directory, or a `normalize/` output
directory — into an mp4. Two timing modes: uniform fps (default) or `--proportional`, where
each frame's screen time is proportional to the real time-gap before the next frame (for
irregularly-spaced batches like drone photos). Optional date filtering, dark-frame/dedupe
filters, and two QA/build-time-only enhancements (labels, white balance). See
`docs/design.md` Component 5 for the reasoning behind each option.

```
usage: python -m video.main [-h] -o OUTPUT [--from SINCE] [--to UNTIL]
                             [--fps FPS] [--proportional] [--duration DURATION]
                             [--min-hold MIN_HOLD] [--max-hold MAX_HOLD]
                             [--drop-dark] [--dark-threshold DARK_THRESHOLD]
                             [--dedupe] [--label-date] [--label-filename]
                             [--white-balance]
                             [--white-balance-patch WHITE_BALANCE_PATCH]
                             [--white-balance-target WHITE_BALANCE_TARGET]
                             input

positional arguments:
  input                 Directory of frames: an archive cam directory, or a
                         normalize/align.py output dir

options:
  -o, --output OUTPUT   Output mp4 path
  --from SINCE           Only include frames on/after this date (YYYY-MM-DD)
  --to UNTIL              Only include frames on/before this date (YYYY-MM-DD)
  --fps FPS               Frame rate for uniform mode (default: 24.0); ignored with --proportional
  --proportional          Hold each frame proportional to the real gap before the next frame
  --duration DURATION     Target total video length in seconds (required with --proportional)
  --min-hold MIN_HOLD     Proportional mode: minimum seconds any one frame is held (default: 0.05)
  --max-hold MAX_HOLD     Proportional mode: maximum seconds any one frame is held (default: 2.0)
  --drop-dark             Drop frames below --dark-threshold mean brightness
  --dark-threshold N      Mean brightness (0-255) below which a frame counts as dark (default: 40.0)
  --dedupe                Drop frames byte-identical to the immediately preceding kept frame
  --label-date            Burn each frame's capture date into the bottom-left corner (QA aid)
  --label-filename        Burn each frame's source filename into the bottom-left corner (QA aid)
  --white-balance         Correct per-frame color cast by sampling a fixed patch (aligned frames only)
  --white-balance-patch T,L,B,R  Pixel box of a neutral-ish surface -- required with --white-balance
  --white-balance-target PATH    Photo whose patch color others are corrected to match -- required with --white-balance
```

```bash
# Whole archive, uniform 24fps
python -m video.main archive/bluewood/summit -o output/summit.mp4 --fps 24

# One date range, dark frames and duplicates dropped
python -m video.main archive/bluewood/summit -o output/summit.mp4 \
  --from 2026-01-05 --to 2026-02-20 --drop-dark --dedupe

# Proportional timing (irregular drone-photo batches), 30s target
python -m video.main output/normalized/drone-shots -o output/drone.mp4 \
  --proportional --duration 30

# Aligned drone frames with white-balance correction against a chosen frame
python -m video.main output/normalized/drone-shots -o output/out.mp4 \
  --white-balance --white-balance-patch 100,200,140,260 \
  --white-balance-target output/normalized/drone-shots/2026-03-01T12-00-00.jpg
```

## video/daily_clip.py — one day's clip

Preset over the same machinery: builds a single day's clip from a cam directory, defaulting
to yesterday (Pacific) with dark/night frames dropped — runnable unattended (e.g. a daily
cron job).

```
usage: python -m video.daily_clip [-h] -o OUTPUT_DIR [--date DATE] [--fps FPS]
                                   [--no-drop-dark]
                                   [--dark-threshold DARK_THRESHOLD]
                                   input

positional arguments:
  input                  Archive cam directory (e.g. archive/bluewood/summit)

options:
  -o, --output-dir DIR   Directory to write <date>.mp4 into (created if missing)
  --date DATE             Day to build (YYYY-MM-DD); default: yesterday (Pacific)
  --fps FPS               Frame rate (default: 24.0)
  --no-drop-dark          Keep night/dark frames instead of dropping them (dropped by default)
  --dark-threshold N      Mean brightness (0-255) below which a frame counts as dark (default: 40.0)
```

```bash
python -m video.daily_clip archive/bluewood/summit -o daily/bluewood/summit
python -m video.daily_clip archive/bluewood/summit -o daily/bluewood/summit --date 2026-02-14
```

## video/season_video.py — season-long, one frame/day

Subsamples a cam directory to one frame per day (closest to `--at-hour`), then encodes the
whole range as one video — for a season-long timelapse that would otherwise be unwatchably
long at every-15-minutes cadence.

```
usage: python -m video.season_video [-h] -o OUTPUT [--from SINCE] [--to UNTIL]
                                     [--at-hour AT_HOUR] [--fps FPS]
                                     [--proportional] [--duration DURATION]
                                     [--min-hold MIN_HOLD] [--max-hold MAX_HOLD]
                                     input

positional arguments:
  input                Archive cam directory (e.g. archive/bluewood/summit)

options:
  -o, --output OUTPUT  Output mp4 path
  --from SINCE          Only include frames on/after this date (YYYY-MM-DD)
  --to UNTIL             Only include frames on/before this date (YYYY-MM-DD)
  --at-hour AT_HOUR     Pick each day's frame closest to this hour, 0-23 (default: 12.0, noon)
  --fps FPS              Frame rate for uniform mode (default: 8.0); ignored with --proportional
  --proportional         Hold each day's frame proportional to the real gap before the next one
  --duration DURATION    Target total video length in seconds (required with --proportional)
  --min-hold MIN_HOLD    Proportional mode: minimum seconds any one frame is held (default: 0.05)
  --max-hold MAX_HOLD    Proportional mode: maximum seconds any one frame is held (default: 2.0)
```

```bash
python -m video.season_video archive/bluewood/summit -o output/season.mp4
python -m video.season_video archive/bluewood/summit -o output/season.mp4 \
  --at-hour 15 --proportional --duration 60
```

## normalize/main.py — align a drone-photo batch

Separate, on-demand batch input path for photos that aren't already fixed-position (e.g.
drone shots): aligns and crops a directory of photos onto a common frame so they cut into a
smooth timelapse. See `docs/design.md` Component 4 for a full comparison of the alignment
methods against this project's actual photo batches.

```
usage: python -m normalize.main [-h] [--reference REFERENCE]
                                 [--method {ecc,orb}]
                                 [--min-matches MIN_MATCHES]
                                 [--min-confidence MIN_CONFIDENCE]
                                 [--crop {none,intersection}] [--size SIZE]
                                 [--features FEATURES]
                                 [--control-points CONTROL_POINTS]
                                 input output

positional arguments:
  input                  Directory of source photos
  output                 Directory to write normalized frames into (e.g. output/normalized/<name>)

options:
  --reference PATH        Photo to align every other frame to (default: first frame, sorted by filename)
  --method {ecc,orb}       Alignment algorithm (default: ecc). ecc suits low-texture scenes
                           (grass/dirt); orb is faster, needs more texture
  --min-matches N          orb only: minimum agreeing feature matches to include a photo (default: 10)
  --min-confidence N       ecc only: minimum ECC correlation coefficient to include a photo (default: 0.2)
  --crop {none,intersection}  none keeps full reference size with black borders; intersection
                               crops to the region valid across all frames (shrinks fast on large batches)
  --size WxH               Resize final frames (default: keep cropped/full size)
  --features N             orb only: ORB keypoints per image (default: 20000)
  --control-points PATH    normalize/annotate.py JSON of manually-clicked anchor photos
```

```bash
python -m normalize.main path/to/drone-photos output/normalized/drone-shots --size 1920x1080
python -m normalize.main path/to/drone-photos output/normalized/drone-shots \
  --method orb --min-matches 15 --crop intersection
```

## normalize/annotate.py — manually anchor photos that don't auto-align

Local web tool (opens a small server on `--port`) for reviewing every photo in a sequence:
click control points onto ones worth using as a manually-verified anchor, or reject ones
that don't belong. Saves straight to disk as you click, so a pass can be stopped and resumed
anytime. Output feeds `normalize/main.py --control-points`.

```
usage: python -m normalize.annotate [-h] --reference REFERENCE
                                     [--photos PHOTOS] [--port PORT]
                                     input control_points

positional arguments:
  input             Directory of source photos
  control_points    Control points JSON file to write

options:
  --reference PATH  Reference photo
  --photos LIST     Comma-separated photo filenames to review (default: every photo,
                     capture-time order, excluding the reference)
  --port PORT
```

```bash
python -m normalize.annotate path/to/drone-photos control_points/drone-shots.json \
  --reference path/to/drone-photos/2026-03-01.jpg
```

## web/generate.py — regenerate the status page

Regenerates the static status/activity dashboard (what's live at
`http://timelapse-pi.local:8080/`). Running it locally is mostly for previewing a
`web/generate.py` change against a local archive before deploying.

```
usage: python -m web.generate [-h] [--config CONFIG] [--output OUTPUT]

options:
  --config CONFIG  Capture config YAML (for archive_dir / capture_log) (default: capture/config.yaml)
  --output OUTPUT  Output HTML path (default: config's web_output, else site/index.html)
```

```bash
python -m web.generate --config capture/config.pi.yaml --output site/index.html
```
