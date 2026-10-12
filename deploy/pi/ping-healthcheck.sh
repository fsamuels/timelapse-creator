#!/usr/bin/env bash
# Pings healthchecks.io (or any compatible URL) to say "a capture run finished".
# Run as ExecStartPost of timelapse-capture.service, so it only fires when
# capture itself ran to completion — a hung capture, dead wifi, or a Pi that's
# down all stop the pings, and healthchecks.io emails when they stop.
#
# The URL is a secret-ish per-check token, so it lives on the Pi in
# /etc/timelapse/healthchecks.env (HC_PING_URL=https://hc-ping.com/<uuid>),
# not in git. No file / no URL = silently does nothing. Never fails the unit.

[ -n "${HC_PING_URL:-}" ] || exit 0
curl -fsS -m 10 --retry 3 -o /dev/null "$HC_PING_URL" || echo "healthcheck ping failed" >&2
exit 0
