# Pi SD card migration: 4GB → 64GB

**Status: executed, 2026-07-30.** This was the runbook used to replace the Pi's original 4GB
card — see `docs/open-questions.md` #11 for why (six cams capturing at the time, up from
four, and the North Carolina cams' PNG snapshots are likely larger per frame than the
Bluewood/Seattle JPEGs). The Pi is now running on the 64GB card; this doc is kept as a
reference for any future card swap.

## Recommended approach: fresh OS install + `rsync` the archive over

Rather than a full-disk clone (`dd` / Raspberry Pi Imager's "clone" mode), install
Raspberry Pi OS fresh on the new card and copy just the data over. Two reasons this is
preferred for this project specifically:

- **No partition resizing.** A cloned 4GB image still has a 4GB partition table after
  writing it to a 64GB card — you'd have to resize the root partition/filesystem
  afterward (`raspi-config` → Advanced Options → Expand Filesystem, or `parted` +
  `resize2fs` by hand) to actually use the extra space. A fresh Raspberry Pi OS image
  auto-expands to fill the card on first boot.
- **Free verification of the bring-up doc.** Doing a from-scratch install means re-running
  `deploy/pi/README.md`'s bring-up steps for real, which catches any drift between that doc
  and what actually works — a clone skips that check entirely.

The only thing that actually needs to move is `/var/lib/timelapse/` (the archive, the
capture log, and the generated status page — the last one is regenerated automatically, so
only the archive and log matter). Everything else (OS, venv, systemd units) is cheaper to
reinstall than to clone.

### Prerequisites

- A second, larger microSD card (64GB) and a way to write to it — a USB SD card reader on
  another machine (this doesn't have to be the Pi itself; the Pi Zero W has only one card
  slot, so the new card is prepared separately and swapped in at the end).
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on that other machine.
- Network access to the Pi (`ssh` to `timelapse-pi.local`) for the `rsync` steps below.
- If your workstation's local username differs from the Pi's account (e.g. a Mac's default
  shortname vs. the Pi login), either prefix every remote path below with `<pi-username>@`,
  or add this once to `~/.ssh/config` so it's automatic from then on:
  ```
  Host timelapse-pi.local
      User <pi-username>
  ```

### Steps

1. **Copy the archive off the old card before touching anything else.** Prefer `rsync` over
   a `tar` snapshot — it verifies transferred bytes, only re-sends what's changed on a second
   pass, and (with `-a`) preserves timestamps/permissions, though none of that is actually
   load-bearing here: `capture/archive.py` encodes each frame's real capture time in its
   filename, not its mtime, so even a mangled-timestamp copy wouldn't break anything
   downstream. The exact mechanics depend on what hardware is available:

   - **Spare Pi, or a Linux workstation that can mount the card's ext4 partition:** rsync
     directly, old card → new card, over the network — no intermediate copy needed:
     ```
     rsync -avz --progress <pi-username>@timelapse-pi.local:/var/lib/timelapse/archive/ /var/lib/timelapse/archive/
     rsync -avz --progress <pi-username>@timelapse-pi.local:/var/lib/timelapse/capture.log /var/lib/timelapse/
     ```
     Run this from wherever the new card is reachable — booted in the spare Pi, or mounted
     via a USB reader on the Linux machine.

   - **Neither available (the common case — most workstations are macOS/Windows, and a
     dedicated capture Pi typically has no spare sibling to borrow):** relay through the
     workstation instead. This isn't just the easier option, it's close to the only one: the
     new card's root filesystem is **ext4**, which macOS/Windows can't read or write natively
     without third-party drivers, and the Pi Zero W's single card slot means the old and new
     cards can never be network-reachable at the same time anyway — direct card-to-card
     rsync simply isn't on the table without a second Pi. Two hops instead:

     ```
     # Leg 1 — old Pi → workstation, while the old card is still running normally
     mkdir -p ~/timelapse-backup
     rsync -avz --progress <pi-username>@timelapse-pi.local:/var/lib/timelapse/archive/ ~/timelapse-backup/archive/
     rsync -avz --progress <pi-username>@timelapse-pi.local:/var/lib/timelapse/capture.log ~/timelapse-backup/
     ```

     Then flash and bring up the new card (steps 2–4 below). Once it answers on the network:

     ```
     # Leg 2 — workstation → new Pi (this is also step 5, "restore the archive", below)
     rsync -avz --progress ~/timelapse-backup/archive/ <pi-username>@timelapse-pi.local:/var/lib/timelapse/archive/
     rsync -avz --progress ~/timelapse-backup/capture.log <pi-username>@timelapse-pi.local:/var/lib/timelapse/
     ```

     Note the trailing slash on `archive/` on *both* sides of *both* legs — that's what makes
     rsync sync directory contents into the destination directory, rather than nesting an
     extra `archive/archive/` level.

     A gap in captured frames while the old card is offline for this is expected and fine —
     the project already treats "cam down" as ordinary operation (see `docs/design.md`'s
     outage handling), and a card swap is just another outage. For the tightest possible gap,
     run leg 1 twice: once early (moves the bulk of the data with zero downtime), and again
     right after stopping the timer in step 3 below (catches just the last few minutes of
     frames before the physical swap) — but a single pass is fine if a small gap doesn't
     matter to you.

   Keep this copy (`~/timelapse-backup/`, and/or the old card itself) until the migration is
   verified end-to-end (step 6). This is the safety net — everything below should be
   reversible by just continuing to use the old card if something goes wrong.

2. **Flash the new 64GB card** with Raspberry Pi Imager, same OS choice as the original
   bring-up (Raspberry Pi OS Lite, headless — use Imager's advanced options (⚙) to preload
   WiFi credentials and enable SSH, same as the first Pi setup). Do this on the other
   machine's card reader; the Pi itself keeps running on the old card throughout.

3. **Boot the new card** — either in a spare Pi temporarily, or plan for a short capture
   outage and do steps 3–6 on the live Pi. If planning for an outage, stop the timer first
   so nothing tries to write mid-migration:

   ```
   sudo systemctl stop timelapse-capture.timer timelapse-web.service
   ```

   Then physically swap the card, boot, and confirm SSH access to the new card
   (`ssh timelapse-pi.local` — expect a host key mismatch prompt since it's a new OS
   install; that's expected here, not a spoofing concern, since you just imaged the card
   yourself).

4. **Follow `deploy/pi/README.md`'s bring-up steps 1–6 from scratch** on the new card: clone
   the repo, create the venv, install dependencies, create `/var/lib/timelapse`, copy the
   systemd unit files, `daemon-reload`. **Do not** `enable --now` the timer yet — the archive
   needs to be restored first, or the very first tick's stale-frame detection has nothing to
   compare against (harmless, just means one extra frame gets archived unnecessarily).

5. **Restore the archive.** If step 1 used the direct card-to-card method, this is already
   done. If it used the workstation-relay method, this is leg 2 from step 1 above — run it
   now that the new card is reachable on the network.

6. **Verify the restore** before going live:

   ```
   find /var/lib/timelapse/archive -name '*.jpg' -o -name '*.png' | wc -l
   ```

   Compare against the same command run against the backup (or the old card, if still
   reachable) — counts should match exactly.

7. **Go live:** enable the timer and web service, then confirm one capture tick behaves as
   expected:

   ```
   sudo systemctl enable --now timelapse-capture.timer
   sudo systemctl enable --now timelapse-web.service
   journalctl -u timelapse-capture.service -f
   ```

   Check the status page (`http://timelapse-pi.local:8080/`) shows the full frame history
   and per-cam disk usage, and that the next 15-minute tick appends a new frame per cam
   rather than re-archiving something already present.

8. **Confirm the filesystem actually grew** to 64GB (`df -h /`), since a fresh Raspberry Pi
   OS image should auto-expand on first boot but it's worth a direct check rather than an
   assumption.

9. **Keep the old 4GB card** as a cold backup for a couple of weeks before wiping and
   reusing it, in case anything surfaces that the verification in steps 6–7 missed.

## Alternative: full-disk clone

A `dd`-style or Raspberry Pi Imager "clone" of the old card onto the new one is simpler as a
single command, but:

- still needs the partition/filesystem resize described above to use the extra ~60GB, and
- doesn't double as a bring-up-doc verification.

Only worth it if the fresh-install approach above turns out to be impractical (e.g. no
second machine with a card reader available).

## Downtime

The only unavoidable downtime is the physical card swap in step 3 plus however long steps
4–7 take on the live Pi (or none at all, if steps 3–6 are done on a spare Pi first and only
the final card swap happens on `timelapse-pi` itself). Either way, a gap in captured frames
during the migration is expected and handled the same way as any other Bluewood-style
outage — it's just a gap in the archive, not an error state (see `docs/design.md`'s
stale/outage handling).
