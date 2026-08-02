"""
Tests for the health monitor's detection and escalation logic.

The two bugs these pin (2026-08 review, T1.4/T1.5/T1.6):

1. `check_websocket` used a bare `asyncio.open_connection`, which the kernel's
   listen backlog satisfies even when the server process is stopped. It reported
   a frozen server as healthy, i.e. it could not detect the stuck states it
   exists to detect. `test_bound_but_not_serving_is_unhealthy` is the case the
   old implementation passed and the new one must fail.

2. The reboot escalation was unconditional, and `last_healthy_time` defaulted to
   "now", so a server that had NEVER started looked identical to one that had
   died. Since `server_restart_count` is process-local, every reboot restarted
   the escalation from zero and the device power-cycled indefinitely.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from health_monitor import HealthMonitor, HealthState  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def monitor(tmp_path):
    return HealthMonitor(
        websocket_port=_free_port(),
        state_file=tmp_path / "health-state.json",
        websocket_timeout=2.0,
    )


# ---------------------------------------------------------------------------
# T1.4 - the check must require the server to actually serve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bound_but_not_serving_is_unhealthy(monitor):
    """
    A listening socket that never completes a handshake must read UNHEALTHY.

    This is the frozen-server case: the port is bound and the kernel accepts the
    connection into the backlog, but nothing services it. The pre-fix bare-TCP
    check returned healthy here.
    """
    server = await asyncio.start_server(
        lambda r, w: asyncio.sleep(3600),  # accept, then never respond
        "127.0.0.1",
        monitor.websocket_port,
    )
    async with server:
        result = await monitor.check_websocket()

    assert result.healthy is False
    assert "pong" in result.message.lower() or "not serving" in result.message.lower()


@pytest.mark.asyncio
async def test_nothing_listening_is_unhealthy(monitor):
    result = await monitor.check_websocket()
    assert result.healthy is False


@pytest.mark.asyncio
async def test_real_ping_pong_is_healthy(monitor):
    """A server that completes the handshake and answers `ping` reads HEALTHY."""
    websockets = pytest.importorskip("websockets")

    async def handler(ws, *args):
        # Mirror the real server: push unsolicited state first, so the probe has
        # to read past it rather than assuming pong arrives first.
        await ws.send(json.dumps({"type": "connection_status", "payload": {}}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "ping":
                await ws.send(json.dumps({
                    "type": "pong",
                    "in_reply_to": msg.get("message_id"),
                }))

    async with websockets.serve(handler, "127.0.0.1", monitor.websocket_port):
        result = await monitor.check_websocket()

    assert result.healthy is True, result.message


@pytest.mark.asyncio
async def test_probe_import_failure_does_not_trigger_recovery(monitor, monkeypatch):
    """
    If the probe itself cannot run, report healthy rather than restarting a
    server we have no evidence against - same convention as check_disk_space.
    """
    async def boom():
        raise ImportError("no websockets module")

    monkeypatch.setattr(monitor, "_ping_roundtrip", boom)
    result = await monitor.check_websocket()
    assert result.healthy is True
    assert "unavailable" in result.message.lower()


# ---------------------------------------------------------------------------
# T1.5 - reboot escalation must be gated
# ---------------------------------------------------------------------------

def test_state_does_not_fabricate_a_healthy_observation():
    """`last_healthy_time` must start unset, or 'never up' looks like 'died'."""
    assert HealthState().last_healthy_time is None
    assert HealthState().ever_healthy is False


def test_no_reboot_when_server_was_never_healthy(monitor, monkeypatch):
    """
    The reboot loop: a server that cannot start (bad unit, missing dependency,
    missing ReadWritePaths dir) is not fixable by rebooting, and the escalation
    counter resets every boot.
    """
    rebooted = []
    monkeypatch.setattr(monitor, "trigger_reboot", lambda: rebooted.append(True))

    assert monitor.consider_reboot() is False
    assert rebooted == []


def test_reboot_allowed_once_after_a_real_healthy_observation(monitor, monkeypatch):
    rebooted = []
    monkeypatch.setattr(monitor, "trigger_reboot", lambda: rebooted.append(True))

    monitor.update_state(monitor.server_state, healthy=True)  # genuinely came up
    assert monitor.server_state.ever_healthy is True

    assert monitor.consider_reboot() is True
    assert rebooted == [True]


def test_second_reboot_within_cooldown_is_refused(monitor, monkeypatch):
    """The persisted ledger is the backstop if the first gate is satisfied."""
    monkeypatch.setattr(monitor, "trigger_reboot", HealthMonitor.trigger_reboot.__get__(monitor))
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)

    monitor.update_state(monitor.server_state, healthy=True)
    assert monitor.consider_reboot() is True          # first one goes through
    assert monitor.consider_reboot() is False         # second is inside cooldown

    ledger = json.loads(monitor.state_file.read_text())
    assert ledger["self_reboot_count"] == 1


def test_reboot_ledger_survives_a_new_process(tmp_path, monkeypatch):
    """
    The ledger must be read back by a FRESH monitor - after the reboot it
    records, this process is gone. An in-memory counter would reboot forever.
    """
    state_file = tmp_path / "health-state.json"
    first = HealthMonitor(state_file=state_file)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    first.update_state(first.server_state, healthy=True)
    assert first.consider_reboot() is True

    second = HealthMonitor(state_file=state_file)  # simulates post-reboot restart
    second.update_state(second.server_state, healthy=True)
    rebooted = []
    monkeypatch.setattr(second, "trigger_reboot", lambda: rebooted.append(True))
    assert second.consider_reboot() is False
    assert rebooted == []


def test_unwritable_state_file_does_not_crash_monitoring(tmp_path, monkeypatch):
    monitor = HealthMonitor(state_file=tmp_path / "nope" / "deep" / "state.json")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)
    monitor.update_state(monitor.server_state, healthy=True)
    monitor.consider_reboot()  # must not raise


# ---------------------------------------------------------------------------
# T1.6 - the radar must never be restarted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_radar_never_restarts_a_service(monitor, monkeypatch):
    """
    Restarting cricket-radar re-runs sensorStop/reconfig/sensorStart, which is
    recorded in this project's field notes as reporting success while leaving
    the chip silent - recoverable only by a physical replug.
    """
    restarted = []
    monkeypatch.setattr(monitor, "restart_service", lambda name: restarted.append(name))
    monkeypatch.setattr(monitor, "trigger_reboot", lambda: restarted.append("REBOOT"))
    monitor.radar_device = "/dev/definitely-not-a-real-device"

    # Server healthy throughout, so only the radar could provoke an action.
    async def healthy_ws():
        return None

    monkeypatch.setattr(monitor, "_ping_roundtrip", healthy_ws)

    for _ in range(monitor.max_failures_before_restart + 3):
        await monitor.check_and_recover()

    assert restarted == []
    assert monitor.radar_degraded is True


@pytest.mark.asyncio
async def test_radar_present_but_failing_still_never_restarts(monitor, monkeypatch, tmp_path):
    """
    The dangerous case: the node exists (a cable nudge has re-enumerated it) so
    the old code read device_present=True and restarted into a mute radar.
    """
    restarted = []
    monkeypatch.setattr(monitor, "restart_service", lambda name: restarted.append(name))

    # A regular file: present, but not a character device, so the check fails
    # while os.path.exists() is True.
    fake = tmp_path / "ttyUSB0"
    fake.write_text("")
    monitor.radar_device = str(fake)

    async def healthy_ws():
        return None

    monkeypatch.setattr(monitor, "_ping_roundtrip", healthy_ws)

    for _ in range(monitor.max_failures_before_restart + 3):
        await monitor.check_and_recover()

    assert restarted == []


def test_radar_degraded_clears_when_the_device_returns(monitor):
    monitor.radar_degraded = True
    monitor.update_state(monitor.radar_state, healthy=True)
    # check_and_recover clears the flag; assert the state tracking it relies on
    assert monitor.radar_state.ever_healthy is True


# ---------------------------------------------------------------------------
# The systemd unit that the reboot loop depended on
# ---------------------------------------------------------------------------

UNIT = Path(__file__).resolve().parent.parent / "scripts" / "systemd" / "cricket-server.service"


def _section(text: str, name: str) -> str:
    """Return the body of [name], up to the next section header."""
    out, in_section = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == f"[{name}]"
            continue
        if in_section:
            out.append(stripped)
    return "\n".join(out)


def test_start_limit_directives_are_in_the_unit_section():
    """
    systemd ignores StartLimitIntervalSec in [Service] and silently falls back to
    a 10s default, which RestartSec=3 can never trip - so the crash-loop guard
    did not exist and an unstartable server restarted forever.
    """
    text = UNIT.read_text()
    service, unit = _section(text, "Service"), _section(text, "Unit")

    for key in ("StartLimitIntervalSec", "StartLimitBurst", "StartLimitAction"):
        assert f"{key}=" in unit, f"{key} must be in [Unit]"
        assert f"{key}=" not in service, f"{key} in [Service] is silently ignored"


def test_readwritepaths_tolerate_missing_directories():
    """
    A ReadWritePaths directory that does not exist fails the mount namespace
    (226/NAMESPACE) before Python runs. `recordings/` is gitignored, so a fresh
    clone has none.
    """
    for line in UNIT.read_text().splitlines():
        if line.startswith("ReadWritePaths="):
            assert line.startswith("ReadWritePaths=-"), (
                f"{line!r} must use the '-' prefix so a missing dir is non-fatal"
            )
