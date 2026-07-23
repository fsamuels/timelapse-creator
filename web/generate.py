"""Static status-page generator for timelapse-creator.

Reads the frame archive (via the timestamped filenames) and the persisted capture
log, then writes a single self-contained HTML page with two views:

  * Health/status — last frame per cam, how long ago, and the last capture-run
    outcome, so you can tell at a glance whether capture is still working. A cam
    down for the night looks the same as a broken one from frames alone, which is
    why this reads the capture log rather than only the archive.
  * Activity heatmap — a GitHub-style contribution grid of frames captured per
    day, per cam.

Also symlinks the raw archive in next to the page (see ``ensure_archive_link``)
so it's directly browsable, and reports per-cam and total disk usage.

Designed to run on the Pi after each capture (see deploy/pi/), regenerating the
page — no persistent app server. It reads ``archive_dir`` from the config, so it
naturally shows exactly the frames in that directory (on the Pi: Pi-captured
frames) with no source-era filtering logic of its own.
"""

import argparse
import html
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from capture.archive import PACIFIC, parse_frame_time
from capture.capture_log import latest_outcomes, read_capture_log

CONFIG_PATH = Path(__file__).parent.parent / "capture" / "config.yaml"
DEFAULT_OUTPUT = "site/index.html"
HEATMAP_WEEKS = 13  # ~a quarter, the recent-activity window shown per cam
STALE_MULTIPLIER = 2  # flag a cam stale after this many missed capture intervals


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


def heatmap_grid(counts, end_date, weeks=HEATMAP_WEEKS):
    """Build a GitHub-style grid: a list of weeks, each a list of 7 day cells.

    Weeks run Sunday-first and oldest-first; the last column contains end_date.
    Each cell is ``{"date", "count", "level", "future"}`` where level is a 0-4
    intensity bucket relative to the busiest day in the window and future cells
    (dates after end_date, padding out the final week) are marked so the page
    can render them blank.
    """
    days_since_sunday = (end_date.weekday() + 1) % 7
    last_week_start = end_date - timedelta(days=days_since_sunday)
    peak = max(counts.values(), default=0)

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
                    "level": _level(count, peak),
                    "future": day > end_date,
                }
            )
        grid.append(week)
    return grid


def _level(count, peak):
    if count == 0 or peak == 0:
        return 0
    return min(4, 1 + int(3 * (count - 1) / peak))


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


def build_page_data(archive_dir, log_path, now, cam_config=None):
    """Gather everything the template needs from the archive and the log.

    ``cam_config`` is the capture config's ``cams`` mapping (cam name -> dict
    with ``url`` and ``interval_minutes``), used to link each cam's name to
    its live image and to size its stale threshold.

    Returns ``{"sites": [...], "disk": {"total", "used", "free"} or None}``.
    """
    archive_dir = Path(archive_dir)
    sites = scan_archive(archive_dir)
    outcomes = latest_outcomes(read_capture_log(log_path))
    cam_config = cam_config or {}
    today = now.date()

    site_views = []
    for site, cams in sites.items():
        cam_views = []
        for cam, frames in cams.items():
            cam_cfg = cam_config.get(cam)
            cam_views.append(
                {
                    "name": cam,
                    "url": (cam_cfg or {}).get("url"),
                    "health": cam_health(frames, outcomes.get(cam), now, stale_after_for(cam_cfg)),
                    "grid": heatmap_grid(daily_counts(frames), today),
                    "bytes": frame_bytes(frames),
                    "thumb_url": thumb_url(frames, archive_dir),
                }
            )
        site_views.append({"site": site, "cams": cam_views})
    return {"sites": site_views, "disk": disk_usage(archive_dir)}


# --- rendering -------------------------------------------------------------

_LIGHT_VARS = """
    --bg: #ffffff; --fg: #1f2328; --muted: #656d76; --card: #f6f8fa;
    --border: #d0d7de; --ok: #1a7f37; --stale: #9a6700;
    --l0:#ebedf0; --l1:#bcd7ff; --l2:#7fb0f5; --l3:#3f7fd6; --l4:#1b52a0;
"""

_STYLE = f"""
:root {{
  --bg: #0d1117; --fg: #e6edf3; --muted: #8b949e; --card: #161b22;
  --border: #30363d; --ok: #3fb950; --stale: #d29922;
  --l0:#161b22; --l1:#0b2c5c; --l2:#15468a; --l3:#2b6cb8; --l4:#4c9aff;
}}
:root[data-theme="light"] {{
{_LIGHT_VARS}}}
@media (prefers-color-scheme: light) {{
  :root[data-theme="system"] {{
{_LIGHT_VARS}  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 2rem 1.5rem; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
main {{ max-width: 900px; margin: 0 auto; }}
.top-row {{ display: flex; align-items: baseline; justify-content: space-between;
  gap: 1rem; flex-wrap: wrap; }}
h1 {{ font-size: 1.5rem; margin: 0 0 .25rem; }}
h2 {{ font-size: 1.05rem; margin: 2rem 0 .75rem; }}
.sub {{ color: var(--muted); margin: 0 0 1.5rem; font-size: .9rem; }}
.theme-select {{ color: var(--muted); font-size: .85rem; }}
.theme-select select {{ font: inherit; color: var(--fg); background: var(--card);
  border: 1px solid var(--border); border-radius: .35rem; padding: .15rem .4rem;
  margin-left: .35rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; font-size: .8rem; text-transform: uppercase;
  letter-spacing: .03em; }}
.badge {{ display: inline-block; padding: .1rem .5rem; border-radius: 2rem; font-size: .8rem;
  font-weight: 600; }}
.badge.ok {{ color: var(--ok); background: color-mix(in srgb, var(--ok) 15%, transparent); }}
.badge.stale {{ color: var(--stale);
  background: color-mix(in srgb, var(--stale) 18%, transparent); }}
.muted {{ color: var(--muted); }}
.cam-block {{ margin: 1.25rem 0; }}
.cam-name {{ font-weight: 600; margin-bottom: .4rem; }}
.cam-row {{ display: flex; align-items: center; gap: .75rem; flex-wrap: wrap; }}
.cam-thumb {{ height: 108px; width: auto; max-width: 160px; object-fit: cover;
  border-radius: .35rem; border: 1px solid var(--border); background: var(--card);
  flex: 0 0 auto; }}
.heatmap {{ overflow-x: auto; padding-bottom: .25rem; min-width: 0; }}
.hm-grid {{ display: inline-grid;
  grid-template-columns: 28px repeat({HEATMAP_WEEKS}, 11px);
  grid-auto-rows: 11px; gap: 3px; align-items: center; }}
.hm-corner {{ width: 28px; height: 11px; }}
.hm-month {{ font-size: .7rem; line-height: 11px; color: var(--muted);
  white-space: nowrap; overflow: visible; }}
.hm-wd {{ font-size: .7rem; line-height: 11px; color: var(--muted);
  text-align: right; padding-right: 4px; white-space: nowrap; }}
.day {{ width: 11px; height: 11px; border-radius: 2px; background: var(--l0); }}
.day.future {{ background: transparent; }}
.day.l1 {{ background: var(--l1); }} .day.l2 {{ background: var(--l2); }}
.day.l3 {{ background: var(--l3); }} .day.l4 {{ background: var(--l4); }}
.legend {{ display: flex; align-items: center; gap: 4px; color: var(--muted);
  font-size: .78rem; margin-top: .5rem; }}
.legend .day {{ display: inline-block; }}
footer {{ color: var(--muted); font-size: .8rem; margin-top: 2.5rem;
  border-top: 1px solid var(--border); padding-top: 1rem; }}
"""


def _status_row(cam_name, url, health, cam_bytes, now):
    if url:
        name_cell = (
            f'<a href="{html.escape(url)}" target="_blank" rel="noopener">'
            f"{html.escape(cam_name)}</a>"
        )
    else:
        name_cell = html.escape(cam_name)
    last = health["last_time"]
    if last is None:
        last_cell = '<span class="muted">no frames yet</span>'
    else:
        last_cell = (
            f"{html.escape(last.strftime('%Y-%m-%d %H:%M'))} "
            f'<span class="muted">({html.escape(_human_ago(now - last))})</span>'
        )
    badge = (
        '<span class="badge stale">stale</span>'
        if health["is_stale"]
        else '<span class="badge ok">live</span>'
    )
    outcome = health["outcome"]
    if outcome:
        outcome_name = outcome.get("outcome", "")
        run_cell = html.escape(outcome_name)
        detail = outcome.get("detail")
        if detail:
            if outcome_name == "saved":
                detail = Path(str(detail)).name  # just the filename, not the full save path
            run_cell += f' <span class="muted">{html.escape(str(detail))}</span>'
    else:
        run_cell = '<span class="muted">—</span>'
    return (
        f"<tr><td>{name_cell}</td><td>{badge}</td>"
        f"<td>{last_cell}</td><td>{health['frame_count']}</td>"
        f"<td>{html.escape(_human_bytes(cam_bytes))}</td><td>{run_cell}</td></tr>"
    )


_WEEKDAY_LABELS = {1: "Mon", 3: "Wed", 5: "Fri"}  # row index (Sunday-first) -> label


def _heatmap_html(grid):
    """Render a GitHub-style grid with month labels on top and weekday labels on the left.

    ``grid`` is a list of week-columns (oldest -> newest), each a list of 7 day
    cells ordered Sunday..Saturday (see heatmap_grid). Labels are placed via a
    single CSS grid so they line up with the 11px cells / 3px gaps.
    """
    cells = ['<div class="hm-corner"></div>']

    prev_month = None
    last_label_col = -3
    for i, week in enumerate(grid):
        first_date = week[0]["date"]
        month_key = (first_date.year, first_date.month)
        if month_key != prev_month and (i - last_label_col) >= 3:
            label = first_date.strftime("%b")
            last_label_col = i
        else:
            label = ""
        prev_month = month_key
        cells.append(f'<div class="hm-month">{label}</div>')

    for row in range(7):
        cells.append(f'<div class="hm-wd">{_WEEKDAY_LABELS.get(row, "")}</div>')
        for week in grid:
            cell = week[row]
            if cell["future"]:
                cls = "day future"
                title = ""
            else:
                n = cell["count"]
                title = f' title="{n} image{"s" if n != 1 else ""} on {cell["date"].isoformat()}"'
                cls = f"day l{cell['level']}"
            cells.append(f'<div class="{cls}"{title}></div>')

    return '<div class="heatmap"><div class="hm-grid">' + "".join(cells) + "</div></div>"


def render_html(page_data, now):
    sites = page_data["sites"]
    disk = page_data["disk"]
    parts = [
        "<!doctype html>",
        '<html lang="en" data-theme="dark"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta http-equiv="refresh" content="900">',  # reload every 15 min
        "<title>timelapse-creator status</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        '<div class="top-row"><h1>timelapse-creator status</h1>'
        '<label class="theme-select">Theme '
        "<select onchange=\"document.documentElement.setAttribute('data-theme', this.value)\">"
        '<option value="dark" selected>Dark</option>'
        '<option value="light">Light</option>'
        '<option value="system">System</option>'
        "</select></label></div>",
        f'<p class="sub">Generated {html.escape(now.strftime("%Y-%m-%d %H:%M %Z"))} · '
        f'"stale" = no new frame in over {STALE_MULTIPLIER}× a cam\'s normal interval · '
        '<a href="archive/">browse the full archive</a></p>',
    ]
    if disk:
        parts.append(
            f'<p class="sub">Disk: {html.escape(_human_bytes(disk["free"]))} free of '
            f'{html.escape(_human_bytes(disk["total"]))} '
            f'({html.escape(_human_bytes(disk["used"]))} used)</p>'
        )

    if not sites:
        parts.append('<p class="muted">No frames archived yet.</p>')

    for site in sites:
        parts.append(f"<h2>{html.escape(site['site'])}</h2>")
        parts.append(
            "<table><thead><tr><th>Cam</th><th>Status</th><th>Last frame</th>"
            "<th>Frames</th><th>Disk</th><th>Last run</th></tr></thead><tbody>"
        )
        for cam in site["cams"]:
            parts.append(_status_row(cam["name"], cam.get("url"), cam["health"], cam["bytes"], now))
        parts.append("</tbody></table>")
        for cam in site["cams"]:
            parts.append('<div class="cam-block">')
            parts.append(f'<div class="cam-name">{html.escape(cam["name"])}</div>')
            parts.append('<div class="cam-row">')
            parts.append(_heatmap_html(cam["grid"]))
            if cam.get("thumb_url"):
                parts.append(
                    f'<img class="cam-thumb" src="{html.escape(cam["thumb_url"])}" '
                    f'alt="Latest frame from {html.escape(cam["name"])}" loading="lazy">'
                )
            parts.append("</div>")
            parts.append("</div>")

    parts.append(_legend_html())
    parts.append(
        f"<footer>Static page, regenerated after each capture run · "
        f"{html.escape(now.isoformat())}</footer>"
    )
    parts.append("</main></body></html>")
    return "\n".join(parts)


def _legend_html():
    cells = '<div class="day"></div>' + "".join(
        f'<div class="day l{lvl}"></div>' for lvl in range(1, 5)
    )
    return f'<div class="legend"><span>Less</span>{cells}<span>More</span></div>'


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

    now = datetime.now(PACIFIC)
    page_data = build_page_data(archive_dir, log_path, now, cam_config=config.get("cams"))
    html_doc = render_html(page_data, now)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc)
    ensure_archive_link(output.parent, archive_dir)
    print(f"wrote {output} ({len(page_data['sites'])} site(s))")


if __name__ == "__main__":
    main()
