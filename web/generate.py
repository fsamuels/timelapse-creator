"""Static status-page generator for timelapse-creator.

Reads the frame archive (via the timestamped filenames) and the persisted capture
log, then writes a single self-contained HTML page: a disk/burn-rate summary up
top, then per-cam cards grouped by site showing the last frame, a recent-activity
strip, and a link to that cam's full multi-month history (a GitHub-style
contribution grid, opened as a bottom-sheet "modal" whose interactivity — the
open/close and the day-tap-for-detail behavior — is a mix of CSS ``:target``
and small inline ``onclick`` attributes; there's no ``<script>`` tag, keeping
the page dependency-free). Clicking a day with frames in that grid reveals an
hourly drill-down for just that day (see ``hourly_counts`` /
``day_details_for_grid``); days with no frames are skipped rather than given
an empty sub-grid.

Also symlinks the raw archive in next to the page (see ``ensure_archive_link``)
so it's directly browsable, reports per-cam and total disk usage, and shows a
footer of host stats (uptime, longest recorded uptime streak — see
``update_max_uptime_record`` — memory, load average, and how long this run
took to gather its data — see ``system_stats``) plus a deployment marker
(commit sha8 and date — see ``read_git_info``), so it's obvious which commit is
actually live given the Pi auto-updates from ``main`` on a 10-minute timer
(see ``deploy/pi/update.sh``).

Designed to run on the Pi after each capture (see deploy/pi/), regenerating the
page — no persistent app server. It reads ``archive_dir`` from the config, so it
naturally shows exactly the frames in that directory (on the Pi: Pi-captured
frames) with no source-era filtering logic of its own.
"""

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import yaml

from capture.archive import PACIFIC, parse_frame_time
from capture.capture_log import latest_outcomes, read_capture_log

CONFIG_PATH = Path(__file__).parent.parent / "capture" / "config.yaml"
REPO_DIR = Path(__file__).parent.parent
DEFAULT_OUTPUT = "site/index.html"
HEATMAP_WEEKS = 13  # ~a quarter, the full-history window shown per cam
RECENT_DAYS = 31  # the compact recent-activity strip on each cam card
HEATMAP_LEVELS = 2  # off / low / high — see _level()
STALE_MULTIPLIER = 2  # flag a cam stale after this many missed capture intervals
RUNWAY_WARN_DAYS = 14  # runway at or below this is flagged in warning red
DISK_FREE_WARN_PCT = 25  # % free at or below this turns the disk bar yellow
DISK_FREE_CRITICAL_PCT = 10  # % free at or below this turns the disk bar red


def scan_archive(archive_dir):
    """Return ``{site: {cam: [frame Path, ...sorted]}}`` for archive_dir.

    Expects the ``archive/<site>/<cam>/YYYY/MM/*.jpg`` layout. Missing or empty
    directories yield an empty mapping rather than an error.
    """
    archive_dir = Path(archive_dir)
    sites = {}
    if not archive_dir.is_dir():
        return sites
    for site_dir in sorted(p for p in archive_dir.iterdir() if p.is_dir()):
        cams = {}
        for cam_dir in sorted(p for p in site_dir.iterdir() if p.is_dir()):
            frames = sorted(cam_dir.rglob("*.jpg"))
            if frames:
                cams[cam_dir.name] = frames
        if cams:
            sites[site_dir.name] = cams
    return sites


def frame_bytes(frames):
    """Total size in bytes of a list of frame files."""
    return sum(f.stat().st_size for f in frames)


def thumb_url(frames, archive_dir):
    """URL for the newest frame, relative to the page (served via the
    ``archive`` symlink created by ``ensure_archive_link``); None if empty.
    """
    if not frames:
        return None
    return f"archive/{frames[-1].relative_to(archive_dir).as_posix()}"


def disk_usage(archive_dir):
    """Return ``{"total", "used", "free"}`` bytes for archive_dir's filesystem.

    None if archive_dir doesn't exist yet (nothing captured, or a fresh checkout).
    """
    archive_dir = Path(archive_dir)
    if not archive_dir.is_dir():
        return None
    usage = shutil.disk_usage(archive_dir)
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def read_uptime_seconds(path="/proc/uptime"):
    """Seconds since boot, from /proc/uptime. None off-Linux or if unreadable."""
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    return float(text.split()[0])


def read_memory_usage(path="/proc/meminfo"):
    """``{"total_kb", "available_kb"}`` from /proc/meminfo.

    None off-Linux, if unreadable, or if either field is missing.
    """
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(rest.split()[0])  # kB, per /proc/meminfo's own unit
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    return {"total_kb": values["MemTotal"], "available_kb": values["MemAvailable"]}


def system_stats(uptime_path="/proc/uptime", meminfo_path="/proc/meminfo", load_avg_fn=None):
    """Host stats for the status page footer: uptime, memory, load average.

    Reads straight from /proc rather than a library like psutil — this runs on
    a Pi Zero W (armv6), where a C-extension dependency isn't guaranteed to
    have a prebuilt wheel (see deploy/pi/README.md's wheel caveat). Load
    average (not instantaneous CPU%) is used for the same reason it's the
    standard Unix-ops proxy for "processor usage": sampling instantaneous
    usage needs two /proc/stat reads with a sleep in between, which would
    slow down every page regeneration; the paths/fn are injectable for tests.
    """
    load_avg_fn = load_avg_fn or os.getloadavg
    try:
        load_avg = load_avg_fn()
    except OSError:
        load_avg = None
    return {
        "uptime_seconds": read_uptime_seconds(uptime_path),
        "memory": read_memory_usage(meminfo_path),
        "load_avg": load_avg,
    }


def read_git_info(repo_dir=REPO_DIR, run_fn=None):
    """``{"sha8", "commit_date"}`` for the running checkout's HEAD commit.

    Used for the footer's deployment marker — the Pi auto-updates from ``main``
    on a 10-minute timer (see ``deploy/pi/update.sh``), so knowing which commit
    is actually live is otherwise a `git log` away. None if repo_dir isn't a
    git checkout or git isn't installed; run_fn is injectable for tests.
    """
    run_fn = run_fn or _run_git_log
    try:
        output = run_fn(repo_dir)
    except (OSError, subprocess.CalledProcessError):
        return None
    sha8, _, commit_date = output.strip().partition("\t")
    if not sha8 or not commit_date:
        return None
    return {"sha8": sha8, "commit_date": commit_date}


def _run_git_log(repo_dir):
    return subprocess.run(
        ["git", "log", "-1", "--format=%h\t%cs", "--abbrev=8"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def read_max_uptime_record(path):
    """Read the persisted max-uptime record: ``{"uptime_seconds", "start", "end"}``.

    None if ``path`` is falsy, missing, or unparseable.
    """
    if not path:
        return None
    path = Path(path)
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def update_max_uptime_record(path, uptime_seconds, now):
    """Update the persisted longest-uptime-streak record if this run's uptime
    is a new record; return the record (updated or unchanged) as ``{"uptime_seconds",
    "start", "end"}`` with ``start``/``end`` as ISO timestamps.

    ``start`` is this streak's boot time (``now`` minus uptime), recomputed each
    qualifying run rather than carried over, since it should land on the same
    moment every time (modulo clock drift) for as long as this is the same
    continuous streak. ``end`` rolls forward to ``now`` on every run that's
    still within the record streak, so it reads as "still ongoing" until a
    reboot ends it and the record freezes at its last value. None if ``path``
    or ``uptime_seconds`` is falsy (e.g. off-Linux, where uptime isn't readable).
    """
    if not path or uptime_seconds is None:
        return None
    record = read_max_uptime_record(path)
    if record and record.get("uptime_seconds", 0) >= uptime_seconds:
        return record
    record = {
        "uptime_seconds": uptime_seconds,
        "start": (now - timedelta(seconds=uptime_seconds)).isoformat(),
        "end": now.isoformat(),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return record


def ensure_archive_link(www_dir, archive_dir):
    """Symlink ``www_dir/archive`` to archive_dir.

    Lets http.server serve the raw frames (browsable directory listing, no
    copying) alongside the generated status page. A no-op if the link already
    points at archive_dir, and leaves anything else already at that path alone.
    """
    archive_dir = Path(archive_dir).resolve()
    if not archive_dir.is_dir():
        return  # nothing captured yet
    link = Path(www_dir) / "archive"
    if link.is_symlink():
        if link.resolve() == archive_dir:
            return
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(archive_dir, target_is_directory=True)


def bytes_captured_on(frames, day):
    """Total size in bytes of frames captured on ``day``."""
    return frame_bytes([f for f in frames if parse_frame_time(f).date() == day])


def daily_burn_rate(bytes_today, now):
    """Project a full day's disk usage from today's rate so far.

    None once there isn't enough of today elapsed to extrapolate from —
    dividing by a near-zero elapsed time would spike wildly off a single
    frame captured right after midnight.
    """
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_hours = (now - midnight).total_seconds() / 3600
    if elapsed_hours < 0.25:
        return None
    return {
        "bytes_today": bytes_today,
        "elapsed_hours": elapsed_hours,
        "projected_daily_bytes": bytes_today / elapsed_hours * 24,
    }


def daily_counts(frames):
    """Count frames per capture date (a ``{date: int}`` mapping)."""
    counts = {}
    for frame in frames:
        day = parse_frame_time(frame).date()
        counts[day] = counts.get(day, 0) + 1
    return counts


def hourly_counts(frames):
    """Count frames per capture date and hour (``{date: {hour: int}}``).

    A single pass alongside ``daily_counts`` rather than re-scanning frames
    per day; powers the daily drill-down heat map.
    """
    counts = {}
    for frame in frames:
        t = parse_frame_time(frame)
        day_counts = counts.setdefault(t.date(), {})
        day_counts[t.hour] = day_counts.get(t.hour, 0) + 1
    return counts


def cam_health(frames, outcome, now, stale_after):
    """Summarize one cam's health for the status view."""
    last_time = parse_frame_time(frames[-1]) if frames else None
    is_stale = last_time is None or (now - last_time) > stale_after
    return {
        "frame_count": len(frames),
        "last_time": last_time,
        "is_stale": is_stale,
        "outcome": outcome,
    }


def _level(count, peak, levels=HEATMAP_LEVELS):
    """Bucket ``count`` into ``0..levels`` relative to ``peak`` (0 = no activity)."""
    if count <= 0 or peak <= 0:
        return 0
    return max(1, min(levels, math.ceil(levels * count / peak)))


def heatmap_grid(counts, end_date, weeks=HEATMAP_WEEKS, levels=HEATMAP_LEVELS):
    """Build a GitHub-style grid: a list of weeks, each a list of 7 day cells.

    Weeks run Sunday-first and oldest-first; the last column contains end_date.
    Each cell is ``{"date", "count", "level", "future"}`` where level is a
    0..levels intensity bucket relative to the busiest day in the window and
    future cells (dates after end_date, padding out the final week) are marked
    so the page can render them blank.
    """
    days_since_sunday = (end_date.weekday() + 1) % 7
    last_week_start = end_date - timedelta(days=days_since_sunday)
    grid_start = last_week_start - timedelta(weeks=weeks - 1)
    # Scoped to the days actually shown — counts holds a cam's whole history,
    # and an old burst day outside this window shouldn't flatten the colors
    # of the window that's actually rendered.
    peak = max(
        (count for day, count in counts.items() if grid_start <= day <= end_date),
        default=0,
    )

    grid = []
    for w in range(weeks):
        week_start = last_week_start - timedelta(weeks=weeks - 1 - w)
        week = []
        for d in range(7):
            day = week_start + timedelta(days=d)
            count = counts.get(day, 0)
            week.append(
                {
                    "date": day,
                    "count": count,
                    "level": _level(count, peak, levels),
                    "future": day > end_date,
                }
            )
        grid.append(week)
    return grid


def hour_cells_for_day(hour_counts, levels=HEATMAP_LEVELS):
    """Build a single day's 24 hourly cells, leveled relative to that day's
    own busiest hour (each day's drill-down reads on its own scale, not the
    cam's all-time peak).
    """
    peak = max(hour_counts.values(), default=0)
    return [
        {
            "hour": h,
            "count": hour_counts.get(h, 0),
            "level": _level(hour_counts.get(h, 0), peak, levels),
        }
        for h in range(24)
    ]


def day_details_for_grid(hourly, grid):
    """Per-day hourly drill-down data (``{date: [24 cells]}``) for days in
    ``grid`` that actually have frames.

    Days with zero frames are omitted rather than given an all-empty
    sub-grid — nothing to show, and it keeps output size proportional to
    actual archive data instead of the full ~91-day window every time.
    """
    details = {}
    for week in grid:
        for cell in week:
            day = cell["date"]
            if not cell["future"] and day in hourly:
                details[day] = hour_cells_for_day(hourly[day])
    return details


def recent_strip(counts, end_date, days=RECENT_DAYS, levels=HEATMAP_LEVELS):
    """Build the compact ``days``-long activity strip shown on each cam card.

    A flat, oldest-to-newest list of ``{"date", "count", "level"}`` cells,
    leveled the same way as heatmap_grid but scoped to just this window.
    """
    start = end_date - timedelta(days=days - 1)
    peak = max(
        (count for day, count in counts.items() if start <= day <= end_date),
        default=0,
    )
    cells = []
    for i in range(days):
        day = start + timedelta(days=i)
        count = counts.get(day, 0)
        cells.append({"date": day, "count": count, "level": _level(count, peak, levels)})
    return cells


def _human_bytes(n):
    """Render a byte count like '482 KB' or '1.3 GB'."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _human_uptime(seconds):
    """Render seconds-since-boot like '3d 4h', '4h 12m', or '12m'."""
    total_minutes = int(seconds // 60)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _human_ago(delta):
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hr ago"
    return f"{hours // 24} days ago"


def _human_duration(seconds):
    """Render a duration like '428ms' or '1.8s'."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"


def _human_runway(days):
    """Render an estimated days-of-disk-space-left figure like '~3 days'.

    None (no burn rate yet, or burn rate is zero) reads as "steady" rather
    than a fabricated number.
    """
    if days is None:
        return "steady"
    if days < 1:
        return "<1 day"
    if days < 14:
        n = round(days)
        return f"~{n} day" + ("" if n == 1 else "s")
    n = round(days / 7)
    return f"~{n} week" + ("" if n == 1 else "s")


def _slug(text):
    """Turn an arbitrary site/cam name into a safe HTML id/URL-fragment."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def stale_after_for(cam_cfg):
    """How long a cam can go without a new frame before it's flagged stale.

    ``cam_cfg`` is None for a cam with no entry in the current config (e.g. a
    decommissioned camera whose archived frames are still on disk) — treated
    as unmanaged, so it always reads as stale rather than guessing an interval
    for it. A cam that *is* configured must declare its own
    ``interval_minutes``; there's no code-side default to drift out of sync
    with the actual capture schedule.
    """
    if cam_cfg is None:
        return timedelta(0)
    return timedelta(minutes=STALE_MULTIPLIER * cam_cfg["interval_minutes"])


def _site_sort_key(site_order):
    """Sort key factory: configured sites in ``site_order``'s order first,
    then any others (e.g. a decommissioned site whose old frames are still on
    disk but which no longer appears in the config) alphabetically after.
    """

    def key(site):
        try:
            return (0, site_order.index(site))
        except ValueError:
            return (1, site)

    return key


def build_page_data(archive_dir, log_path, now, cam_config=None, site_order=None):
    """Gather everything the template needs from the archive and the log.

    ``cam_config`` is the capture config's ``cams`` mapping (cam name -> dict
    with ``url``, ``interval_minutes``, and optionally ``display_name``), used
    to link each cam's name to its live image, size its stale threshold, and
    (if set) show a human-readable label above the cam's slug on its card.

    ``site_order`` is the config's top-level ``site_order`` list, controlling
    the order sites are displayed in; sites not listed sort after, alphabetically.

    Returns ``{"sites": [...], "disk": {...} or None, "burn_rate": {...} or
    None, "runway_days": float or None, "stale_count": int, "cam_count": int}``.
    """
    archive_dir = Path(archive_dir)
    sites = scan_archive(archive_dir)
    outcomes = latest_outcomes(read_capture_log(log_path))
    cam_config = cam_config or {}
    today = now.date()

    site_views = []
    bytes_today = 0
    stale_count = 0
    cam_count = 0
    for site in sorted(sites, key=_site_sort_key(site_order or [])):
        cams = sites[site]
        cam_views = []
        for cam, frames in cams.items():
            cam_cfg = cam_config.get(cam)
            bytes_today += bytes_captured_on(frames, today)
            counts = daily_counts(frames)
            health = cam_health(frames, outcomes.get(cam), now, stale_after_for(cam_cfg))
            cam_count += 1
            if health["is_stale"]:
                stale_count += 1
            cam_bytes = frame_bytes(frames)
            full_grid = heatmap_grid(counts, today)
            cam_views.append(
                {
                    "name": cam,
                    "display_name": (cam_cfg or {}).get("display_name"),
                    "key": f"{_slug(site)}--{_slug(cam)}",
                    "url": (cam_cfg or {}).get("url"),
                    "interval_minutes": (cam_cfg or {}).get("interval_minutes"),
                    "health": health,
                    "recent": recent_strip(counts, today),
                    "full_grid": full_grid,
                    "day_details": day_details_for_grid(hourly_counts(frames), full_grid),
                    "bytes": cam_bytes,
                    "avg_bytes": cam_bytes / len(frames) if frames else 0,
                    "thumb_url": thumb_url(frames, archive_dir),
                }
            )
        site_stale = sum(1 for c in cam_views if c["health"]["is_stale"])
        site_views.append({"site": site, "cams": cam_views, "stale_count": site_stale})

    disk = disk_usage(archive_dir)
    burn_rate = daily_burn_rate(bytes_today, now) if site_views else None
    runway_days = None
    if disk and burn_rate and burn_rate["projected_daily_bytes"] > 0:
        runway_days = disk["free"] / burn_rate["projected_daily_bytes"]

    return {
        "sites": site_views,
        "disk": disk,
        "burn_rate": burn_rate,
        "runway_days": runway_days,
        "stale_count": stale_count,
        "cam_count": cam_count,
    }


# --- rendering -------------------------------------------------------------

_FONT_STACK = (
    "ui-monospace, 'JetBrains Mono', 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
)

# A camera body with a clock face standing in for the lens, and a green dot
# (the same green as .cam-status.live) for the flash — reused both as the
# page's inline logo and, wrapped and data-URI-encoded, as its favicon.
_LOGO_SVG_BODY = (
    '<rect x="12" y="6" width="8" height="5" rx="1.5" fill="none" stroke="#3b82f6" '
    'stroke-width="2"/>'
    '<rect x="3" y="10" width="26" height="17" rx="3.5" fill="none" stroke="#3b82f6" '
    'stroke-width="2"/>'
    '<circle cx="16" cy="19" r="6.5" fill="none" stroke="#7dd3fc" stroke-width="2"/>'
    '<line x1="16" y1="19" x2="16" y2="15" stroke="#7dd3fc" stroke-width="1.6" '
    'stroke-linecap="round"/>'
    '<line x1="16" y1="19" x2="19" y2="19" stroke="#f5a524" stroke-width="1.3" '
    'stroke-linecap="round"/>'
    '<circle cx="16" cy="19" r="1" fill="#e8eaed"/>'
    '<circle cx="24" cy="14" r="1.6" fill="#3fb950"/>'
)


def _favicon_href():
    """Data-URI ``href`` for a ``<link rel="icon">`` using ``_LOGO_SVG_BODY``.

    Keeps the favicon inline rather than a sibling file, matching this page's
    self-contained design (see module docstring). ``safe=""`` is required so
    the color hex codes' ``#`` characters get percent-encoded rather than
    truncating the data URI at a fragment identifier.
    """
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{_LOGO_SVG_BODY}</svg>'
    return "data:image/svg+xml," + quote(svg, safe="")


_STYLE = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:#0b0d10;font-family:{_FONT_STACK};
  -webkit-font-smoothing:antialiased}}
a{{text-decoration:none}}
.wrap{{min-height:100vh;background:#0b0d10;display:flex;justify-content:center}}
.content{{width:100%;max-width:640px;padding:22px 18px 60px}}
.header{{display:flex;align-items:flex-start;justify-content:space-between;
  gap:12px;flex-wrap:wrap}}
.title-row{{display:flex;align-items:center;gap:9px}}
.logo{{width:26px;height:26px;flex:none}}
.title{{font:700 20px/1.2 {_FONT_STACK};color:#e8eaed;letter-spacing:-.02em}}
.subtitle{{font:400 11px/1.4 {_FONT_STACK};color:rgba(255,255,255,.4);margin-top:4px}}
.archive-link{{font:500 11px {_FONT_STACK};color:#7dd3fc;white-space:nowrap;margin-top:2px}}
.stale-banner{{margin-top:16px;padding:12px 14px;background:rgba(217,119,6,.12);
  border:1px solid rgba(217,119,6,.35);border-radius:10px;display:flex;
  align-items:center;gap:10px}}
.stale-dot{{width:7px;height:7px;border-radius:50%;background:#f5a524;flex:none}}
.stale-text{{font:600 12px/1.3 {_FONT_STACK};color:#f5c363}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:22px}}
.stat-card{{background:rgba(255,255,255,.04);border-radius:12px;padding:14px 16px}}
.stat-label{{font:600 10px {_FONT_STACK};color:rgba(255,255,255,.4);
  letter-spacing:.05em;text-transform:uppercase}}
.stat-value{{font:700 24px {_FONT_STACK};color:#e8eaed;margin-top:5px}}
.stat-value.warn{{color:#ff6b6b}}
.stat-bar{{height:5px;background:rgba(255,255,255,.08);border-radius:3px;
  margin-top:9px;overflow:hidden}}
.stat-bar-fill{{height:100%}}
.stat-bar-fill.bar-ok{{background:#3fb950}}
.stat-bar-fill.bar-warn{{background:#f5a524}}
.stat-bar-fill.bar-critical{{background:#ff6b6b}}
.stat-sub{{font:400 10px {_FONT_STACK};color:rgba(255,255,255,.35);
  margin-top:6px;line-height:1.5}}
.group{{margin-top:26px}}
.group-head{{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:12px}}
.group-name{{font:600 12px {_FONT_STACK};color:#e8eaed;letter-spacing:.05em;
  text-transform:uppercase}}
.group-badge{{font:500 10px {_FONT_STACK};color:#f5a524;
  background:rgba(245,165,36,.12);padding:2px 8px;border-radius:20px}}
.cams{{display:flex;flex-direction:column;gap:14px}}
.cam-card{{border:1px solid rgba(255,255,255,.07);border-radius:16px;
  overflow:hidden;background:rgba(255,255,255,.02)}}
.cam-photo{{position:relative;display:block;height:150px;
  background:repeating-linear-gradient(45deg,#1a1d22,#1a1d22 8px,#22262c 8px,#22262c 16px)}}
.cam-photo-link{{position:absolute;inset:0;display:block}}
.cam-photo img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}}
.cam-photo-label{{position:absolute;top:10px;left:12px;font:500 9px {_FONT_STACK};
  color:rgba(255,255,255,.5);letter-spacing:.05em}}
.cam-photo-gradient{{position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(to top,rgba(0,0,0,.82),transparent 60%)}}
.cam-photo-overlay{{position:absolute;left:14px;right:14px;bottom:10px;display:flex;
  align-items:flex-end;justify-content:space-between;gap:8px;pointer-events:none}}
.cam-photo-overlay a{{pointer-events:auto}}
.cam-display-name{{font:600 10.5px {_FONT_STACK};color:rgba(255,255,255,.65);
  letter-spacing:.03em;margin-bottom:1px}}
.cam-name{{font:700 16px {_FONT_STACK};color:#fff}}
.cam-last-frame{{font:400 11px {_FONT_STACK};color:rgba(255,255,255,.85);margin-top:2px}}
.cam-status{{font:600 9.5px {_FONT_STACK};padding:3px 9px;border-radius:20px;flex:none}}
.cam-status.stale{{color:#2a1a0e;background:#f5a524}}
.cam-status.live{{color:#0a2e12;background:#3fb950}}
.cam-body{{padding:12px 14px 14px}}
.cam-meta{{display:flex;gap:14px;flex-wrap:wrap;font:400 10.5px {_FONT_STACK};
  color:rgba(255,255,255,.7)}}
.cam-heatmap-row{{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;margin-top:10px;gap:6px 10px}}
.recent-strip{{display:grid;grid-template-columns:repeat({RECENT_DAYS},6px);
  gap:1px;opacity:.85;max-width:100%;overflow-x:auto}}
.recent-cell{{width:6px;height:6px;border-radius:1px;background:rgba(255,255,255,.06);
  cursor:pointer}}
.recent-cell.l1{{background:#1e3a8a}}
.recent-cell.l2{{background:#3b82f6}}
.recent-info{{min-height:1.2em;font:400 10px {_FONT_STACK};color:rgba(255,255,255,.6);
  margin-top:6px}}
.history-btn{{border:none;background:none;font:500 10.5px {_FONT_STACK};color:#7dd3fc;
  cursor:pointer;white-space:nowrap;padding:0}}
.modal-overlay{{position:fixed;inset:0;display:none;z-index:50}}
.modal-overlay:target{{display:block}}
.modal-scrim{{position:absolute;inset:0;background:rgba(0,0,0,.7)}}
.modal-sheet{{position:absolute;left:50%;bottom:0;transform:translateX(-50%);
  width:100%;max-width:640px;background:#12151a;border-radius:20px 20px 0 0;
  padding:20px 20px 26px;max-height:80vh;overflow:auto}}
.modal-head{{display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px}}
.modal-title{{font:700 15px {_FONT_STACK};color:#e8eaed}}
.modal-close{{display:inline-flex;align-items:center;justify-content:center;
  border:none;background:rgba(255,255,255,.08);color:#e8eaed;width:26px;height:26px;
  border-radius:50%;font:600 13px {_FONT_STACK};cursor:pointer}}
.month-row{{display:flex;gap:8px}}
.month-spacer{{width:28px;flex:none}}
.month-grid{{display:grid;grid-template-columns:repeat({HEATMAP_WEEKS},18px);
  gap:4px;flex:1;overflow-x:auto}}
.month-label{{font:500 10px {_FONT_STACK};color:rgba(255,255,255,.4)}}
.day-row{{display:flex;gap:8px;margin-top:5px}}
.day-labels{{display:flex;flex-direction:column;gap:4px;width:28px;flex:none}}
.day-label{{height:18px;font:400 9px {_FONT_STACK};color:rgba(255,255,255,.35);
  line-height:18px}}
.full-grid{{display:grid;grid-auto-flow:column;grid-template-rows:repeat(7,18px);
  grid-template-columns:repeat({HEATMAP_WEEKS},18px);gap:4px;flex:1;overflow-x:auto}}
.full-cell{{width:18px;height:18px;border-radius:3px;background:rgba(255,255,255,.06);
  cursor:pointer}}
.full-cell.l1{{background:#1e3a8a}}
.full-cell.l2{{background:#3b82f6}}
.full-cell.future{{background:transparent;cursor:default}}
.modal-hint{{font:400 11px {_FONT_STACK};color:rgba(255,255,255,.35);margin-top:14px}}
.day-detail{{margin-top:14px;padding-top:14px;border-top:1px solid rgba(255,255,255,.08)}}
.day-detail-title{{font:600 11px {_FONT_STACK};color:rgba(255,255,255,.6);margin-bottom:8px}}
.day-hour-grid{{display:grid;grid-template-columns:repeat(24,1fr);gap:3px}}
.hour-cell{{height:18px;border-radius:3px;background:rgba(255,255,255,.06)}}
.hour-cell.l1{{background:#1e3a8a}}
.hour-cell.l2{{background:#3b82f6}}
.day-hour-labels{{display:flex;justify-content:space-between;margin-top:5px;
  font:400 9px {_FONT_STACK};color:rgba(255,255,255,.3)}}
.footer{{margin-top:28px;padding-top:16px;border-top:1px solid rgba(255,255,255,.06);
  font:400 12px {_FONT_STACK};color:rgba(255,255,255,.65);text-align:center;line-height:1.7}}
"""

_WEEKDAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}  # row index (Sunday-first) -> label


def _month_row_html(grid):
    """Month labels spanning the weeks they cover, aligned above the full-history grid."""
    spans = []  # (label, start_week_index, end_week_index_inclusive)
    for i, week in enumerate(grid):
        month_key = week[0]["date"].strftime("%b")
        if spans and spans[-1][0] == month_key and spans[-1][2] == i - 1:
            spans[-1] = (month_key, spans[-1][1], i)
        else:
            spans.append((month_key, i, i))
    cells = [
        f'<div class="month-label" style="grid-column:{start + 1} / {end + 2}">{label}</div>'
        for label, start, end in spans
    ]
    return (
        '<div class="month-row"><div class="month-spacer"></div>'
        f'<div class="month-grid">{"".join(cells)}</div></div>'
    )


def _day_labels_html():
    rows = [f'<div class="day-label">{_WEEKDAY_LABELS.get(r, "")}</div>' for r in range(7)]
    return '<div class="day-labels">' + "".join(rows) + "</div>"


def _day_title(count, day):
    """'N images on YYYY-MM-DD' — count leads, date follows, for a heatmap cell tooltip."""
    return f'{count} image{"s" if count != 1 else ""} on {day.isoformat()}'


def _hour_title(count, hour, day):
    """'N images at HH:00 on YYYY-MM-DD' — an hourly cell's tooltip text."""
    return f'{count} image{"s" if count != 1 else ""} at {hour:02d}:00 on {day.isoformat()}'


def _day_detail_html(key, day, hour_cells):
    """Render one day's hidden hourly drill-down (24 cells), toggled visible
    by the matching full-history cell's onclick — see ``_full_grid_html``.
    """
    detail_id = f"day-detail-{key}-{day.isoformat()}"
    cells = "".join(
        f'<div class="hour-cell l{c["level"]}" '
        f'title="{html.escape(_hour_title(c["count"], c["hour"], day))}"></div>'
        for c in hour_cells
    )
    return (
        f'<div class="day-detail" id="{detail_id}" style="display:none">'
        f'<div class="day-detail-title">{html.escape(day.isoformat())} &middot; hourly</div>'
        f'<div class="day-hour-grid">{cells}</div>'
        '<div class="day-hour-labels"><span>12a</span><span>6a</span>'
        "<span>12p</span><span>6p</span></div></div>"
    )


def _full_grid_html(cam):
    """Render the 13-week x 7-day cells in column-major order (matches
    ``grid-auto-flow: column``): one week's 7 days, then the next week's.

    Each cell's ``title`` shows on hover; since touch screens have no way to
    trigger a hover, an ``onclick`` also copies that same text into the
    modal's ``.modal-hint`` line so a tap reveals it the same way. For a day
    with frames (an entry in ``cam["day_details"]``), the same click also
    reveals that day's hidden hourly sub-grid (see ``_day_detail_html``),
    hiding whichever day was previously shown.
    """
    key = cam["key"]
    day_details = cam.get("day_details", {})
    cells = []
    for week in cam["full_grid"]:
        for cell in week:
            if cell["future"]:
                cells.append('<div class="full-cell future"></div>')
                continue
            day = cell["date"]
            title = html.escape(_day_title(cell["count"], day))
            onclick = (
                "this.closest('.modal-sheet').querySelector('.modal-hint').textContent=this.title;"
                "this.closest('.modal-sheet').querySelectorAll('.day-detail')"
                ".forEach(function(d){d.style.display='none'})"
            )
            if day in day_details:
                detail_id = f"day-detail-{key}-{day.isoformat()}"
                onclick += f";document.getElementById('{detail_id}').style.display='block'"
            cells.append(
                f'<div class="full-cell l{cell["level"]}" title="{title}" '
                f'onclick="{onclick}"></div>'
            )
    return '<div class="full-grid">' + "".join(cells) + "</div>"


def _recent_strip_html(cells):
    """Render the 31-day recent-activity strip.

    Each cell's ``title`` shows the day's count on hover; an ``onclick``
    mirrors the full-history grid's tap-to-reveal behavior into the
    card's ``.recent-info`` line for touch screens.
    """
    divs = "".join(
        f'<div class="recent-cell l{c["level"]}" '
        f'title="{html.escape(_day_title(c["count"], c["date"]))}" '
        "onclick=\"this.closest('.cam-body').querySelector('.recent-info')"
        '.textContent=this.title"></div>'
        for c in cells
    )
    return f'<div class="recent-strip">{divs}</div>'


def _cam_card_html(cam, now):
    key = cam["key"]
    health = cam["health"]
    last = health["last_time"]

    name_html = html.escape(cam["name"])
    if cam.get("url"):
        cam_url = html.escape(cam["url"])
        name_cell = (
            f'<a class="cam-name" href="{cam_url}" target="_blank" rel="noopener">{name_html}</a>'
        )
    else:
        name_cell = f'<div class="cam-name">{name_html}</div>'
    display_name_cell = (
        f'<div class="cam-display-name">{html.escape(cam["display_name"])}</div>'
        if cam.get("display_name")
        else ""
    )

    interval = cam.get("interval_minutes")
    interval_suffix = f" &middot; every {interval} min" if interval else ""
    if last:
        ago = html.escape(_human_ago(now - last))
        last_frame = (
            f'{html.escape(last.strftime("%Y-%m-%d %H:%M"))} &middot; {ago}{interval_suffix}'
        )
    else:
        last_frame = f"no frames yet{interval_suffix}"
    status_cls, status_text = ("stale", "STALE") if health["is_stale"] else ("live", "LIVE")

    if cam.get("thumb_url"):
        thumb = html.escape(cam["thumb_url"])
        img = f'<img src="{thumb}" alt="Latest frame from {name_html}" loading="lazy">'
        # The anchor covers the whole .cam-photo box (not just the image) so the
        # entire thumbnail is clickable, not only the pixels under the img. The
        # cam-name link below lives in a sibling (not descendant) element, so
        # there's no invalid nested <a> — that previously made browsers silently
        # split/reflow the markup, throwing off the name's left alignment.
        photo_inner = (
            f'<a href="{thumb}" class="cam-photo-link" target="_blank" rel="noopener" '
            f'aria-label="Full size frame from {name_html}">{img}</a>'
        )
    else:
        photo_inner = '<div class="cam-photo-label">CAMERA PHOTO</div>'
    photo = (
        f'<div class="cam-photo">{photo_inner}'
        '<div class="cam-photo-gradient"></div>'
        '<div class="cam-photo-overlay">'
        f'<div>{display_name_cell}{name_cell}<div class="cam-last-frame">{last_frame}</div></div>'
        f'<span class="cam-status {status_cls}">{status_text}</span>'
        "</div></div>"
    )

    meta = (
        '<div class="cam-meta">'
        f'<span>{html.escape(_human_bytes(cam["avg_bytes"]))} avg</span>'
        f'<span>{health["frame_count"]} frames</span>'
        f'<span>{html.escape(_human_bytes(cam["bytes"]))} disk</span>'
        "</div>"
    )
    heatmap_row = (
        '<div class="cam-heatmap-row">'
        f'{_recent_strip_html(cam["recent"])}'
        f'<a class="history-btn" href="#history-{key}">full history &rarr;</a>'
        "</div>"
    )
    recent_info = '<div class="recent-info">Tap a day for details</div>'
    return (
        f'<div class="cam-card">{photo}'
        f'<div class="cam-body">{meta}{heatmap_row}{recent_info}</div></div>'
    )


def _history_modal_html(cam):
    key = cam["key"]
    day_details = "".join(
        _day_detail_html(key, day, cells)
        for day, cells in sorted(cam.get("day_details", {}).items())
    )
    return (
        f'<div class="modal-overlay" id="history-{key}">'
        f'<a class="modal-scrim" href="#!" aria-label="Close"></a>'
        '<div class="modal-sheet">'
        '<div class="modal-head">'
        f'<div class="modal-title">{html.escape(cam["name"])} &middot; full history</div>'
        '<a class="modal-close" href="#!" aria-label="Close">&times;</a>'
        "</div>"
        f'{_month_row_html(cam["full_grid"])}'
        f'<div class="day-row">{_day_labels_html()}{_full_grid_html(cam)}</div>'
        '<div class="modal-hint">Tap a day for details</div>'
        f"{day_details}"
        "</div></div>"
    )


def _footer_html(system):
    """Render the footer: one host stat per line (uptime, longest recorded
    uptime streak, memory, load average, page generation time), then a
    deployment marker (commit sha8 and date) on its own line after.

    Each stat/marker is included only if its underlying read succeeded (see
    ``system_stats`` / ``read_git_info``), so the footer degrades gracefully
    rather than showing a fabricated value — e.g. on a dev machine with no
    /proc/meminfo, or a non-git deployment.
    """
    lines = []
    if system.get("uptime_seconds") is not None:
        lines.append(f'Uptime {html.escape(_human_uptime(system["uptime_seconds"]))}')
    max_uptime = system.get("max_uptime")
    if max_uptime:
        start = datetime.fromisoformat(max_uptime["start"]).strftime("%Y-%m-%d")
        end = datetime.fromisoformat(max_uptime["end"]).strftime("%Y-%m-%d")
        lines.append(
            f'Longest uptime {html.escape(_human_uptime(max_uptime["uptime_seconds"]))} '
            f"&middot; {html.escape(start)} &rarr; {html.escape(end)}"
        )
    memory = system.get("memory")
    if memory:
        used_kb = memory["total_kb"] - memory["available_kb"]
        pct = used_kb / memory["total_kb"] * 100 if memory["total_kb"] else 0
        lines.append(
            f"Mem {html.escape(_human_bytes(used_kb * 1024))} / "
            f'{html.escape(_human_bytes(memory["total_kb"] * 1024))} ({pct:.0f}%)'
        )
    load_avg = system.get("load_avg")
    if load_avg:
        l1, l5, l15 = load_avg
        lines.append(f"Load {l1:.2f}, {l5:.2f}, {l15:.2f}")
    if system.get("generate_seconds") is not None:
        lines.append(f'Generated in {html.escape(_human_duration(system["generate_seconds"]))}')

    git = system.get("git")
    if git:
        lines.append(
            f'Deployed {html.escape(git["sha8"])} &middot; {html.escape(git["commit_date"])}'
        )
    if not lines:
        return ""
    return f'<div class="footer">{"<br>".join(lines)}</div>'


def render_html(page_data, now, show_stale_banner=False, system=None):
    sites = page_data["sites"]
    disk = page_data["disk"]
    burn = page_data.get("burn_rate")
    runway_days = page_data.get("runway_days")

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="refresh" content="900">',  # reload every 15 min
        "<title>Capture Status</title>",
        f'<link rel="icon" type="image/svg+xml" href="{_favicon_href()}">',
        f"<style>{_STYLE}</style></head><body>",
        '<div class="wrap"><div class="content">',
        '<div class="header"><div>'
        '<div class="title-row">'
        f'<svg class="logo" viewBox="0 0 32 32" aria-hidden="true">{_LOGO_SVG_BODY}</svg>'
        '<div class="title">Capture Status</div></div>'
        f'<div class="subtitle">Generated {html.escape(now.strftime("%Y-%m-%d %H:%M"))} '
        f'&middot; {html.escape(now.strftime("%Z"))}</div></div>'
        '<a class="archive-link" href="archive/">browse full archive &rarr;</a></div>',
    ]

    if show_stale_banner and page_data["stale_count"] > 0:
        parts.append(
            '<div class="stale-banner"><div class="stale-dot"></div>'
            f'<div class="stale-text">{page_data["stale_count"]} of {page_data["cam_count"]} '
            "cams stale &mdash; no fresh frames</div></div>"
        )

    if disk:
        used_pct = disk["used"] / disk["total"] * 100 if disk["total"] else 0
        free_pct = disk["free"] / disk["total"] * 100 if disk["total"] else 100
        if free_pct <= DISK_FREE_CRITICAL_PCT:
            bar_cls = " bar-critical"
        elif free_pct <= DISK_FREE_WARN_PCT:
            bar_cls = " bar-warn"
        else:
            bar_cls = " bar-ok"
        stats = [
            '<div class="stat-card"><div class="stat-label">Disk free</div>'
            f'<div class="stat-value">{html.escape(_human_bytes(disk["free"]))}</div>'
            '<div class="stat-bar">'
            f'<div class="stat-bar-fill{bar_cls}" style="width:{used_pct:.1f}%"></div></div>'
            f'<div class="stat-sub">of {html.escape(_human_bytes(disk["total"]))} '
            f'&middot; {html.escape(_human_bytes(disk["used"]))} used '
            f"&middot; {free_pct:.0f}% free</div></div>"
        ]
        warn_cls = " warn" if runway_days is not None and runway_days <= RUNWAY_WARN_DAYS else ""
        if burn:
            hours = burn["elapsed_hours"]
            sub = (
                f'{html.escape(_human_bytes(burn["projected_daily_bytes"]))}/day burn &middot; '
                f'{html.escape(_human_bytes(burn["bytes_today"]))} captured in {hours:.1f} '
                f'hr{"" if hours == 1 else "s"} today'
            )
        else:
            sub = "not enough data yet to estimate burn rate"
        stats.append(
            '<div class="stat-card"><div class="stat-label">Runway</div>'
            f'<div class="stat-value{warn_cls}">{html.escape(_human_runway(runway_days))}</div>'
            f'<div class="stat-sub">{sub}</div></div>'
        )
        parts.append(f'<div class="stats">{"".join(stats)}</div>')

    if not sites:
        parts.append('<p class="subtitle" style="margin-top:22px">No frames archived yet.</p>')

    modals = []
    for site in sites:
        parts.append('<div class="group"><div class="group-head">')
        parts.append(f'<div class="group-name">{html.escape(site["site"])}</div>')
        if site["stale_count"] > 0:
            parts.append(f'<span class="group-badge">{site["stale_count"]} stale</span>')
        parts.append('</div><div class="cams">')
        for cam in site["cams"]:
            parts.append(_cam_card_html(cam, now))
            modals.append(_history_modal_html(cam))
        parts.append("</div></div>")

    if system:
        parts.append(_footer_html(system))

    parts.append("</div></div>")  # .content, .wrap
    parts.extend(modals)
    parts.append("</body></html>")
    return "\n".join(parts)


def parse_args():
    parser = argparse.ArgumentParser(description="Generate the static status page.")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Capture config YAML (for archive_dir / capture_log) (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output HTML path (default: config's web_output, else {DEFAULT_OUTPUT})",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = yaml.safe_load(args.config.read_text())
    archive_dir = config["archive_dir"]
    log_path = config.get("capture_log")
    output = args.output or Path(config.get("web_output", DEFAULT_OUTPUT))
    show_stale_banner = config.get("web_show_stale_banner", False)

    now = datetime.now(PACIFIC)
    generate_start = time.perf_counter()
    page_data = build_page_data(
        archive_dir,
        log_path,
        now,
        cam_config=config.get("cams"),
        site_order=config.get("site_order"),
    )
    system = system_stats()
    system["git"] = read_git_info()
    max_uptime_path = config.get("max_uptime_log")
    system["max_uptime"] = update_max_uptime_record(max_uptime_path, system["uptime_seconds"], now)
    # Covers the archive scan, capture log, /proc reads, and git subprocess —
    # the I/O-bound work — not render_html's pure-string HTML assembly below.
    system["generate_seconds"] = time.perf_counter() - generate_start
    html_doc = render_html(page_data, now, show_stale_banner=show_stale_banner, system=system)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc)
    ensure_archive_link(output.parent, archive_dir)
    print(f"wrote {output} ({len(page_data['sites'])} site(s))")


if __name__ == "__main__":
    main()
