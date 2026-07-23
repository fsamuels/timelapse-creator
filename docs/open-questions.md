# Open questions

Decisions that shape the implementation, with the options discussed and current
recommendations. None of these are locked in yet.

## 1. Where does the capture job run? (decided)

**Decided:** a Raspberry Pi Zero W (`timelapse-pi`) is now the sole capture platform,
capturing all six cams every 15 minutes via a systemd timer. The Pi was ordered
2026-07-16.

### The Pi hand-off plan (decided; complete)

**Status:** the hand-off trial is over. The Pi (`timelapse-pi`) is deployed and live —
running the systemd capture timer against `capture/config.pi.yaml` (all six cams) and
serving the status page at `http://timelapse-pi.local:8080/`. The git-committed Bluewood
frames have been migrated onto the Pi's local archive, `capture.yml`'s `schedule:` trigger
has been removed (`workflow_dispatch` stays as a manual emergency-capture fallback), and
`archive/` is no longer tracked in git (added to `.gitignore`; the historical commits stay
in git history, no rewrite done).

- **Scheduling:** a systemd timer on the Pi, not cron — better logging (`journalctl`) and
  restart semantics, and it's a natural place to also manage the bucket-sync job and the web
  interface as sibling units. Same 15-minute cadence as today.
- **GitHub Actions during the transition:** keep `capture.yml`'s schedule running in parallel
  for a ~1-2 week trial once the Pi is live, watching the two sources agree, then disable the
  `schedule:` trigger (leave `workflow_dispatch` in place as a manual emergency-capture
  fallback). Not kept indefinitely — two independent 15-minute capture loops is only worth
  the redundancy short-term, while trusting the Pi's power/wifi/SD-card reliability.
- **Existing git-committed frames:** migrating everything captured so far by GitHub Actions
  onto the Pi's storage (see question 5) is straightforward and worth doing, so the archive
  has one home going forward instead of being permanently split across two eras. Any frames
  GitHub Actions commits during the trial period get folded in too once the trial ends, and
  `archive/` then stops being tracked in git (added to `.gitignore`; the historical commits
  stay in git history, no rewrite needed).

Hardware notes from picking the Pi:
- **Zero W vs Zero 2 W:** the 2 W (quad-core Cortex-A53) is ~4-5x faster than the original
  W (single-core ARM11) for the same ~$15, but this workload (an HTTP fetch every 15 min,
  occasional ffmpeg frame-grab) doesn't need it — went with the plain **W** since the 2 W was
  out of stock.
- **W vs WH:** identical boards; WH just has pre-soldered GPIO header pins. Irrelevant here —
  nothing GPIO-based is planned.
- **Real cost:** ~$15 board + ~$7 SD card + power supply (reused an old micro-USB phone
  charger — the Zero W powers over micro-USB, not USB-C) — well under the $50-100 "starter
  kit" price, since a case and HDMI/keyboard peripherals aren't needed for a headless,
  shelf-sitting capture box (Raspberry Pi Imager preloads WiFi + SSH onto the SD card before
  first boot).
- A **dedicated** Pi was chosen deliberately over reusing the existing TiltPi (homebrew
  fermentation tracker) Pi, to avoid resource conflicts / risk to that setup when brewing.
- An ESP32 (Arduino-IDE-programmable, C/C++) was considered as a fun embedded alternative —
  genuinely capable (WiFi + TLS + SD card), but a real project in itself (watchdog timer,
  NTP time sync, WiFi reconnect logic, streaming HTTPS response to SD instead of buffering
  in RAM) rather than an afternoon's work. Not pursued for now; still on the table as a
  side/fun build if of interest later.

Original option comparison, for reference:

| Option | Pros | Cons |
| --- | --- | --- |
| **Home server / Raspberry Pi** ✅ chosen (live) | Free to run, local storage, any interval, full control | Requires an always-on box at home |
| **GitHub Actions scheduled workflow** ✅ chosen (running now) | Free, zero hardware to own | Practical floor of ~10–15 min with scheduling jitter; runs occasionally skipped under load; frames must be pushed somewhere (repo or external storage) |
| **Cloud scheduler + function** (Cloud Run job / Lambda + EventBridge, writing to a bucket) | Most reliable timing, runs forever unattended, pennies per month | Most setup: cloud account, IAM, deployment |
| **Everyday computer (cron/launchd)** | Simplest possible | Only captures while the machine is awake — adds our downtime on top of Bluewood's |

## 2. Capture cadence (decided)

**Decided: every 15 minutes.** Live since 2026-07-16.

| Option | Frames/day/cam (daylight) | Storage/season (2 cams) | Notes |
| --- | --- | --- | --- |
| **Every 15 min** ✅ decided | ~50 | ~1–2 GB | Works on every platform incl. GitHub Actions; plenty for both daily and season videos. Confirmed free — this repo is public, so GitHub Actions minutes are unlimited. |
| Every 5 min | ~150 | ~3–6 GB | Smoother daily clips (clouds, lifts, parking lot); pushing the limits of GitHub Actions scheduling |
| Every 30–60 min | ~12–25 | < 1 GB | Fine for a season-long video; too choppy for interesting daily clips |

The "storage/season" column above was estimated for the original 2 Bluewood cams. The Pi
now captures 6 cams (2 Bluewood, 2 Seattle, 2 North Carolina); the two North Carolina cams
were the heaviest contributors to disk growth and now run on a 60-min `interval_minutes`
(everything else stays at 15) — see question 11 for the revised estimate and why it drove
an SD card upgrade regardless.

## 3. What video output do we actually want? (mostly decided)

Not mutually exclusive — the first two are presets of the third.

- **On-demand date ranges** ✅ built — `python -m video.main <dir> --from ... --to ... -o out.mp4`.
  Both a fixed-cadence webcam archive directory and a normalize/align.py output directory
  work as `<dir>`; see `docs/design.md` Component 2.
- **Daily clips** — a short sunrise-to-sunset clip per day, auto-generated each night. **Not
  built** — a preset on top of the same `video/frames.py`/`video/encode.py` machinery.
- **Season-long video** — snow accumulating and melting over the whole winter. **Not
  built** — same, plus a subsampling stage (e.g. "one frame per day at noon") that doesn't
  exist yet.

**Decided and built:** the on-demand CLI is the core; daily/season presets remain a
follow-on, not needed until the on-demand path proves out.

A second, new capability landed alongside the on-demand CLI, not originally scoped by this
question: **proportional (time-accurate) duration** — `--proportional --duration N` holds
each frame for a time proportional to the real gap before the next frame (capped by
`--min-hold`/`--max-hold`), instead of fixed fps. This is for irregularly-spaced batches —
namely drone photos, where some weeks have several flights and others have one — so the
video's pacing reflects actual coverage density rather than flattening every frame to equal
screen time. See `docs/design.md` Component 2 for the algorithm and its tradeoffs (mainly:
clamping means `--duration` is a target, not a guarantee).

## 4. How should outages look in the finished video? (decided)

**Decided: skip gaps silently, no overlay.** This reverses the earlier lean toward a
timestamp overlay below — the overlay wasn't wanted, not a case of the idea being
technically unworkable. `video/main.py`'s default (and only, in this pass) behavior for
gaps is exactly what plain concat already does: jump across them with no annotation.

| Option | Watchability | Effort | Status |
| --- | --- | --- | --- |
| **Skip gaps silently** ✅ decided, built | Smoothest; outages invisible | Zero | Default and only behavior |
| Timestamp overlay + skip | Smooth, but the burned-in clock shows time jumping across outages | Low (ffmpeg `drawtext`) | Considered, not wanted |
| "Power out" placeholder cards | Outages become explicit events in the video | Medium (synthesize card frames from the capture log) | Not built |

## 5. Storage and final destination (mostly decided)

Where do archived frames live, and where do finished videos go?

**Decided: frames live on the Pi's local disk, synced periodically to a cloud bucket** via
`rclone` (same sync command regardless of provider — it backs S3, B2, and Google Drive
alike, so the provider choice below doesn't change the mechanism). Local disk is the
capture-time write target (SD card wear isn't a concern at this write volume); the bucket
sync is the off-device backup.

**Still open: which bucket provider.**

| Option | Notes |
| --- | --- |
| **AWS S3** | Already have an AWS account — least new setup. |
| **Backblaze B2** | Cheaper at this volume; wanted to explore pricing/fit before deciding. |
| **Google Drive** | Considered as an alternative to a "bucket" proper — see below. |

At the season estimate of ~1-2 GB (both cams, 15-min cadence, per Component 1), cost is a
rounding error on any of the three — the real decision driver is ecosystem fit, not price.

**Google Photos vs. Google Drive, for raw frame storage specifically:** Drive is the better
fit if the Google ecosystem is preferred — arbitrary folder structure, exact-byte file
storage, which the stale-detection hash comparison in `archive.py` depends on. Google Photos
was considered and set aside for this purpose: its API was locked down in 2025 to mostly
app-created content, there's no more free unlimited-storage tier, and it's an album/browsing
model rather than a raw-file store — an awkward fit for reading back the previous frame's
exact bytes. Both would need an OAuth device-code flow with a refresh token persisted on the
headless Pi (more moving parts than a long-lived S3/B2 access key).

**Finished videos:** local disk, cloud bucket, and/or **auto-upload to Google Photos** — the
original idea behind the `gphotos-uploader` repo name. Unlike raw frames, a finished mp4 *is*
a photo-library-shaped artifact, so Photos remains a good fit here specifically — a natural
place for a nightly daily-clip to land. Worthwhile follow-on once the video builder exists.

## 6. How are the cams actually served? (resolved)

Both cams are hosted on **CameraFTP** (DriveHQ), a third-party webcam-hosting service, via
a "last image" REST endpoint — a plain JPEG, no scraping or stream-grabbing needed:

- Summit: `https://cameraftpapi.drivehq.com/api/Camera/LastImageaspx/shareID17403860/bwdsummit.jpg?`
- Base: `https://cameraftpapi.drivehq.com/api/Camera/LastImageaspx/shareID17403629/bwdbase.jpg?`

These are wired into `capture/config.yaml` as `type: image`. Not yet checked: whether the
endpoint sends useful `Last-Modified`/`ETag` headers (stale detection currently relies on
content hashing alone, which works regardless).

Bonus find: the cams are live *now*, in the off-season, because the resort is doing
maintenance and — notably — **replacing the old 3-person lift with a high-speed quad**.
That's a second, time-sensitive timelapse subject worth capturing alongside the season-long
snow one.

## 7. Where do frames land during the pre-Pi stopgap? (resolved — see question 1)

`.github/workflows/capture.yml` runs the `capture/` code every 15 minutes and commits new
frames straight to `main`'s `archive/` directory — confirmed working end-to-end against the
real CameraFTP URLs (manual test run succeeded, then the recurring schedule was turned on).

This was a deliberate short-term tradeoff, not the long-term storage answer from question 5:
committing a growing set of binary images to git works fine for a couple of weeks at this
volume, but isn't what we'd want for a full season. The hand-off question this section used
to leave open — does the Pi push to this same repo, to a bucket, or does GitHub Actions keep
running as a redundant source — is now decided; see "The Pi hand-off plan" under question 1.

Known caveat either way: GitHub auto-disables a scheduled workflow after 60 days with no
commits to the repo. Since commits only happen when a frame actually changes, a long enough
stretch of both cams being fully dark (e.g. a deep off-season closure) would eventually
silently turn the schedule off, needing a manual re-enable. Relevant again during the trial
period in question 1, less so afterward once the schedule is disabled deliberately.

**Branch protection follow-up:** `main` currently has no GitHub branch protection. That was
left open partly because `capture.yml`'s direct commits would otherwise be blocked by a
"PRs only" rule — now that GitHub Actions capture is disabled and `archive/` is no longer
tracked in git, that blocker is gone, and adding a GitHub ruleset requiring PRs on `main`
for everyone (no bypass needed) is a clean follow-up (see question 12).

## 8. The web interface (implemented)

Two goals: confirm the capture pipeline is still working, and show a GitHub-style activity
graph of images downloaded per day.

**Implemented and deployed** as `web/generate.py`, matching the decided stack below: it reuses
`capture/archive.py`'s `parse_frame_time` and regenerates one self-contained static HTML
page (inline CSS, a Dark/Light/System theme dropdown defaulting to dark, no persistence),
run as an `ExecStartPost` on the capture service and served by
`deploy/pi/timelapse-web.service` (`python -m http.server`). See `docs/design.md`
Component 3. It's live on the Pi at `http://timelapse-pi.local:8080/` (home network only).

**Stack — decided:** a small Python script (reusing `capture/archive.py`'s filename/timestamp
helpers directly) regenerates a static HTML page after each capture run, served by nginx or
even `python -m http.server` under systemd — no persistent app server. Matches the data's
actual cadence (it only changes every 15 minutes regardless) and the project's existing
"batch job, not a service" shape, and is the lightest option for the Pi Zero W's single core
and 512MB RAM.

Options considered and set aside for now:

| Option | Why not (yet) |
| --- | --- |
| Flask/FastAPI app | Natural upgrade path once something interactive is wanted (manual capture trigger, date-range filters) — overkill for a pure status page today. |
| PHP | Genuinely capable on this hardware, but would be the only non-Python code in an otherwise all-Python codebase — no reuse of `archive.py`'s parsing logic. Fine to revisit if wanted for its own sake, not because it's the leaner choice here. |
| Node/Express | Second runtime + npm dependency tree, no reuse benefit, more weight on a memory-constrained board. |

**Two views:**
- **Activity heatmap** — derived directly from archive filenames (the timestamp is already
  in the path); no new data source needed. **Decided: Pi-era only** — the generator reads
  `archive_dir` from the config, so on the Pi it shows only Pi-captured frames. This needs
  no explicit era-filtering code: it's just a consequence of pointing at the Pi's local
  archive rather than the repo's git-committed GitHub Actions history. **Tap-friendly
  tooltips (implemented):** the day cells' `title` tooltip only shows on hover, which isn't
  reachable on a touchscreen, so each cell also has an inline `onclick` (no `<script>` tag —
  stays self-contained) that copies its text into a visible line below the grid, giving touch
  users the same "N images on YYYY-MM-DD" detail a tap gives on desktop.
- **Health/status** — last successful frame per cam, last-run outcome, a "stale — no update
  in over N hours" flag, and now per-cam + total disk usage (`shutil.disk_usage` on
  `archive_dir`), plus a projected daily burn rate (implemented) at the top of the page —
  today's captured bytes across all cams extrapolated to a full day from the elapsed hours
  since midnight, hidden for the first 15 minutes of the day to avoid a wild early spike.
  This *does* need new data: see question 9.

**Browsing the raw archive (implemented):** each cam's name in the status table links out
to its live CameraFTP image, and `web/generate.py` symlinks `www/archive` to `archive_dir`
on every regeneration, so `python -m http.server` serves the whole frame archive as a
plain directory listing at `/archive/` next to the status page — no copying, no new code
in the web server. Deliberately full exposure, not just a thumbnail: same "home network
only, no auth" trust model as the rest of the page (see Access below), and it's the
groundwork for a proper gallery view later (paginated by day/cam instead of a raw file
tree) without having to revisit what's exposed.

**Thumbnail (implemented):** each cam's per-cam block shows its newest frame to the right of
its heatmap — an `<img>` pointed straight at the file under the `archive/` symlink above, so
there's no separate copy step to keep in sync, wrapped in a link to that same file so it
opens at full resolution instead of the thumbnail's cropped 108px-tall preview. Placed in
the per-cam block rather than the status table (already the busiest part of the page), and
beside the heatmap grid is safe because that grid stays a fixed 13-week width regardless of
archive size — it wasn't actually the growth risk it first looked like. Wraps below the
heatmap on narrow viewports.

**Access — decided:** home network only for now, and that now covers the raw archive too
(the `/archive/` symlink above), not just the generated page — the archive was fine to
expose since the trust boundary (home network, no auth) doesn't change. Tailscale (private
VPN mesh, no port forwarding) is noted as a documented future improvement rather than built
now — and would also solve remote SSH access to the Pi for maintenance, not just web UI
access, so it may be worth revisiting sooner than the web interface itself if remote
maintenance access is wanted in the meantime.

**Still open:** whether the LAN-only page needs any auth at all, or explicitly trusts the
home network (leaning the latter, but worth a conscious decision rather than an accidental
one).

## 9. Persisted capture log (implemented)

`docs/design.md` already flagged the lack of a persistent `capture.log` as worth revisiting.
The web interface's health/status view is the thing that makes it necessary: a fetch failure
currently only goes to stdout / the GitHub Actions run log, which doesn't survive on the Pi
and isn't queryable by a status page. **Implemented:** `capture/capture_log.py` appends one
JSONL line per cam per run (`ts`, `cam`, `outcome`: saved / stale / fetch_failed, `detail`),
wired into `capture/main.py` behind a `capture_log` config key — set in
`capture/config.pi.yaml`, absent from `capture/config.yaml` so GitHub Actions' behavior is
unchanged. Log rotation policy is still open, though low-stakes given the line is tiny and
only written every 15 minutes per cam.

## 10. North Carolina cams (resolved)

Two new cams were added to `capture/config.pi.yaml` under a new `north-carolina` site,
Pi-only (no `config.yaml` / GitHub Actions equivalent — unrelated to the Bluewood hand-off
trial):

- **UNCA tower cam:** `https://wlos.com/resources/ftptransfer/wlos/maps/Cam%20UNCA%20EcoNet.png`
- **Nantahala Outdoor Center cam:** `https://wlos.com/resources/ftptransfer/wlos/maps/Cam%20Nantahala%20Outdoor%20Center.png`

Both are hosted directly by WLOS (an Asheville TV station) as plain PNG snapshots — `type:
image` in the config, same `fetch_image` path as every other image cam. Content hashing for
stale-frame detection doesn't care that these are PNGs rather than JPEGs; it compares raw
bytes either way. Not yet verified from this environment: the outbound network policy here
blocks `wlos.com`, so the URLs are wired in unverified from the sandbox — the Pi's own
network isn't subject to that restriction, so first-run behavior should be checked via
`journalctl -u timelapse-capture.service` after deploying, same as any new cam.

The status page picks these up with no code changes — `web/generate.py` derives its site/cam
list from whatever's actually in `archive_dir`, and cam URLs from the config, so a new site
just appears once the Pi captures its first frame.

## 11. SD card capacity migration (documented, not yet executed)

The Pi currently boots from a **4GB** microSD card. Two things are now squeezing it:

- The OS + venv + dependencies already use a meaningful chunk of a 4GB card before any
  frames are captured.
- The archive just grew from 4 cams to 6 (see question 10) — using question 2's ~1–2 GB/
  season estimate for 2 cams as a baseline, 6 cams at the same 15-min cadence would have
  projected to roughly **~3–6 GB/season**, likely more: the North Carolina cams are PNG
  snapshots, which tend to be larger per frame than the Bluewood/Seattle cams' JPEGs — they
  turned out to be the two heaviest cams of the six by a wide margin. Moving them to a
  60-min `interval_minutes` (see question 2) cuts their combined contribution to roughly a
  quarter of that, but the card was already tight enough that the migration below is still
  warranted.

**Recommendation:** move to a 64GB card before the archive fills the current one. The
process is documented as a runbook in **`docs/sd-card-migration.md`** — a fresh-OS-install +
`rsync`-the-archive-over approach (recommended over a full-disk `dd` clone, since it doubles
as a from-scratch verification that `deploy/pi/README.md`'s bring-up steps still work, and
sidesteps partition-resize fuss). **Not yet executed** — this is the documented process for
when it's time, not a completed migration. In the meantime, watch actual growth via the
status page's per-cam and total disk-usage figures (`web/generate.py`, already built) rather
than relying on the estimate above.

## 12. Next concrete steps

- [x] Add the persisted capture log (question 9) — `capture/capture_log.py`, wired into
      `capture/main.py` via `capture/config.pi.yaml`'s `capture_log` key
- [x] Pi systemd scaffolding written as code — `deploy/pi/timelapse-capture.service`,
      `deploy/pi/timelapse-capture.timer`, `deploy/pi/timelapse-web.service`, and a bring-up
      doc (`deploy/pi/README.md`)
- [x] Pi Zero W deployed (`timelapse-pi`) and brought up per "The Pi hand-off plan"
      (question 1) using the `deploy/pi/` units; capture runs on the systemd timer against
      `capture/config.pi.yaml` (all six cams)
- [x] Confirm the Pi writes to real local disk (`/var/lib/timelapse/archive`) end-to-end on
      hardware, in the `<site>/<cam>/` layout
- [x] Add the two North Carolina cams (question 10) — `capture/config.pi.yaml`, Pi-only
- [ ] Execute the SD card migration (question 11) — runbook is written
      (`docs/sd-card-migration.md`), migration itself not yet done
- [x] Migrate the existing git-committed Bluewood frames onto the Pi's storage so the archive
      has one home (question 1, question 5)
- [ ] Pick a bucket provider (question 5) — evaluate Backblaze B2 pricing/fit against the
      existing AWS account before deciding; wire up `rclone` sync either way
- [x] Disable the GitHub Actions capture schedule and stop tracking `archive/` in git
      (question 1) — `workflow_dispatch` kept as a manual emergency-capture fallback
- [x] Build and deploy the static-HTML web interface (question 8) — `web/generate.py`
      (health/status + per-cam activity heatmap), regenerated via the capture service's
      `ExecStartPost` and served by `deploy/pi/timelapse-web.service`; live on the Pi at
      `http://timelapse-pi.local:8080/`
- [x] Script the Pi redeploy step — `deploy/pi/update.sh` wraps `git pull --ff-only`,
      dependency reinstall, and an immediate page regeneration into one command; unit-file
      changes still need a manual `systemctl restart` (documented in `deploy/pi/README.md`).
      A self-updating Pi (pull on its own schedule, no manual step at all) was considered and
      set aside for now — code would go live unattended between capture ticks, a bigger trust
      call than a one-command manual redeploy.
- [x] Build the video builder (`docs/design.md` Component 2) — `video/` (`frames.py`,
      `encode.py`, `main.py`): on-demand CLI over either a webcam archive directory or a
      normalize/align.py output directory, uniform-fps and proportional-duration timing
      modes, optional dark-frame/dedupe filters, ffmpeg concat-demuxer H.264 encode. Daily/
      season presets and a subsampling stage remain follow-ons.
- [x] Decide output format (question 3) and gap-handling-in-video (question 4) — on-demand
      CLI is the core (presets deferred); gaps are skipped silently, no timestamp overlay.
- [ ] Now that GitHub Actions is disabled and archived frames are removed from git, lock
      `main` down with a GitHub ruleset requiring PRs for everyone (see question 7
      follow-up) — a repo-settings change, not done as part of this cleanup
