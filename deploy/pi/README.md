# Pi bring-up

Runs the capture job on a Raspberry Pi via systemd — the sole scheduled capture platform for
this project. **This is deployed and running** on the Pi (hostname `timelapse-pi`), capturing
**all ten active cams** (the two Bluewood cams, three Seattle cams, two Washington-state
cams, the Pi-only UNCA tower North Carolina cam — Nantahala Outdoor Center stays
commented out — and two Hawaii cams, Mauna Kea observatories captured via YouTube live
streams) from `capture/config.pi.yaml`.
GitHub Actions previously ran this same capture job on a schedule too, in parallel, for a
~1-2 week hand-off trial (see `docs/open-questions.md` #1). That trial is complete: the
schedule trigger is disabled, and `.github/workflows/capture.yml` now exists only as a manual
(`workflow_dispatch`) emergency-capture fallback with nothing to commit day-to-day. The steps
below document a from-scratch bring-up.

**Running low on SD card space?** See `docs/sd-card-migration.md` for the 4GB → 64GB
migration process (`docs/open-questions.md` #11) — already executed once (2026-07-30), kept
here as a reference for any future card swap.

## Steps

1. Install system dependencies, then clone the repo to `/opt/timelapse-creator` (adjust if
   you use a different path — see the placeholder-paths note below), and hand ownership to
   your user so a manual `deploy/pi/update.sh` run doesn't need `sudo` for `git pull`:

   ```
   sudo apt update
   sudo apt install -y git python3-venv python3-pip build-essential python3-dev libyaml-dev \
       ffmpeg
   sudo git clone https://github.com/<owner>/timelapse-creator.git /opt/timelapse-creator
   sudo chown -R $USER:$USER /opt/timelapse-creator
   ```

   Raspberry Pi OS Lite doesn't ship `git` by default, and `python3 -m venv` needs
   `python3-venv` installed separately on Debian-based systems — both are needed before step
   2 below. `build-essential`/`python3-dev`/`libyaml-dev` cover step 2's armv6 wheel-build
   caveat up front. `ffmpeg` is needed by `capture/fetch.py`'s `type: stream`/`type: youtube`
   cams (the two Hawaii cams currently) — `yt-dlp` itself comes from `requirements.txt` in
   step 2, but it shells out to the system `ffmpeg` binary to grab a frame, so `ffmpeg` isn't
   a pip package here.

   `timelapse-update.service` (step 4) instead runs `update.sh` as root, so root also
   needs permission to `git pull` here despite the repo being owned by `$USER` — otherwise
   git refuses with "dubious ownership". Grant it once:

   ```
   sudo git config --system --add safe.directory /opt/timelapse-creator
   ```

   One consequence: files touched by an automated (root) pull end up root-owned, which is
   harmless for the automated path but means a later *manual* `git pull`/`update.sh` run as
   `$USER` may hit permission errors on those files — `sudo chown -R $USER:$USER
   /opt/timelapse-creator` clears it up if that happens.

2. Create a virtualenv and install dependencies:

   ```
   cd /opt/timelapse-creator
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

   **armv6 wheel caveat (Pi Zero W):** the original Pi Zero W is armv6, which doesn't
   always have prebuilt wheels on PyPI for every package version. `requests` and
   `PyYAML` are both small pure-Python-ish packages that build fine from source if pip
   falls back to a source build — expect it to take a little longer, not to fail. Step 1's
   `build-essential`/`python3-dev`/`libyaml-dev` install already covers what a source build
   needs, so this shouldn't require any extra intervention.

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
`main` is picked up automatically — no manual step needed, including for unit-file changes
(e.g. changing the capture timer's `OnCalendar` interval). Each run pulls `main`
(fast-forward only — refuses if the local repo is on another branch or has diverged); if
that pull brings no new commits, the run stops there. Only when new commits actually
landed does it reinstall dependencies from `requirements.txt` and regenerate the status
page. If the pulled commits also touched any `deploy/pi/*.service` or `*.timer` file,
`update.sh` copies the changed unit(s) into `/etc/systemd/system`, runs `systemctl
daemon-reload`, and restarts `timelapse-capture.timer`, `timelapse-web.service`, and
`timelapse-update.timer` — closing the loop that previously required SSHing in by hand.
Most ticks are a no-op `git pull` and nothing else. Plain code changes need no restart:
`timelapse-capture.service` re-reads the repo from disk on every timer tick, and
`timelapse-web.service` just serves whatever static files are already there.

To redeploy immediately instead of waiting for the next tick:

```
sudo systemctl start timelapse-update.service
```

or run `deploy/pi/update.sh` directly as `$USER` (it's the same script either way; the
`safe.directory` config from step 1 covers both root and `$USER`, so neither hits git's
"dubious ownership" check). A manual run only needs `sudo` if the pulled commits changed
unit files, since installing them into `/etc/systemd/system` requires root either way.
Watch it with `journalctl -u timelapse-update.service -f`.

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
otherwise expose port 8080 publicly (see `docs/open-questions.md` #8) — use the Tailscale
setup below for remote access instead.

## Samba share (browsing the archive from other machines)

Optional, but useful once you want to run `video/main.py` (or `daily_clip.py`/
`season_video.py`) against a cam directory from your own computer instead of on the Pi
itself — a read-only export of the raw frame archive over the local network, so there's no
copying frames around by hand first.

```
sudo apt install samba
```

Append a share definition to `/etc/samba/smb.conf`:

```
[timelapse]
   path = /var/lib/timelapse/archive
   read only = yes
   guest ok = yes
   force user = <pi-username>
```

(`<pi-username>` is the account created in step 1 above — `force user` makes guest
connections read with that account's permissions, since there's no real login to derive
them from otherwise.) Then:

```
sudo systemctl restart smbd
```

Mount it from another machine:
- **macOS:** Finder → Go → Connect to Server → `smb://timelapse-pi.local/timelapse`
- **Windows:** File Explorer address bar → `\\timelapse-pi.local\timelapse`
- **Linux:** `smbclient //timelapse-pi.local/timelapse` or a `cifs-utils` mount

**Trust model:** `guest ok = yes` matches the same "home network only, no auth" posture the
status page and its `/archive/` symlink already use (see `docs/open-questions.md` #8) —
anyone on the LAN can read every frame, nobody can write any. If that's ever not the right
call, switch to a real Samba account instead: `sudo smbpasswd -a <pi-username>`, then set
`guest ok = no` in the share definition above.

## Remote access (Tailscale)

Covers SSH, the status page, and the Samba share above from outside the home network, with
no port-forwarding:

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

This prints a login URL — open it in a browser, sign in (or create a free personal Tailscale
account), and approve the Pi joining your tailnet. The Pi then gets a stable `100.x.x.x`
address and a MagicDNS name (typically `timelapse-pi.<your-tailnet>.ts.net`), reachable from
any other device signed into the same tailnet. Install Tailscale on whichever other
device(s) you want to reach the Pi from too (same one-liner on Linux/Mac, the Tailscale app
on iOS/Android/Windows) — `ssh`, the status page, and the Samba share above then all just
work over the tailnet exactly as they do on the home network, no extra configuration per
service.

**Do this after switching to key-only SSH auth, not before** — once the Pi is reachable from
anywhere, password auth is a meaningfully bigger attack surface than "reachable only from
inside the home network."

Tailscale runs over its own virtual interface (`tailscale0`) rather than opening anything on
your home router — nothing here needs router config, which is the main appeal over plain
port-forwarding.

## Status

Deployed and confirmed working on the Pi (`timelapse-pi`): the capture timer runs against
`capture/config.pi.yaml` (all ten active cams, most at 15 min, unca-tower at 60 min), and
the status page is live at `http://timelapse-pi.local:8080/`. The paths in the unit files
match that deployment (`/opt/timelapse-creator`, `/var/lib/timelapse`); adjust them if yours
differ.

`timelapse-update.timer` (auto-redeploy on merge to `main`, including unit-file changes
like a capture cadence tweak) is written up above but not yet installed on `timelapse-pi` —
run the `safe.directory` config from step 1, copy the unit into place per the "Copy the
unit files" step, and `enable --now` it to turn on automatic deploys.
