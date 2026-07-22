# Open questions

Decisions that shape the implementation, with the options discussed and current
recommendations. None of these are locked in yet.

## 1. Where does the capture job run? (decided, for now)

**Decided:** GitHub Actions cron, running today. A Raspberry Pi Zero W was also ordered
2026-07-16 (free shipping, ETA ~12-18 days) and will likely take over capture once it
arrives — the hand-off plan itself is decided too, see the new section below.

### The Pi hand-off plan (decided, execution pending Pi arrival)

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
| **Home server / Raspberry Pi** ✅ chosen (arriving) | Free to run, local storage, any interval, full control | Requires an always-on box at home |
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

## 3. What video output do we actually want?

Not mutually exclusive — the first two are presets of the third.

- **Season-long video** — snow accumulating and melting over the whole winter. The classic
  payoff.
- **Daily clips** — a short sunrise-to-sunset clip per day, auto-generated each night.
- **On-demand date ranges** — `timelapse build --from ... --to ...`. Most flexible.

**Recommendation:** build the on-demand CLI as the core, add daily/season presets on top.

## 4. How should outages look in the finished video?

| Option | Watchability | Effort |
| --- | --- | --- |
| Skip gaps silently | Smoothest; outages invisible | Zero |
| **Timestamp overlay + skip** ✅ recommended | Smooth, but the burned-in clock shows time jumping across outages — fits the off-grid story | Low (ffmpeg `drawtext`) |
| "Power out" placeholder cards | Outages become explicit events in the video | Medium (synthesize card frames from the capture log) |

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

**Branch protection follow-up:** `main` currently has no GitHub branch protection, so the
`capture.yml` workflow's direct commits are one reason it's been left open — locking `main`
to "PRs only" today would also block the capture bot. Once GitHub Actions is disabled and
archived frames are removed from git (question 5's real answer), revisit this and add a
GitHub ruleset requiring PRs on `main` for everyone, no bypass needed anymore.

## 8. The web interface (decided: what to build, on what stack)

Two goals: confirm the capture pipeline is still working, and show a GitHub-style activity
graph of images downloaded per day.

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
  in the path); no new data source needed. Open detail: does it span the full season
  (including the pre-Pi GitHub Actions era) or just since the Pi took over — leaning full
  season for the more satisfying "contribution graph" story, but not blocking.
- **Health/status** — last successful frame per cam, last-run outcome, a "stale — no update
  in over N hours" flag. This *does* need new data: see question 9.

**Access — decided:** home network only for now. Tailscale (private VPN mesh, no port
forwarding) is noted as a documented future improvement rather than built now — and would
also solve remote SSH access to the Pi for maintenance, not just web UI access, so it may be
worth revisiting sooner than the web interface itself if remote maintenance access is wanted
in the meantime.

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

## 10. Next concrete steps

- [x] Add the persisted capture log (question 9) — `capture/capture_log.py`, wired into
      `capture/main.py` via `capture/config.pi.yaml`'s `capture_log` key
- [x] Pi systemd scaffolding written as code — `deploy/pi/timelapse-capture.service`,
      `deploy/pi/timelapse-capture.timer`, and a bring-up doc (`deploy/pi/README.md`). Not
      yet run anywhere: the Pi hardware hasn't arrived, so this is untested on-device.
- [ ] Pi Zero W arrives → bring up per "The Pi hand-off plan" (question 1) using the
      `deploy/pi/` units, smoke-test `capture/main.py --config capture/config.pi.yaml`
      on-device (watch for missing armv6 wheels — `requests` and `PyYAML` are both small
      enough to build from source if needed)
- [ ] Point the Pi config's `archive_dir` at real local disk and confirm it works end-to-end
      on hardware; migrate existing git-committed Bluewood frames onto it once the Pi also
      captures Bluewood (question 1, question 5)
- [ ] Pick a bucket provider (question 5) — evaluate Backblaze B2 pricing/fit against the
      existing AWS account before deciding; wire up `rclone` sync either way
- [ ] Keep GitHub Actions running in parallel for the trial period (question 1), then disable
      the schedule and stop tracking `archive/` in git
- [ ] Build the static-HTML web interface (question 8) once the capture log exists to read from
- [ ] Build the video builder (`docs/design.md` Component 2) — currently just a design, no code
- [ ] Decide output format (question 3) and gap-handling-in-video (question 4) — needed
      before the video builder can be built, not just designed
- [ ] Once GitHub Actions is disabled and archived frames are removed from git, lock `main`
      down with a GitHub ruleset requiring PRs for everyone (see question 7 follow-up)
