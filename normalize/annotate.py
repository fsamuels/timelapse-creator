"""Local web tool for reviewing every photo in a sequence: clicking control
points onto ones worth using as a manually-verified anchor, or rejecting
ones that don't belong in the sequence at all.

Serves a page (meant to be opened in your own browser, not driven by
Claude) with the reference photo and one other photo side by side, plus
Prev/Next paging through every photo in the input directory (in capture-time
order) so a full triage pass is possible, not just a handful of anchors.
Click the same physical point on both photos (e.g. a barn corner) to record
a correspondence -- at least 4 pairs turns the current photo into an anchor.
Reject instead for a photo that's a bad angle/zoom or doesn't belong at all.
Saves straight to the control points file on disk (normalize/control_points.py)
as you go -- nothing to download or copy/paste, and you can stop and resume
anytime.

    python -m normalize.annotate path/to/drone-photos control_points/rcr-front-pastures.json \\
        --reference path/to/drone-photos/some_photo.jpg

Everything runs on localhost; no image data leaves this machine.
"""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from normalize import control_points as cp
from normalize.align import list_images

PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Control point annotator</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 1.5rem; background: #111; color: #eee; }
  h1 { font-size: 1.1rem; }
  #nav { margin-bottom: 1rem; display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  #nav select { max-width: 32rem; background: #222; color: #eee; border: 1px solid #555; }
  #nav button { padding: 0.3rem 0.7rem; cursor: pointer; }
  #counter { color: #9ca3af; }
  #status-badge {
    padding: 0.15rem 0.5rem; border-radius: 4px; border: 1px solid #555; font-size: 0.85rem;
  }
  #status-badge.done { border-color: #22c55e; color: #4ade80; }
  #status-badge.rejected { border-color: #ef4444; color: #f87171; }
  .panes { display: flex; gap: 1rem; }
  .pane { position: relative; flex: 1; min-width: 0; }
  .pane img { max-width: 100%; display: block; cursor: crosshair; user-select: none; }
  .pane .label { font-size: 0.85rem; margin-bottom: 0.25rem; color: #aaa; }
  .dot {
    position: absolute; width: 18px; height: 18px; margin-left: -9px; margin-top: -9px;
    border-radius: 50%; background: rgba(255,80,80,0.85); color: white; font-size: 11px;
    display: flex; align-items: center; justify-content: center; pointer-events: none;
    border: 1px solid white;
  }
  #controls { margin-top: 1rem; display: flex; gap: 0.5rem; align-items: center; }
  button.action { padding: 0.4rem 0.8rem; cursor: pointer; }
  #status { margin-left: 1rem; color: #9ca3af; }
</style>
</head>
<body>
<h1>Click the same point on both photos, in order, to add this photo as an
anchor (need 4+ pairs) -- or Reject if it doesn't belong / isn't worth using.</h1>
<div id="nav">
  <button id="prev">&larr; Prev</button>
  <select id="photo-select"></select>
  <button id="next">Next &rarr;</button>
  <span id="counter"></span>
  <span id="status-badge"></span>
  <label><input type="checkbox" id="unreviewed-only"> Only step through unreviewed</label>
</div>
<div class="panes">
  <div class="pane" id="pane-reference">
    <div class="label">reference</div>
    <img id="img-reference">
  </div>
  <div class="pane" id="pane-anchor">
    <div class="label" id="anchor-label">photo</div>
    <img id="img-anchor">
  </div>
</div>
<div id="controls">
  <button class="action" id="undo">Undo last pair</button>
  <button class="action" id="clear">Clear all</button>
  <button class="action" id="save">Save as anchor</button>
  <button class="action" id="reject">Reject / doesn't match</button>
  <span id="status"></span>
</div>
<script>
let state = null;
let currentAnchor = null;
let pointsReference = [];
let pointsAnchor = [];
let awaiting = "reference"; // which image the next click should land on

function visiblePhotos() {
  if (!state) return [];
  const unreviewedOnly = document.getElementById("unreviewed-only").checked;
  return unreviewedOnly ? state.anchors.filter(a => !a.done && !a.rejected) : state.anchors;
}

async function refreshState(advance) {
  state = await (await fetch("/api/state")).json();
  const select = document.getElementById("photo-select");
  select.innerHTML = "";
  state.anchors.forEach(a => {
    const opt = document.createElement("option");
    const prefix = a.done ? "✓ " : a.rejected ? "✗ " : "· ";
    const suffix = a.done ? ` (${a.num_points} pts)` : a.rejected ? " (rejected)" : "";
    opt.value = a.name;
    opt.textContent = prefix + a.name + suffix;
    select.appendChild(opt);
  });

  if (!currentAnchor && state.anchors.length) {
    selectAnchor(visiblePhotos()[0]?.name || state.anchors[0].name, false);
    return;
  }
  if (advance) {
    const pool = visiblePhotos();
    const idx = pool.findIndex(a => a.name === currentAnchor);
    const next = idx >= 0 ? pool[idx + 1] : pool[0];
    if (next) { selectAnchor(next.name, false); return; }
  }
  renderStatus();
}

function renderStatus() {
  const select = document.getElementById("photo-select");
  select.value = currentAnchor;
  const all = state.anchors;
  const idx = all.findIndex(a => a.name === currentAnchor);
  document.getElementById("counter").textContent = `${idx + 1} / ${all.length}`;
  const info = all[idx];
  const badge = document.getElementById("status-badge");
  if (info && info.done) {
    badge.textContent = `anchor (${info.num_points} pts)`;
    badge.className = "done";
  } else if (info && info.rejected) {
    badge.textContent = "rejected";
    badge.className = "rejected";
  } else {
    badge.textContent = "unreviewed";
    badge.className = "";
  }
}

function selectAnchor(name, refresh) {
  currentAnchor = name;
  pointsReference = [];
  pointsAnchor = [];
  awaiting = "reference";
  document.getElementById("anchor-label").textContent = name;
  document.getElementById("img-anchor").src =
    "/api/image?which=anchor&name=" + encodeURIComponent(name);
  redrawDots();
  if (refresh === false) { renderStatus(); } else { refreshState(); }
}

document.getElementById("img-reference").src = "/api/image?which=reference";

document.getElementById("photo-select").addEventListener("change", evt => {
  selectAnchor(evt.target.value, false);
});

document.getElementById("prev").onclick = () => {
  const pool = visiblePhotos();
  const idx = pool.findIndex(a => a.name === currentAnchor);
  const prev = idx > 0 ? pool[idx - 1] : pool[pool.length - 1];
  if (prev) selectAnchor(prev.name, false);
};

document.getElementById("next").onclick = () => {
  const pool = visiblePhotos();
  const idx = pool.findIndex(a => a.name === currentAnchor);
  const next = idx >= 0 && idx < pool.length - 1 ? pool[idx + 1] : pool[0];
  if (next) selectAnchor(next.name, false);
};

function toNatural(img, evt) {
  const rect = img.getBoundingClientRect();
  const scaleX = img.naturalWidth / rect.width;
  const scaleY = img.naturalHeight / rect.height;
  return [(evt.clientX - rect.left) * scaleX, (evt.clientY - rect.top) * scaleY];
}

function redrawDots() {
  for (const id of ["pane-reference", "pane-anchor"]) {
    document.querySelectorAll("#" + id + " .dot").forEach(el => el.remove());
  }
  const drawOn = (paneId, img, points) => {
    const rect = img.getBoundingClientRect();
    const scaleX = rect.width / img.naturalWidth;
    const scaleY = rect.height / img.naturalHeight;
    points.forEach((p, i) => {
      const dot = document.createElement("div");
      dot.className = "dot";
      dot.textContent = i + 1;
      dot.style.left = (p[0] * scaleX) + "px";
      dot.style.top = (p[1] * scaleY) + "px";
      document.getElementById(paneId).appendChild(dot);
    });
  };
  drawOn("pane-reference", document.getElementById("img-reference"), pointsReference);
  drawOn("pane-anchor", document.getElementById("img-anchor"), pointsAnchor);
}

document.getElementById("img-reference").addEventListener("click", evt => {
  if (awaiting !== "reference") return;
  pointsReference.push(toNatural(evt.target, evt));
  awaiting = "anchor";
  redrawDots();
});

document.getElementById("img-anchor").addEventListener("click", evt => {
  if (awaiting !== "anchor") return;
  pointsAnchor.push(toNatural(evt.target, evt));
  awaiting = "reference";
  redrawDots();
});

document.getElementById("undo").onclick = () => {
  if (awaiting === "anchor") { pointsReference.pop(); awaiting = "reference"; }
  else {
    pointsAnchor.pop();
    if (pointsAnchor.length < pointsReference.length) pointsReference.pop();
  }
  redrawDots();
};

document.getElementById("clear").onclick = () => {
  pointsReference = []; pointsAnchor = []; awaiting = "reference"; redrawDots();
};

document.getElementById("save").onclick = async () => {
  const statusEl = document.getElementById("status");
  if (pointsReference.length !== pointsAnchor.length || pointsReference.length < 4) {
    statusEl.textContent = "Need at least 4 complete pairs.";
    return;
  }
  const res = await fetch("/api/save", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      anchor: currentAnchor,
      points_reference: pointsReference,
      points_anchor: pointsAnchor,
    }),
  });
  statusEl.textContent = res.ok ? "Saved." : "Save failed -- see terminal.";
  if (res.ok) refreshState(true);
  else refreshState();
};

document.getElementById("reject").onclick = async () => {
  const statusEl = document.getElementById("status");
  const res = await fetch("/api/reject", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({anchor: currentAnchor}),
  });
  statusEl.textContent = res.ok ? "Rejected." : "Reject failed -- see terminal.";
  if (res.ok) refreshState(true);
  else refreshState();
};

window.addEventListener("resize", redrawDots);
refreshState();
</script>
</body>
</html>
"""


def make_handler(input_dir, reference_path, control_points_path, photos):
    input_dir = Path(input_dir)
    reference_path = Path(reference_path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format_, *args):
            pass  # quiet -- the CLI already prints the URL to use

        def _json(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _image(self, path):
            data = path.read_bytes()
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/state":
                data = cp.load(control_points_path)
                rejected = set(data.get("rejected", []))
                self._json(
                    {
                        "reference": reference_path.name,
                        "anchors": [
                            {
                                "name": name,
                                "done": name in data["anchors"],
                                "rejected": name in rejected,
                                "num_points": len(
                                    data["anchors"].get(name, {}).get("points_reference", [])
                                ),
                            }
                            for name in photos
                        ],
                    }
                )
            elif parsed.path == "/api/image":
                query = parse_qs(parsed.query)
                which = query.get("which", [None])[0]
                if which == "reference":
                    self._image(reference_path)
                elif which == "anchor":
                    name = query.get("name", [None])[0]
                    if name not in photos:
                        self.send_error(404, "unknown photo")
                        return
                    self._image(input_dir / name)
                else:
                    self.send_error(400, "which must be 'reference' or 'anchor'")
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/save", "/api/reject"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            anchor = payload.get("anchor")
            if anchor not in photos:
                self._json({"error": f"unknown photo {anchor!r}"}, status=400)
                return

            data = cp.load(control_points_path)
            data["reference"] = reference_path.name

            if path == "/api/reject":
                cp.reject(data, anchor)
                cp.save(control_points_path, data)
                print(f"rejected {anchor}")
                self._json({"ok": True})
                return

            try:
                cp.set_anchor_points(
                    data, anchor, payload["points_reference"], payload["points_anchor"]
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, status=400)
                return
            cp.save(control_points_path, data)
            print(f"saved {len(payload['points_reference'])} point pair(s) for {anchor}")
            self._json({"ok": True})

    return Handler


def parse_args():
    parser = argparse.ArgumentParser(
        description="Local web tool for reviewing every photo in a sequence: click control "
        "points onto ones worth using as a manually-verified anchor, or reject ones that "
        "don't belong."
    )
    parser.add_argument("input", type=Path, help="Directory of source photos")
    parser.add_argument("control_points", type=Path, help="Control points JSON file to write")
    parser.add_argument("--reference", type=Path, required=True, help="Reference photo")
    parser.add_argument(
        "--photos",
        type=str,
        default=None,
        help="Comma-separated photo filenames to review (default: every photo in the input "
        "directory, in capture-time order, excluding the reference)",
    )
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.photos:
        photos = args.photos.split(",")
    else:
        reference_path = Path(args.reference).resolve()
        photos = [p.name for p in list_images(args.input) if p.resolve() != reference_path]
    if not photos:
        raise SystemExit("no photos to review -- pass --photos explicitly")

    handler = make_handler(args.input, args.reference, args.control_points, photos)
    server = ThreadingHTTPServer(("localhost", args.port), handler)
    print(f"{len(photos)} photo(s) to review.")
    print(f"Open http://localhost:{args.port}/ in your own browser (not this terminal).")
    print("Ctrl+C to stop -- your progress is saved to disk as you go, resume anytime.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
