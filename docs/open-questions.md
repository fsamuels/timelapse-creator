# Open questions

Decisions that shape the implementation, with the options discussed and current
recommendations. None of these are locked in yet.

## 1. Where does the capture job run? (decided, for now)

**Decided:** GitHub Actions cron, running today. A Raspberry Pi Zero W was also ordered
2026-07-16 (free shipping, ETA ~12-18 days) and will likely take over capture once it
arrives — but that hand-off itself is still open (see below), so GitHub Actions stays the
capture job until then, not just a placeholder.

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

## 5. Storage and final destination

Where do archived frames live, and where do finished videos go?

- **Frames:** local disk (if home server), a cloud bucket (S3/GCS/B2), or committed to a
  git repo (only viable at low cadence — git is a poor fit for a growing image archive).
- **Finished videos:** local disk, cloud bucket, and/or **auto-upload to Google Photos** —
  the original idea behind the `gphotos-uploader` repo name, and a natural place for a
  nightly daily-clip to land.

**Recommendation:** frames live wherever the capture job runs (disk or bucket); Google
Photos upload of finished videos is a worthwhile follow-on once the pipeline works.

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

## 7. Where do frames land during the pre-Pi stopgap? (live)

`.github/workflows/capture.yml` runs the `capture/` code every 15 minutes and commits new
frames straight to `main`'s `archive/` directory — confirmed working end-to-end against the
real CameraFTP URLs (manual test run succeeded, then the recurring schedule was turned on).

This is still a deliberate short-term tradeoff, not the long-term storage answer from
question 5 above: committing a growing set of binary images to git works fine for a couple
of weeks at this volume, but isn't what we'd want for a full season. Revisit storage once
the Pi takes over as the real capture host — open question at that point: does the Pi push
frames to this same repo, to a bucket, or does GitHub Actions keep running as a redundant
second capture source (cheap insurance against the Pi being offline)?

Known caveat either way: GitHub auto-disables a scheduled workflow after 60 days with no
commits to the repo. Since commits only happen when a frame actually changes, a long enough
stretch of both cams being fully dark (e.g. a deep off-season closure) would eventually
silently turn the schedule off, needing a manual re-enable.

**Branch protection follow-up:** `main` currently has no GitHub branch protection, so the
`capture.yml` workflow's direct commits (question 7) are one reason it's been left open —
locking `main` to "PRs only" today would also block the capture bot. Once GitHub Actions is
disabled and archived frames are removed from git (question 5's real answer), revisit this
and add a GitHub ruleset requiring PRs on `main` for everyone, no bypass needed anymore.

## 8. Next concrete steps

- [ ] Pi Zero W arrives → decide capture hand-off (question 7)
- [ ] Build the video builder (`docs/design.md` Component 2) — currently just a design, no code
- [ ] Decide output format (question 3) and gap-handling-in-video (question 4) — needed
      before the video builder can be built, not just designed
- [ ] Decide long-term frame/video storage (question 5)
- [ ] Once GitHub Actions is disabled and archived frames are removed from git, lock `main`
      down with a GitHub ruleset requiring PRs for everyone (see question 7 follow-up)
