# Design

## Context

- Source: [Ski Bluewood snowcams](https://bluewood.com/webcams/) — two cameras, **Summit**
  and **Base**, near Dayton, WA.
- Bluewood is **100% off-grid**: the webcams are powered by the resort's generator and go
  dark whenever it isn't running — nights, closed days, and the entire off-season. Downtime
  is the normal state, not a failure mode.
- [OnTheSnow archives daily images from Bluewood](https://www.onthesnow.com/washington/bluewood/webcams),
  which could serve as a backfill source for days the capture job misses.
- **Open homework item:** inspect how the cams are embedded on bluewood.com (the design
  sandbox couldn't reach the site). Two likely cases:
  - a plain `.jpg` URL that the page refreshes — trivial to fetch with an HTTP GET;
  - an embedded live stream (YouTube/HLS) — still workable; ffmpeg can grab a single frame
    from a stream (`ffmpeg -i <stream-url> -frames:v 1 out.jpg`).

  The capture code should isolate this behind a small "fetch one frame" function per cam so
  the rest of the system doesn't care which case it is.

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

Runs on a schedule (cron or equivalent — platform TBD, see open questions). Each run:

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
  summit/
    2026/01/  # one directory per month keeps directory sizes sane
      2026-01-15T08-30-00Z.jpg
      2026-01-15T08-40-00Z.jpg
  base/
    2026/01/
      ...
  capture.log        # one line per run: timestamp, cam, outcome (saved/stale/failed)
```

- Filenames are UTC timestamps → chronological sort order is lexical sort order, and the
  builder needs no database, just a glob.
- The log makes outages inspectable ("how long was the generator off last week?") and is
  the raw material if we ever want to render outage annotations into the video.

## Component 2: the video builder

A CLI that turns a slice of the archive into an mp4. Everything it does is a pure function
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

## Deferred / follow-on ideas

- **Upload finished videos to Google Photos** automatically (the original impetus behind
  the `gphotos-uploader` repo name).
- **Backfill** missed days from OnTheSnow's daily-image archive.
- Generalize beyond Bluewood: cams defined in a small config file (name, URL or stream,
  fetch method), so adding a third camera is a config change, not a code change.
