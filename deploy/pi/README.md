# Pi bring-up

Runs the capture job on a Raspberry Pi via systemd instead of GitHub Actions. **This
currently captures the Seattle (KING 5) cams only** (`capture/config.pi.yaml`) — the
off-grid Bluewood cams stay on GitHub Actions (`.github/workflows/capture.yml`) for
now and move over once the Pi trial begins (see `docs/open-questions.md` #1). The two
capture paths run independently and in parallel; nothing here disables the Actions
schedule.

## Steps

1. Clone the repo to `/opt/timelapse-creator` (adjust if you use a different path — see
   the placeholder-paths note below):

   ```
   sudo git clone https://github.com/<owner>/timelapse-creator.git /opt/timelapse-creator
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
   (`archive_dir` and `capture_log`):

   ```
   sudo mkdir -p /var/lib/timelapse
   sudo chown $USER:$USER /var/lib/timelapse
   ```

4. Copy the two unit files into place:

   ```
   sudo cp deploy/pi/timelapse-capture.service deploy/pi/timelapse-capture.timer /etc/systemd/system/
   ```

5. **Adjust the placeholder paths** in both unit files if your clone or venv don't live
   at `/opt/timelapse-creator` — `WorkingDirectory` and `ExecStart` in
   `timelapse-capture.service` assume that path.

6. Reload systemd and enable the timer:

   ```
   sudo systemctl daemon-reload
   sudo systemctl enable --now timelapse-capture.timer
   ```

7. Watch it run:

   ```
   journalctl -u timelapse-capture.service -f
   ```

## Status

Confirmed working on-device: the timer fires on the documented 15-minute cadence
(`systemctl list-timers`), and `capture.main` saves frames and appends `capture.log`
entries to `/var/lib/timelapse` as expected (see `docs/open-questions.md` #10). Bluewood
was added to `capture/config.pi.yaml` alongside the Seattle cams once this was confirmed
reliable, starting the GitHub Actions/Pi trial from `docs/open-questions.md` #1 — existing
git-committed Bluewood frames were migrated onto this Pi's local disk at matching
`archive/<cam>/YYYY/MM/...` paths.
