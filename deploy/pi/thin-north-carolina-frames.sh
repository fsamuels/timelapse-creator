#!/usr/bin/env bash
set -euo pipefail

# One-off maintenance script: the north-carolina cams were briefly captured
# every 15 min before moving to interval_minutes: 60 (see docs/open-questions.md
# #2). This retroactively thins the frames captured during that window down to
# roughly 1-in-4, so the archive reads as hourly throughout.
#
# NOTE: this permanently deletes real captured frames, which cuts against this
# project's usual "archive everything raw" rule (docs/design.md) — only use it
# if you've decided that's an acceptable tradeoff here. Back up the directory
# first if you want a way back:
#   tar -czf north-carolina-backup-$(date +%Y%m%d).tar.gz -C /var/lib/timelapse/archive north-carolina
#
# Keeps every 4th frame per cam, in filename (== chronological) order — a
# mechanical 1-in-4 keep, not "whichever frame is closest to the hour mark",
# so a cam with a missed capture will drift which frames survive.
#
# Usage (dry run by default — prints what it would delete, deletes nothing):
#   deploy/pi/thin-north-carolina-frames.sh [archive_dir] [--confirm]
# archive_dir defaults to /var/lib/timelapse/archive/north-carolina.
# Pass --confirm to actually delete.

ARCHIVE_ROOT="/var/lib/timelapse/archive/north-carolina"
CONFIRM=false
for arg in "$@"; do
  case "$arg" in
    --confirm) CONFIRM=true ;;
    *) ARCHIVE_ROOT="$arg" ;;
  esac
done

if [ ! -d "$ARCHIVE_ROOT" ]; then
  echo "error: $ARCHIVE_ROOT not found" >&2
  exit 1
fi

total_kept=0
total_deleted=0

for cam_dir in "$ARCHIVE_ROOT"/*/; do
  cam="$(basename "$cam_dir")"
  mapfile -t files < <(find "$cam_dir" -type f -name '*.jpg' | sort)
  n=${#files[@]}
  kept=0
  deleted=0

  for i in "${!files[@]}"; do
    if (( i % 4 == 0 )); then
      kept=$((kept + 1))
    else
      deleted=$((deleted + 1))
      if $CONFIRM; then
        rm -- "${files[$i]}"
      else
        echo "would delete: ${files[$i]}"
      fi
    fi
  done

  echo "$cam: $n frames -> keeping $kept, deleting $deleted"
  total_kept=$((total_kept + kept))
  total_deleted=$((total_deleted + deleted))
done

echo "TOTAL: keeping $total_kept, deleting $total_deleted"
if ! $CONFIRM; then
  echo
  echo "Dry run only — nothing was deleted. Re-run with --confirm to actually delete."
fi
