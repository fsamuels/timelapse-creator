# timelapse-creator

Tools for building timelapse videos from the [Ski Bluewood webcams](https://bluewood.com/webcams/)
(Dayton, WA) — and eventually any public webcam.

**Status: design phase.** Nothing is implemented yet. The documents below capture the ideas,
constraints, and open decisions from the initial design discussion, for review before any code
is written.

## The idea

Bluewood publishes two webcams (Summit and Base). This project will:

1. **Capture** frames from those cams on a regular schedule, all season long.
2. **Archive** every frame raw, with timestamped filenames.
3. **Build** timelapse videos from the archive on demand (daily clips, a season-long video,
   or arbitrary date ranges).

The defining constraint: Bluewood is **100% off-grid**. The webcams only work while the
resort's generator is running, so outages are the norm — every night, every closed day, the
whole off-season. The system must treat "cam is down" as ordinary operation, not an error.

## Documents

| Document | Contents |
| --- | --- |
| [docs/design.md](docs/design.md) | Architecture: the capture job, the video builder, storage layout, and outage/stale-frame handling |
| [docs/open-questions.md](docs/open-questions.md) | The decisions still open (platform, cadence, output format, gap handling, storage), the options for each, and current recommendations |

## Quick summary of the recommended defaults

- **Language/tools:** Python for the capture job and builder CLI; **ffmpeg** for video encoding.
- **Cadence:** capture every 10 minutes from each cam.
- **Archive:** raw JPEGs named by cam and UTC timestamp; never filtered at capture time.
- **Outages:** failed fetches are logged and skipped; *stale* frames (cam down but still
  serving its last cached image) are detected by content hash and discarded.
- **Video:** an on-demand builder CLI (date range → mp4), with "daily clip" and
  "season video" as presets; gaps are skipped, with a burned-in timestamp overlay so
  jumps in time are visible.

These are defaults, not decisions — see [docs/open-questions.md](docs/open-questions.md).
