#!/usr/bin/env bash
# Reboots the Pi if it has been unable to reach its default gateway for ~30
# minutes (MAX_FAILS consecutive failed runs of timelapse-net-watchdog.timer).
#
# Why: the hardware watchdog only catches a hard kernel hang. The failure mode
# seen here (docs/open-questions.md #13) is wifi going bad; if the radio wedges
# without taking the kernel with it, the Pi stays "up" but unreachable and
# capturing nothing, and nothing would ever reboot it.
#
# Only the gateway is checked, deliberately: an ISP outage with a healthy LAN
# isn't fixed by rebooting the Pi, and would otherwise cause pointless reboots.
#
# Loop guard: at most one watchdog reboot per MIN_REBOOT_GAP seconds, tracked in
# a file that survives reboots, so a permanently dead router can't reboot-loop
# the Pi. Failure counting itself lives in /run (tmpfs) and resets on boot.
#
# Overridable via environment for testing (tests/test_net_watchdog.py).

set -u

FAIL_FILE="${FAIL_FILE:-/run/timelapse-net-watchdog.fails}"
LAST_REBOOT_FILE="${LAST_REBOOT_FILE:-/var/lib/timelapse/net-watchdog-last-reboot}"
MAX_FAILS="${MAX_FAILS:-6}"
MIN_REBOOT_GAP="${MIN_REBOOT_GAP:-21600}"

gateway="$(ip -4 route show default 2>/dev/null | awk '/^default/ {print $3; exit}')"

if [ -n "$gateway" ] && ping -c 3 -W 5 "$gateway" >/dev/null 2>&1; then
  rm -f "$FAIL_FILE"
  exit 0
fi

fails=$(( $(cat "$FAIL_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" >"$FAIL_FILE"
echo "gateway '${gateway:-none}' unreachable ($fails/$MAX_FAILS consecutive checks)"

if [ "$fails" -lt "$MAX_FAILS" ]; then
  exit 0
fi

now="$(date +%s)"
last="$(cat "$LAST_REBOOT_FILE" 2>/dev/null || echo 0)"
if [ $(( now - last )) -lt "$MIN_REBOOT_GAP" ]; then
  echo "would reboot, but last watchdog reboot was $(( now - last ))s ago (< ${MIN_REBOOT_GAP}s); skipping"
  exit 0
fi

echo "$now" >"$LAST_REBOOT_FILE"
echo "rebooting"
sync
systemctl reboot
