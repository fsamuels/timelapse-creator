"""Static status-page generator for timelapse-creator.

Reads the frame archive (via the timestamped filenames) and the persisted capture
log, then writes a single self-contained HTML page: a disk/burn-rate summary up
top, then per-cam cards grouped by site showing the last frame, a recent-activity
strip, and a link to that cam's full multi-month history (a GitHub-style
contribution grid, opened as a bottom-sheet "modal" implemented purely with CSS
``:target`` — no ``<script>`` tag, keeping the page dependency-free).

Also symlinks the raw archive in next to the page (see ``ensure_archive_link``)
so it's directly browsable, and reports per-cam and total disk usage.

Designed to run on the Pi after each capture (see deploy/pi/), regenerating the
page — no persistent app server. It reads ``archive_dir`` from the config, so it
naturally shows exactly the frames in that directory (on the Pi: Pi-captured
frames) with no source-era filtering logic of its own.
"""

import argparse
import html
import math
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from capture.archive import PACIFIC, parse_frame_time
from capture.capture_log import latest_outcomes, read_capture_log

CONFIG_PATH = Path(__file__).parent.parent / "capture" / "config.yaml"
DEFAULT_OUTPUT = "site/index.html"
HEATMAP_WEEKS = 13  # ~a quarter, the full-history window shown per cam
RECENT_DAYS = 31  # the compact recent-activity strip on each cam card
HEATMAP_LEVELS = 2  # off / low / high — see _level()
STALE_MULTIPLIER = 2  # flag a cam stale after this many missed capture intervals
RUNWAY_WARN_DAYS = 14  # runway at or below this is flagged in warning red


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
    with ``url`` and ``interval_minutes``), used to link each cam's name to
    its live image and to size its stale threshold.

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
            cam_views.append(
                {
                    "name": cam,
                    "key": f"{_slug(site)}--{_slug(cam)}",
                    "url": (cam_cfg or {}).get("url"),
                    "health": health,
                    "recent": recent_strip(counts, today),
                    "full_grid": heatmap_grid(counts, today),
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

_STYLE = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:#0b0d10;font-family:{_FONT_STACK};
  -webkit-font-smoothing:antialiased}}
a{{text-decoration:none}}
.wrap{{min-height:100vh;background:#0b0d10;display:flex;justify-content:center}}
.content{{width:100%;max-width:640px;padding:22px 18px 60px}}
.header{{display:flex;align-items:flex-start;justify-content:space-between;
  gap:12px;flex-wrap:wrap}}
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
.stat-bar-fill{{height:100%;background:#f5a524}}
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
  color:rgba(255,255,255,.25);letter-spacing:.05em}}
.cam-photo-gradient{{position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(to top,rgba(0,0,0,.82),transparent 60%)}}
.cam-photo-overlay{{position:absolute;left:14px;right:14px;bottom:10px;display:flex;
  align-items:flex-end;justify-content:space-between;gap:8px;pointer-events:none}}
.cam-photo-overlay a{{pointer-events:auto}}
.cam-name{{font:700 16px {_FONT_STACK};color:#fff}}
.cam-last-frame{{font:400 11px {_FONT_STACK};color:rgba(255,255,255,.65);margin-top:2px}}
.cam-status{{font:600 9.5px {_FONT_STACK};padding:3px 9px;border-radius:20px;flex:none}}
.cam-status.stale{{color:#2a1a0e;background:#f5a524}}
.cam-status.live{{color:#0a2e12;background:#3fb950}}
.cam-body{{padding:12px 14px 14px}}
.cam-meta{{display:flex;gap:14px;flex-wrap:wrap;font:400 10.5px {_FONT_STACK};
  color:rgba(255,255,255,.45)}}
.cam-heatmap-row{{display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;margin-top:10px;gap:6px 10px}}
.recent-strip{{display:grid;grid-template-columns:repeat({RECENT_DAYS},6px);
  gap:1px;opacity:.85;max-width:100%;overflow-x:auto}}
.recent-cell{{width:6px;height:6px;border-radius:1px;background:rgba(255,255,255,.06);
  cursor:pointer}}
.recent-cell.l1{{background:#1e3a8a}}
.recent-cell.l2{{background:#3b82f6}}
.recent-info{{min-height:1.2em;font:400 10px {_FONT_STACK};color:rgba(255,255,255,.35);
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


def _full_grid_html(grid):
    """Render the 13-week x 7-day cells in column-major order (matches
    ``grid-auto-flow: column``): one week's 7 days, then the next week's.

    Each cell's ``title`` shows on hover; since touch screens have no way to
    trigger a hover, an ``onclick`` also copies that same text into the
    modal's ``.modal-hint`` line so a tap reveals it the same way.
    """
    cells = []
    for week in grid:
        for cell in week:
            if cell["future"]:
                cells.append('<div class="full-cell future"></div>')
            else:
                title = html.escape(_day_title(cell["count"], cell["date"]))
                cells.append(
                    f'<div class="full-cell l{cell["level"]}" title="{title}" '
                    "onclick=\"this.closest('.modal-sheet').querySelector('.modal-hint')"
                    '.textContent=this.title"></div>'
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

    if last:
        ago = html.escape(_human_ago(now - last))
        last_frame = f'{html.escape(last.strftime("%Y-%m-%d %H:%M"))} &middot; {ago}'
    else:
        last_frame = "no frames yet"
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
        f'<div>{name_cell}<div class="cam-last-frame">{last_frame}</div></div>'
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
    return (
        f'<div class="modal-overlay" id="history-{key}">'
        f'<a class="modal-scrim" href="#!" aria-label="Close"></a>'
        '<div class="modal-sheet">'
        '<div class="modal-head">'
        f'<div class="modal-title">{html.escape(cam["name"])} &middot; full history</div>'
        '<a class="modal-close" href="#!" aria-label="Close">&times;</a>'
        "</div>"
        f'{_month_row_html(cam["full_grid"])}'
        f'<div class="day-row">{_day_labels_html()}{_full_grid_html(cam["full_grid"])}</div>'
        '<div class="modal-hint">Tap a day for details</div>'
        "</div></div>"
    )


def render_html(page_data, now, show_stale_banner=False):
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
        f"<style>{_STYLE}</style></head><body>",
        '<div class="wrap"><div class="content">',
        '<div class="header"><div>'
        '<div class="title">timelapse-creator</div>'
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
        stats = [
            '<div class="stat-card"><div class="stat-label">Disk free</div>'
            f'<div class="stat-value">{html.escape(_human_bytes(disk["free"]))}</div>'
            '<div class="stat-bar">'
            f'<div class="stat-bar-fill" style="width:{used_pct:.1f}%"></div></div>'
            f'<div class="stat-sub">of {html.escape(_human_bytes(disk["total"]))} '
            f'&middot; {html.escape(_human_bytes(disk["used"]))} used</div></div>'
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
    page_data = build_page_data(
        archive_dir,
        log_path,
        now,
        cam_config=config.get("cams"),
        site_order=config.get("site_order"),
    )
    html_doc = render_html(page_data, now, show_stale_banner=show_stale_banner)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc)
    ensure_archive_link(output.parent, archive_dir)
    print(f"wrote {output} ({len(page_data['sites'])} site(s))")


if __name__ == "__main__":
    main()
