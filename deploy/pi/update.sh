#!/usr/bin/env bash
set -euo pipefail

# Redeploys the Pi after a PR merges to main: pulls the latest commit,
# reinstalls dependencies if requirements.txt changed, and regenerates the
# status page immediately instead of waiting for the next capture tick.
# If the pull brings no new commits, dependency install and page regen are
# skipped — safe to run this on a timer without doing needless work every tick.
#
# Run this on the Pi itself:
#   deploy/pi/update.sh [config]
# config defaults to capture/config.pi.yaml.
#
# Does NOT touch systemd unit files — if a change also modifies
# deploy/pi/*.service or *.timer, copy them into place and restart the
# affected units yourself (see deploy/pi/README.md).

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

echo "==> Done. If deploy/pi/*.service or *.timer changed, also run:"
echo "      sudo cp deploy/pi/*.service deploy/pi/*.timer /etc/systemd/system/"
echo "      sudo systemctl daemon-reload"
echo "      sudo systemctl restart timelapse-capture.timer timelapse-web.service"
