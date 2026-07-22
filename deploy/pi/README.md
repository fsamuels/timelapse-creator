# Pi bring-up

Runs the capture job on a Raspberry Pi via systemd instead of GitHub Actions. **This is
deployed and running** on the Pi (hostname `timelapse-pi`), capturing **all four cams** (the
two Bluewood cams plus the two Seattle KING 5 dev cams) from `capture/config.pi.yaml`. GitHub
Actions (`.github/workflows/capture.yml`) still captures Bluewood in parallel during the
hand-off trial (see `docs/open-questions.md` #1); the two capture paths run independently and
nothing here disables the Actions schedule. The steps below document a from-scratch bring-up.

## Steps

1. Clone the repo to `/opt/timelapse-creator` (adjust if you use a different path — see
   the placeholder-paths note below), then hand ownership to your user so later updates
   (`deploy/pi/update.sh`) don't need `sudo` for `git pull`:

   ```
   sudo git clone https://github.com/<owner>/timelapse-creator.git /opt/timelapse-creator
   sudo chown -R $USER:$USER /opt/timelapse-creator
   ```

2. Create a virtualenv and install dependencies:

   ```
   cd /opt/timelapse-creator
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

   **armv6 wheel caveat (Pi Zero W):** the original Pi Zero W is armv6, which doesn't
   always have prebuilt wheels on PyPI for every package version. `requests` and
   `PyYAML` are both small pure-Python-ish packages that build fine from source if pip
   falls back to a source build — expect it to take a little longer, not to fail. If it
   does fail, install build essentials first (`sudo apt install build-essential
   python3-dev libyaml-dev`).

3. Create the local-disk storage directory used by `capture/config.pi.yaml`
   (`archive_dir`, `capture_log`, and the status page's `web_output`):

   ```
   sudo mkdir -p /var/lib/timelapse/www
   sudo chown -R $USER:$USER /var/lib/timelapse
   ```

4. Copy the unit files into place:

   ```
   sudo cp deploy/pi/timelapse-capture.service deploy/pi/timelapse-capture.timer \
           deploy/pi/timelapse-web.service /etc/systemd/system/
   ```

5. **Adjust the placeholder paths** in the unit files if your clone or venv don't live
   at `/opt/timelapse-creator` — `WorkingDirectory` and the `ExecStart`/`ExecStartPost`
   lines in `timelapse-capture.service` assume that path, and `timelapse-web.service`
   serves `/var/lib/timelapse/www` (matching `web_output` in `capture/config.pi.yaml`).

6. Reload systemd and enable the timer and the web server:

   ```
   sudo systemctl daemon-reload
   sudo systemctl enable --now timelapse-capture.timer
   sudo systemctl enable --now timelapse-web.service
   ```

7. Watch it run:

   ```
   journalctl -u timelapse-capture.service -f
   ```

## Updating the deployment

Once a PR merges to `main`, redeploy the code change on the Pi with:

```
deploy/pi/update.sh
```

It pulls `main` (fast-forward only — refuses if you're on another branch or the local repo
has diverged), reinstalls dependencies from `requirements.txt`, and regenerates the status
page immediately rather than waiting for the next capture tick. Nothing needs restarting for
a plain code change: `timelapse-capture.service` re-reads the repo from disk on every timer
tick, and `timelapse-web.service` just serves whatever static files are already there.

**Exception:** if the PR also changed `deploy/pi/*.service` or `*.timer`, `update.sh` won't
pick that up — copy the unit files into place and restart the affected units yourself:

```
sudo cp deploy/pi/*.service deploy/pi/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart timelapse-capture.timer timelapse-web.service
```

## Status page

`capture/main.py` writes frames and the capture log; `web/generate.py` turns those into
a single static `index.html` (health/status table per cam, including per-cam and total
disk usage, + a GitHub-style activity heatmap). It also symlinks `www/archive` to the
configured `archive_dir`, so the raw frame archive is browsable (plain directory listing)
alongside the generated page. The capture service regenerates the page after every run
via `ExecStartPost`, and `timelapse-web.service` serves it with `python -m http.server` —
no persistent app server. Once the timer has run at least once, browse to
`http://<pi-hostname>:8080/` from the home network — on the current deployment that's
`http://timelapse-pi.local:8080/`.

To generate the page by hand (e.g. to check it before enabling the timer):

```
/opt/timelapse-creator/.venv/bin/python -m web.generate --config capture/config.pi.yaml
```

**No auth:** the page — and, via the `archive/` symlink, the entire raw frame archive —
trusts the home network and is served on all interfaces. Don't port-forward it or
otherwise expose port 8080 publicly (see `docs/open-questions.md` #8); Tailscale is the
documented path for remote access.

## Status

Deployed and confirmed working on the Pi (`timelapse-pi`): the capture timer runs every
15 minutes against `capture/config.pi.yaml` (all four cams), and the status page is live at
`http://timelapse-pi.local:8080/`. The paths in the unit files match that deployment
(`/opt/timelapse-creator`, `/var/lib/timelapse`); adjust them if yours differ.
