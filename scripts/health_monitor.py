#!/usr/bin/env python3
"""
CricketRadar Health Monitor

Watches critical services and triggers recovery actions:
1. Checks radar serial port is accessible
2. Checks WebSocket server accepts connections
3. Restarts services on failure
4. Escalates to reboot after repeated failures

Run as systemd service for continuous monitoring.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("cricket.health")


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class CheckResult:
    """Result of a health check."""
    name: str
    healthy: bool
    message: str
    duration_ms: int = 0


@dataclass
class HealthState:
    """Tracks health state over time."""
    consecutive_failures: int = 0
    last_check_time: float = 0
    # None until a check has ACTUALLY observed this service healthy. It must not
    # default to "now": that fabricates a healthy observation at startup, and the
    # reboot gate below relies on being able to tell "died" from "never came up".
    last_healthy_time: Optional[float] = None
    total_checks: int = 0
    total_failures: int = 0
    recovery_actions_taken: int = 0

    @property
    def ever_healthy(self) -> bool:
        return self.last_healthy_time is not None


class HealthMonitor:
    """
    Monitors CricketRadar system health and triggers recovery.
    """

    def __init__(
        self,
        check_interval: float = 30.0,
        radar_device: str = "/dev/ttyUSB0",
        websocket_port: int = 5002,
        max_failures_before_restart: int = 2,
        max_restarts_before_reboot: int = 3,
        state_file: Optional[Path] = None,
        reboot_cooldown_sec: float = 3600.0,
        websocket_timeout: float = 10.0,
    ):
        self.check_interval = check_interval
        self.radar_device = radar_device
        self.websocket_port = websocket_port
        self.websocket_timeout = websocket_timeout
        self.max_failures_before_restart = max_failures_before_restart
        self.max_restarts_before_reboot = max_restarts_before_reboot
        # Reboot ledger must survive the reboot it records, so it cannot live in
        # /tmp (tmpfs on some images). Default beside the app.
        self.state_file = state_file or (Path(__file__).resolve().parent.parent / ".health-state.json")
        self.reboot_cooldown_sec = reboot_cooldown_sec

        self.radar_state = HealthState()
        self.server_state = HealthState()
        # The radar is NON-CRITICAL: a missing/faulty radar must never reboot the
        # Pi (server, UI, manual input and data gathering all work without it),
        # and it is never restarted either - see check_and_recover.
        self.server_restart_count = 0
        self.radar_degraded = False
        self.min_free_disk_mb = 500  # warn below this; gathering sessions are ~0.5-1GB
        self.running = False

    def check_radar(self) -> CheckResult:
        """
        Check the radar serial device is present, WITHOUT opening it.

        Opening the port asserts/drops DTR, and on this board the CP2105 DTR
        line is wired to the IWR6843's reset - a periodic "health" probe that
        can hard-stop the sensor is worse than no probe (this exact failure
        already cost a field session). Presence of the device node catches the
        dominant failure (USB unplug / power); data-flow health is the job of
        the process that owns the port.
        """
        start = time.time()

        try:
            st = os.stat(self.radar_device)
        except FileNotFoundError:
            return CheckResult(
                name="radar",
                healthy=False,
                message=f"Device {self.radar_device} not found",
                duration_ms=int((time.time() - start) * 1000),
            )
        except OSError as e:
            return CheckResult(
                name="radar",
                healthy=False,
                message=f"Cannot stat radar device: {e}",
                duration_ms=int((time.time() - start) * 1000),
            )

        import stat as stat_mod
        if not stat_mod.S_ISCHR(st.st_mode):
            return CheckResult(
                name="radar",
                healthy=False,
                message=f"{self.radar_device} exists but is not a character device",
                duration_ms=int((time.time() - start) * 1000),
            )

        return CheckResult(
            name="radar",
            healthy=True,
            message="Radar device present",
            duration_ms=int((time.time() - start) * 1000),
        )

    def check_disk_space(self) -> CheckResult:
        """
        Check free disk space. Long data-gathering sessions write ~0.5-1GB of
        JSONL; a full SD card takes down SQLite and journald with it.
        """
        start = time.time()
        try:
            import shutil
            usage = shutil.disk_usage("/")
            free_mb = usage.free // (1024 * 1024)
            healthy = free_mb >= self.min_free_disk_mb
            return CheckResult(
                name="disk",
                healthy=healthy,
                message=f"{free_mb}MB free" if healthy else (
                    f"LOW DISK: {free_mb}MB free (< {self.min_free_disk_mb}MB) - "
                    f"recordings and database writes at risk"
                ),
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as e:
            return CheckResult(
                name="disk",
                healthy=True,  # don't take recovery action on a broken check
                message=f"Disk check failed: {e}",
                duration_ms=int((time.time() - start) * 1000),
            )

    async def _ping_roundtrip(self) -> None:
        """
        Complete a real WebSocket handshake and a protocol-level ping/pong.

        Raises on any failure; returns None on success.
        """
        import websockets

        uri = f"ws://127.0.0.1:{self.websocket_port}"
        async with websockets.connect(uri, close_timeout=2) as ws:
            await ws.send(json.dumps({
                "type": "ping",
                "message_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "payload": {},
            }))
            # The server pushes connection_status and session_state on connect,
            # so read until the pong rather than assuming it arrives first.
            while True:
                data = json.loads(await ws.recv())
                if data.get("type") == "pong":
                    return

    async def check_websocket(self) -> CheckResult:
        """
        Check the server is actually SERVING, not merely bound.

        A bare TCP connect is satisfied by the kernel's listen backlog: it
        succeeds even when the server process is stopped or its event loop is
        blocked. The previous version of this check did exactly that and so
        reported a SIGSTOPped server as healthy - i.e. it could not detect the
        stuck states it exists to detect. A full handshake plus a ping/pong
        round trip cannot complete unless the process is scheduled and the
        message router is running.

        The timeout is deliberately generous: a Pi 3B+ mid-SD-card-write is
        slow, not dead, and a false positive here now costs a service restart.
        """
        start = time.time()

        def result(healthy: bool, message: str) -> CheckResult:
            return CheckResult(
                name="websocket",
                healthy=healthy,
                message=message,
                duration_ms=int((time.time() - start) * 1000),
            )

        try:
            await asyncio.wait_for(self._ping_roundtrip(), timeout=self.websocket_timeout)
            return result(True, f"WebSocket server answered ping on port {self.websocket_port}")
        except ImportError as e:
            # Probe is broken, not the server. Same convention as check_disk_space:
            # never take a recovery action on the strength of a check we cannot run.
            logger.error(f"Cannot run WebSocket health probe ({e}); treating as healthy")
            return result(True, f"WebSocket probe unavailable: {e}")
        except asyncio.TimeoutError:
            return result(False, (
                f"No pong within {self.websocket_timeout}s on port {self.websocket_port} "
                f"(bound but not serving, or event loop blocked)"
            ))
        except ConnectionRefusedError:
            return result(False, f"WebSocket connection refused on port {self.websocket_port}")
        except Exception as e:
            return result(False, f"WebSocket check failed: {type(e).__name__}: {e}")

    def restart_service(self, service_name: str) -> bool:
        """
        Restart a systemd service.

        Returns:
            True if restart command succeeded
        """
        logger.warning(f"Restarting service: {service_name}")

        try:
            # reset-failed first: if the unit hit its StartLimitBurst, a plain
            # restart is refused until the failure state is cleared.
            subprocess.run(
                ["sudo", "systemctl", "reset-failed", service_name],
                capture_output=True,
                timeout=10,
            )
            result = subprocess.run(
                ["sudo", "systemctl", "restart", service_name],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"Service {service_name} restarted successfully")
                return True
            else:
                logger.error(f"Failed to restart {service_name}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout restarting {service_name}")
            return False
        except Exception as e:
            logger.error(f"Error restarting {service_name}: {e}")
            return False

    def _read_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return {}

    def _write_state(self, state: dict) -> None:
        try:
            self.state_file.write_text(json.dumps(state))
        except OSError as e:
            # A full or read-only disk must not stop monitoring. It does mean the
            # cooldown cannot be enforced across the reboot, so say so loudly.
            logger.error(f"Cannot persist health state to {self.state_file}: {e}")

    def seconds_since_last_reboot(self) -> Optional[float]:
        """Seconds since the last reboot WE triggered, or None if never."""
        last = self._read_state().get("last_self_reboot")
        return time.time() - last if isinstance(last, (int, float)) else None

    def trigger_reboot(self) -> None:
        """
        Trigger system reboot as last resort.

        Records the attempt BEFORE issuing it: the reboot will kill this process,
        so a write afterwards would never happen and the cooldown would never
        engage - which is precisely how a reboot loop starts.
        """
        state = self._read_state()
        state["last_self_reboot"] = time.time()
        state["self_reboot_count"] = int(state.get("self_reboot_count", 0)) + 1
        self._write_state(state)

        logger.critical(
            f"Triggering system reboot due to repeated failures "
            f"(self-reboot #{state['self_reboot_count']})"
        )

        try:
            subprocess.run(
                ["sudo", "systemctl", "reboot"],
                timeout=10,
            )
        except Exception as e:
            logger.error(f"Failed to trigger reboot: {e}")

    def consider_reboot(self) -> bool:
        """
        Decide whether a reboot is a legitimate last resort, and take it if so.

        Two gates, both of which the previous unconditional reboot lacked:

        1. The server must have been observed healthy at least once since this
           monitor started. If it has NEVER come up, a reboot cannot fix the
           cause (a bad unit file, a missing ReadWritePaths directory, an
           uninstalled dependency, a corrupt DB) - it just repeats the whole
           boot and lands here again. `server_restart_count` is process-local,
           so the escalation restarts from zero after every reboot and the
           device power-cycles indefinitely, which is the worst possible field
           failure: it cannot even be SSH'd into reliably to diagnose.

        2. Not more than one self-reboot per cooldown window, persisted across
           reboots. This is the backstop if gate 1 is ever satisfied by a
           service that comes up briefly and then dies each boot.

        Returns True if a reboot was triggered.
        """
        if not self.server_state.ever_healthy:
            logger.critical(
                "Server has NEVER been healthy since this monitor started - NOT rebooting. "
                "A reboot cannot fix a persistent start failure and would loop the device. "
                "Investigate on the Pi: `systemctl status cricket-server` and "
                "`journalctl -u cricket-server -n 50 --no-pager`."
            )
            return False

        since = self.seconds_since_last_reboot()
        if since is not None and since < self.reboot_cooldown_sec:
            logger.critical(
                f"Server unrecoverable, but this monitor already rebooted "
                f"{int(since)}s ago (cooldown {int(self.reboot_cooldown_sec)}s) - NOT rebooting again. "
                f"The reboot did not fix it; manual intervention needed."
            )
            return False

        self.trigger_reboot()
        return True

    def update_state(self, state: HealthState, healthy: bool) -> None:
        """Update health state tracking."""
        state.total_checks += 1
        state.last_check_time = time.time()

        if healthy:
            state.consecutive_failures = 0
            state.last_healthy_time = time.time()
        else:
            state.consecutive_failures += 1
            state.total_failures += 1

    async def check_and_recover(self) -> HealthStatus:
        """
        Run all health checks and trigger recovery if needed.

        Returns:
            Overall health status
        """
        # Run checks
        radar_result = self.check_radar()
        server_result = await self.check_websocket()
        disk_result = self.check_disk_space()
        if not disk_result.healthy:
            logger.warning(f"Disk unhealthy: {disk_result.message}")

        # Update state
        self.update_state(self.radar_state, radar_result.healthy)
        self.update_state(self.server_state, server_result.healthy)

        if radar_result.healthy:
            if self.radar_degraded:
                logger.info("Radar device is back")
            self.radar_degraded = False
        if server_result.healthy:
            self.server_restart_count = 0

        if radar_result.healthy and server_result.healthy and disk_result.healthy:
            logger.debug(f"Health check passed: radar={radar_result.duration_ms}ms, server={server_result.duration_ms}ms")
            return HealthStatus.HEALTHY

        # Low disk is degraded (no recovery action - operator must free space)
        overall_status = HealthStatus.DEGRADED if not disk_result.healthy else HealthStatus.HEALTHY

        # ---- Radar: report only. NEVER restarted, NEVER reboots. ----
        #
        # This monitor used to restart cricket-radar.service when the device node
        # was present but checks were failing. That action is strictly harmful:
        # the restart re-runs sensorStop -> reconfig -> sensorStart, which this
        # project has recorded in the field as REPORTING SUCCESS while leaving the
        # chip silent, recoverable only by physically unplugging the radar (USB
        # power must actually drop; a soft reboot does not do it). Worse, the
        # trigger is mundane - a cable nudge makes the node vanish for a second,
        # and by the time the WebSocket probe has finished the kernel has
        # re-enumerated it, so `device_present` reads True and we "recover" a
        # working radar into a mute one. check_radar only stats the node, so the
        # mute chip then reports healthy for the rest of the session.
        #
        # Doing nothing is better: cricket-radar.service already waits for the
        # device, and a human replugging a cable is the only real fix.
        if not radar_result.healthy:
            overall_status = HealthStatus.DEGRADED
            if not self.radar_degraded:
                logger.warning(
                    f"Radar unhealthy: {radar_result.message}. NOT restarting cricket-radar "
                    f"- a reconfigure can silence the chip and only a physical replug "
                    f"recovers it. If this persists, unplug and reconnect the radar USB."
                )
            self.radar_degraded = True

        # ---- Server: the critical service. Reboot only as a last resort. ----
        if not server_result.healthy:
            overall_status = HealthStatus.UNHEALTHY
            logger.warning(
                f"Server unhealthy: {server_result.message} "
                f"(failures={self.server_state.consecutive_failures}, restarts={self.server_restart_count})"
            )
            if self.server_state.consecutive_failures >= self.max_failures_before_restart:
                if self.server_restart_count < self.max_restarts_before_reboot:
                    self.restart_service("cricket-server.service")
                    self.server_state.recovery_actions_taken += 1
                    self.server_restart_count += 1
                else:
                    logger.critical(
                        f"Server still down after {self.server_restart_count} restarts"
                    )
                    self.consider_reboot()

        return overall_status

    async def run(self) -> None:
        """
        Main monitoring loop.
        """
        logger.info(f"Health monitor starting (interval: {self.check_interval}s)")
        logger.info(f"Monitoring: radar={self.radar_device}, websocket=:{self.websocket_port}")

        self.running = True

        # Initial delay to let services start
        await asyncio.sleep(10)

        while self.running:
            try:
                status = await self.check_and_recover()

                # Log periodic summary
                if self.radar_state.total_checks % 10 == 0:
                    logger.info(
                        f"Health summary: checks={self.radar_state.total_checks}, "
                        f"radar_failures={self.radar_state.total_failures}, "
                        f"server_failures={self.server_state.total_failures}, "
                        f"radar_degraded={self.radar_degraded}, "
                        f"server_restarts={self.server_restart_count}, "
                        f"server_ever_healthy={self.server_state.ever_healthy}"
                    )

            except Exception as e:
                logger.error(f"Health check error: {e}")

            await asyncio.sleep(self.check_interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self.running = False


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="CricketRadar health monitor and recovery service"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Check interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--radar-device",
        default="/dev/ttyUSB0",
        help="Radar serial device path",
    )
    parser.add_argument(
        "--websocket-port",
        type=int,
        default=5002,
        help="WebSocket server port",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=2,
        help="Failures before restart (default: 2)",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        default=3,
        help="Restarts before reboot (default: 3)",
    )
    parser.add_argument(
        "--websocket-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for a ping/pong round trip (default: 10)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Where to persist the reboot ledger (must survive reboots)",
    )
    parser.add_argument(
        "--reboot-cooldown",
        type=float,
        default=3600.0,
        help="Minimum seconds between self-triggered reboots (default: 3600)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    monitor = HealthMonitor(
        check_interval=args.interval,
        radar_device=args.radar_device,
        websocket_port=args.websocket_port,
        max_failures_before_restart=args.max_failures,
        max_restarts_before_reboot=args.max_restarts,
        state_file=args.state_file,
        reboot_cooldown_sec=args.reboot_cooldown,
        websocket_timeout=args.websocket_timeout,
    )

    try:
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("Health monitor stopped by user")
        monitor.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
