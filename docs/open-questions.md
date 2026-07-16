# Open questions

Decisions that shape the implementation, with the options discussed and current
recommendations. None of these are locked in yet.

## 1. Where does the capture job run?

It must fire every few minutes, unattended, for an entire season.

| Option | Pros | Cons |
| --- | --- | --- |
| **Home server / Raspberry Pi** | Free to run, local storage, any interval, full control | Requires an always-on box at home |
| **GitHub Actions scheduled workflow** | Free, zero hardware to own | Practical floor of ~10–15 min with scheduling jitter; runs occasionally skipped under load; frames must be pushed somewhere (repo or external storage) |
| **Cloud scheduler + function** (Cloud Run job / Lambda + EventBridge, writing to a bucket) | Most reliable timing, runs forever unattended, pennies per month | Most setup: cloud account, IAM, deployment |
| **Everyday computer (cron/launchd)** | Simplest possible | Only captures while the machine is awake — adds our downtime on top of Bluewood's |

**Recommendation:** if an always-on home machine exists, use it — otherwise GitHub Actions
is the lowest-friction start and can be migrated later (the capture job doesn't care where
it runs).

## 2. Capture cadence

| Option | Frames/day/cam (daylight) | Storage/season (2 cams) | Notes |
| --- | --- | --- | --- |
| **Every 10–15 min** ✅ recommended | ~50 | ~1–2 GB | Works on every platform incl. GitHub Actions; plenty for both daily and season videos |
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

## 7. Where do frames land during the pre-Pi stopgap?

Until the Raspberry Pi arrives, `.github/workflows/capture.yml` runs the same `capture/`
code as a GitHub Actions job and commits new frames straight to `main`'s `archive/`
directory. This is a deliberate short-term tradeoff, not the long-term storage answer from
question 5 above: committing a growing set of binary images to git works fine for a couple
of weeks at this volume, but isn't what we'd want for a full season. Revisit storage once
the Pi (or a bucket) takes over as the real capture host.

The workflow currently only has a `workflow_dispatch` (manual) trigger, not a `schedule` —
intentionally, until a manual run is confirmed to work end-to-end against the real CameraFTP
URLs. Adding the recurring cron is the next step after that.
