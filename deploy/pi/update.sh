#!/usr/bin/env bash
set -euo pipefail

# Redeploys the Pi after a PR merges to main: pulls the latest commit,
# reinstalls dependencies if requirements.txt changed, regenerates the status
# page, and — if the PR touched any deploy/pi/*.service or *.timer file —
# installs the changed units and restarts them (e.g. picking up a capture
# cadence change). If the pull brings no new commits, all of the above is
# skipped — safe to run this on a timer without doing needless work every tick.
#
# Run this on the Pi itself:
#   deploy/pi/update.sh [config]
# config defaults to capture/config.pi.yaml.
#
# Runs as root via timelapse-update.timer/.service so it can install unit
# files and restart units without a password prompt. Root running `git pull`
# in a repo owned by another user needs a one-time
#   git config --system --add safe.directory /opt/timelapse-creator
# (see deploy/pi/README.md) — without it git refuses with "dubious
# ownership". Expect files touched by an automated pull to end up root-owned.

cd "$(dirname "$0")/../.."

CONFIG="${1:-capture/config.pi.yaml}"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if [ "$BRANCH" != "main" ]; then
  echo "error: on branch '$BRANCH', not 'main' — check out main before updating" >&2
  exit 1
fi

BEFORE_SHA="$(git rev-parse HEAD)"

echo "==> Pulling latest main"
git pull origin main --ff-only

AFTER_SHA="$(git rev-parse HEAD)"

if [ "$BEFORE_SHA" = "$AFTER_SHA" ]; then
  echo "==> Already up to date ($AFTER_SHA); nothing to do"
  exit 0
fi

echo "==> Installing dependencies"
.venv/bin/pip install -q -r requirements.txt

echo "==> Regenerating the status page"
.venv/bin/python -m web.generate --config "$CONFIG"

CHANGED_UNITS="$(git diff --name-only "$BEFORE_SHA" "$AFTER_SHA" -- 'deploy/pi/*.service' 'deploy/pi/*.timer')"
if [ -n "$CHANGED_UNITS" ]; then
  echo "==> Unit file(s) changed, installing and restarting:"
  echo "$CHANGED_UNITS"
  sudo cp deploy/pi/*.service deploy/pi/*.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl restart timelapse-capture.timer timelapse-web.service timelapse-update.timer
fi

echo "==> Done"
