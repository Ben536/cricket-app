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
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
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
    last_healthy_time: float = field(default_factory=time.time)
    total_checks: int = 0
    total_failures: int = 0
    recovery_actions_taken: int = 0


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
    ):
        self.check_interval = check_interval
        self.radar_device = radar_device
        self.websocket_port = websocket_port
        self.max_failures_before_restart = max_failures_before_restart
        self.max_restarts_before_reboot = max_restarts_before_reboot

        self.radar_state = HealthState()
        self.server_state = HealthState()
        # Separate restart budgets. The radar is NON-CRITICAL: a missing/faulty
        # radar must never reboot the Pi (the server, UI, manual input and data
        # gathering all work without it). Only a genuinely dead *server* that
        # restarts cannot revive may, as a last resort, trigger a reboot.
        self.radar_restart_count = 0
        self.server_restart_count = 0
        self.max_radar_restarts = 3  # cap radar-service restarts; never reboots
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

    async def check_websocket(self) -> CheckResult:
        """
        Check if WebSocket server accepts connections.

        Returns:
            CheckResult with health status
        """
        start = time.time()

        try:
            # Try to connect to WebSocket
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', self.websocket_port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()

            return CheckResult(
                name="websocket",
                healthy=True,
                message=f"WebSocket server accepting connections on port {self.websocket_port}",
                duration_ms=int((time.time() - start) * 1000),
            )
        except asyncio.TimeoutError:
            return CheckResult(
                name="websocket",
                healthy=False,
                message=f"WebSocket connection timeout on port {self.websocket_port}",
                duration_ms=int((time.time() - start) * 1000),
            )
        except ConnectionRefusedError:
            return CheckResult(
                name="websocket",
                healthy=False,
                message=f"WebSocket connection refused on port {self.websocket_port}",
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as e:
            return CheckResult(
                name="websocket",
                healthy=False,
                message=f"WebSocket check failed: {e}",
                duration_ms=int((time.time() - start) * 1000),
            )

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

    def trigger_reboot(self) -> None:
        """
        Trigger system reboot as last resort.
        """
        logger.critical("Triggering system reboot due to repeated failures")

        try:
            subprocess.run(
                ["sudo", "systemctl", "reboot"],
                timeout=10,
            )
        except Exception as e:
            logger.error(f"Failed to trigger reboot: {e}")

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

        # Each service resets its own restart budget when it recovers
        if radar_result.healthy:
            self.radar_restart_count = 0
        if server_result.healthy:
            self.server_restart_count = 0

        if radar_result.healthy and server_result.healthy and disk_result.healthy:
            logger.debug(f"Health check passed: radar={radar_result.duration_ms}ms, server={server_result.duration_ms}ms")
            return HealthStatus.HEALTHY

        # Low disk is degraded (no recovery action - operator must free space)
        overall_status = HealthStatus.DEGRADED if not disk_result.healthy else HealthStatus.HEALTHY

        # ---- Radar: degraded only. NEVER reboots. ----
        if not radar_result.healthy:
            overall_status = HealthStatus.DEGRADED
            device_present = os.path.exists(self.radar_device)
            logger.warning(
                f"Radar unhealthy: {radar_result.message} "
                f"(failures={self.radar_state.consecutive_failures}, device_present={device_present})"
            )
            # Only restart the oneshot config service if the DEVICE EXISTS but is
            # faulty (a recoverable state). If the device is absent it's a power/
            # cabling issue that a restart cannot fix - cricket-radar already
            # waits for the device to appear, so churning it just wastes cycles.
            if (
                device_present
                and self.radar_state.consecutive_failures >= self.max_failures_before_restart
                and self.radar_restart_count < self.max_radar_restarts
            ):
                self.restart_service("cricket-radar.service")
                self.radar_state.recovery_actions_taken += 1
                self.radar_restart_count += 1

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
                        f"Server still down after {self.server_restart_count} restarts; "
                        f"rebooting as last resort"
                    )
                    self.trigger_reboot()

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
                        f"radar_restarts={self.radar_restart_count}, "
                        f"server_restarts={self.server_restart_count}"
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
    )

    try:
        asyncio.run(monitor.run())
    except KeyboardInterrupt:
        logger.info("Health monitor stopped by user")
        monitor.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
