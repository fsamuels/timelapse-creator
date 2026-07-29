# Pi bring-up

Runs the capture job on a Raspberry Pi via systemd instead of GitHub Actions. **This is
deployed and running** on the Pi (hostname `timelapse-pi`), capturing **all six cams** (the
two Bluewood cams, the two Seattle KING 5 dev cams, and two Pi-only North Carolina cams —
UNCA tower and Nantahala Outdoor Center) from `capture/config.pi.yaml`. GitHub Actions
(`.github/workflows/capture.yml`) still captures Bluewood in parallel during the hand-off
trial (see `docs/open-questions.md` #1); the two capture paths run independently and nothing
here disables the Actions schedule. The steps below document a from-scratch bring-up.

**Running low on SD card space?** See `docs/sd-card-migration.md` for the documented
4GB → 64GB migration process (`docs/open-questions.md` #11) — not yet executed, but written
up for when the archive needs it.

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
           deploy/pi/timelapse-web.service deploy/pi/timelapse-update.service \
           deploy/pi/timelapse-update.timer /etc/systemd/system/
   ```

5. **Adjust the placeholder paths** in the unit files if your clone or venv don't live
   at `/opt/timelapse-creator` — `WorkingDirectory` and the `ExecStart`/`ExecStartPost`
   lines in `timelapse-capture.service` assume that path, and `timelapse-web.service`
   serves `/var/lib/timelapse/www` (matching `web_output` in `capture/config.pi.yaml`).
   Also **adjust the placeholder `User`/`Group`** (`pi`) in `timelapse-update.service` to
   whichever account owns the clone (the one you `chown`ed to in step 1) — it runs `git
   pull`, and running that as a different user than the repo owner trips git's "dubious
   ownership" safety check.

6. Reload systemd and enable the timer, the web server, and the auto-update timer:

   ```
   sudo systemctl daemon-reload
   sudo systemctl enable --now timelapse-capture.timer
   sudo systemctl enable --now timelapse-web.service
   sudo systemctl enable --now timelapse-update.timer
   ```

7. Watch it run:

   ```
   journalctl -u timelapse-capture.service -f
   ```

## Updating the deployment

`timelapse-update.timer` runs `deploy/pi/update.sh` every 10 minutes, so a PR merged to
`main` is picked up automatically — no manual step needed. Each run pulls `main`
(fast-forward only — refuses if the local repo is on another branch or has diverged); if
that pull brings no new commits, the run stops there. Only when new commits actually
landed does it reinstall dependencies from `requirements.txt` and regenerate the status
page, so most ticks are a no-op `git pull` and nothing else. Nothing needs restarting for
a plain code change: `timelapse-capture.service` re-reads the repo from disk on every timer
tick, and `timelapse-web.service` just serves whatever static files are already there.

To redeploy immediately instead of waiting for the next tick:

```
sudo systemctl start timelapse-update.service
```

or run `deploy/pi/update.sh` directly (it's the same script either way). Watch it with
`journalctl -u timelapse-update.service -f`.

**Exception:** if the PR also changed `deploy/pi/*.service` or `*.timer`, `update.sh` won't
pick that up — copy the unit files into place and restart the affected units yourself:

```
sudo cp deploy/pi/*.service deploy/pi/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart timelapse-capture.timer timelapse-web.service timelapse-update.timer
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
15 minutes against `capture/config.pi.yaml` (all six cams), and the status page is live at
`http://timelapse-pi.local:8080/`. The paths in the unit files match that deployment
(`/opt/timelapse-creator`, `/var/lib/timelapse`); adjust them if yours differ.

`timelapse-update.timer` (auto-redeploy on merge to `main`) is written up above but not yet
installed on `timelapse-pi` — copy it into place per the "Copy the unit files" step above
and `enable --now` it to turn on automatic deploys.
