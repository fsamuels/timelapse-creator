import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "pi" / "net-watchdog.sh"


@pytest.fixture
def env(tmp_path):
    """Stub `ip`/`ping`/`systemctl`/`sync` so the script runs without touching the host."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ip").write_text("#!/bin/sh\necho 'default via 192.168.1.1 dev wlan0'\n")
    (bin_dir / "ping").write_text('#!/bin/sh\nexit "${PING_EXIT:-0}"\n')
    (bin_dir / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/systemctl.log\n')
    (bin_dir / "sync").write_text("#!/bin/sh\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAIL_FILE": str(tmp_path / "fails"),
        "LAST_REBOOT_FILE": str(tmp_path / "last-reboot"),
        "MAX_FAILS": "3",
        "MIN_REBOOT_GAP": "1000",
    }


def run(env, ping_exit=1):
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={**env, "PING_EXIT": str(ping_exit)},
        capture_output=True,
        text=True,
        check=True,
    )


def rebooted(env):
    log = Path(env["FAIL_FILE"]).parent / "systemctl.log"
    return log.exists() and "reboot" in log.read_text()


def test_healthy_gateway_resets_failure_count(env):
    run(env)
    run(env)
    assert Path(env["FAIL_FILE"]).read_text().strip() == "2"
    run(env, ping_exit=0)
    assert not Path(env["FAIL_FILE"]).exists()


def test_reboots_only_after_max_consecutive_failures(env):
    run(env)
    run(env)
    assert not rebooted(env)
    run(env)
    assert rebooted(env)


def test_recovery_between_failures_prevents_reboot(env):
    run(env)
    run(env)
    run(env, ping_exit=0)
    run(env)
    run(env)
    assert not rebooted(env)


def test_loop_guard_blocks_reboot_shortly_after_previous_one(env):
    for _ in range(3):
        run(env)
    assert rebooted(env)
    log = Path(env["FAIL_FILE"]).parent / "systemctl.log"
    log.unlink()
    # Simulate coming back up (fail counter lives in /run, so it resets) and failing again.
    Path(env["FAIL_FILE"]).unlink()
    for _ in range(3):
        run(env)
    assert not rebooted(env)


def test_loop_guard_allows_reboot_once_gap_has_passed(env):
    Path(env["LAST_REBOOT_FILE"]).write_text("0\n")  # long ago
    for _ in range(3):
        run(env)
    assert rebooted(env)
