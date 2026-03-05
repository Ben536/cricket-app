#!/usr/bin/env python3
"""
Robust radar configuration script for IWR6843.

Features:
- Waits for radar device to appear
- Retries with exponential backoff
- Structured logging for journald
- Proper exit codes for systemd
- Timeout handling

Exit codes:
- 0: Success
- 1: Configuration failed after all retries
- 2: Device never appeared (timeout)
- 3: Configuration file not found
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Configure logging for journald (stderr, structured)
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("cricket.radar.config")


# Exit codes
EXIT_SUCCESS = 0
EXIT_CONFIG_FAILED = 1
EXIT_DEVICE_TIMEOUT = 2
EXIT_FILE_NOT_FOUND = 3


@dataclass
class ConfigResult:
    """Result of a configuration attempt."""
    success: bool
    error: Optional[str] = None
    lines_sent: int = 0
    duration_ms: int = 0


def wait_for_device(
    device_path: str,
    timeout_seconds: int = 120,
    check_interval: float = 2.0,
) -> bool:
    """
    Wait for the radar device to appear.

    Args:
        device_path: Path to serial device (e.g., /dev/ttyUSB0)
        timeout_seconds: Maximum time to wait
        check_interval: Seconds between checks

    Returns:
        True if device appeared, False if timeout
    """
    logger.info(f"Waiting for device {device_path} (timeout: {timeout_seconds}s)")

    start_time = time.time()
    last_log_time = start_time

    while time.time() - start_time < timeout_seconds:
        if os.path.exists(device_path):
            elapsed = time.time() - start_time
            logger.info(f"Device {device_path} found after {elapsed:.1f}s")
            # Give the device a moment to initialize
            time.sleep(0.5)
            return True

        # Log progress every 10 seconds
        if time.time() - last_log_time >= 10:
            remaining = timeout_seconds - (time.time() - start_time)
            logger.info(f"Still waiting for {device_path}... ({remaining:.0f}s remaining)")
            last_log_time = time.time()

        time.sleep(check_interval)

    logger.error(f"Timeout waiting for device {device_path}")
    return False


def send_config(
    config_path: Path,
    device_path: str = "/dev/ttyUSB0",
    baud_rate: int = 115200,
    command_delay: float = 0.05,
    read_timeout: float = 1.0,
) -> ConfigResult:
    """
    Send configuration to radar.

    Args:
        config_path: Path to configuration file
        device_path: Serial port path
        baud_rate: Serial baud rate
        command_delay: Delay between commands (seconds)
        read_timeout: Serial read timeout (seconds)

    Returns:
        ConfigResult with success status and details
    """
    import serial

    start_time = time.time()
    lines_sent = 0

    try:
        # Read config file
        config_lines = config_path.read_text().splitlines()
        command_lines = [
            line.strip() for line in config_lines
            if line.strip() and not line.strip().startswith("%")
        ]

        logger.info(f"Loaded {len(command_lines)} commands from {config_path.name}")

        # Open serial port
        ser = serial.Serial(
            device_path,
            baud_rate,
            timeout=read_timeout,
        )

        # Clear buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(0.1)

        # Send each command
        for i, cmd in enumerate(command_lines):
            # Send command with newline
            ser.write((cmd + "\n").encode())
            lines_sent += 1

            # Wait for response
            time.sleep(command_delay)
            response = ser.read(1000).decode(errors="ignore")

            # Check for errors
            if "Error" in response:
                error_msg = f"Radar rejected command '{cmd}': {response.strip()}"
                logger.error(error_msg)
                ser.close()
                return ConfigResult(
                    success=False,
                    error=error_msg,
                    lines_sent=lines_sent,
                    duration_ms=int((time.time() - start_time) * 1000),
                )

            # Log progress for key commands
            if cmd in ("sensorStart", "sensorStop", "flushCfg"):
                logger.info(f"Sent: {cmd}")

        ser.close()

        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(f"Configuration complete: {lines_sent} commands in {duration_ms}ms")

        return ConfigResult(
            success=True,
            lines_sent=lines_sent,
            duration_ms=duration_ms,
        )

    except serial.SerialException as e:
        return ConfigResult(
            success=False,
            error=f"Serial error: {e}",
            lines_sent=lines_sent,
            duration_ms=int((time.time() - start_time) * 1000),
        )
    except Exception as e:
        return ConfigResult(
            success=False,
            error=f"Unexpected error: {e}",
            lines_sent=lines_sent,
            duration_ms=int((time.time() - start_time) * 1000),
        )


def configure_with_retry(
    config_path: Path,
    device_path: str = "/dev/ttyUSB0",
    max_retries: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
) -> bool:
    """
    Configure radar with exponential backoff retry.

    Args:
        config_path: Path to configuration file
        device_path: Serial port path
        max_retries: Maximum number of attempts
        initial_delay: Initial retry delay (seconds)
        max_delay: Maximum retry delay (seconds)
        backoff_factor: Multiplier for delay after each retry

    Returns:
        True if configuration succeeded, False otherwise
    """
    delay = initial_delay

    for attempt in range(1, max_retries + 1):
        logger.info(f"Configuration attempt {attempt}/{max_retries}")

        result = send_config(config_path, device_path)

        if result.success:
            logger.info(
                f"Radar configured successfully on attempt {attempt} "
                f"({result.lines_sent} commands, {result.duration_ms}ms)"
            )
            return True

        # Log failure
        logger.warning(
            f"Attempt {attempt} failed: {result.error} "
            f"(sent {result.lines_sent} commands)"
        )

        # Don't delay after last attempt
        if attempt < max_retries:
            logger.info(f"Retrying in {delay:.1f}s...")
            time.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)

    logger.error(f"Configuration failed after {max_retries} attempts")
    return False


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Configure IWR6843 radar with robust retry handling"
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default="/home/bdrysdale/profile_cricket.cfg",
        help="Path to radar configuration file",
    )
    parser.add_argument(
        "--device",
        default="/dev/ttyUSB0",
        help="Serial device path (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--device-timeout",
        type=int,
        default=120,
        help="Seconds to wait for device to appear (default: 120)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum configuration attempts (default: 5)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Resolve config path
    config_path = Path(args.config_file)
    if not config_path.is_absolute():
        # Try relative to home directory
        config_path = Path.home() / args.config_file

    # Check config file exists
    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        return EXIT_FILE_NOT_FOUND

    logger.info(f"Starting radar configuration: {config_path.name}")

    # Wait for device
    if not wait_for_device(args.device, timeout_seconds=args.device_timeout):
        logger.error("Radar device not found - is it connected?")
        return EXIT_DEVICE_TIMEOUT

    # Configure with retry
    if configure_with_retry(
        config_path=config_path,
        device_path=args.device,
        max_retries=args.max_retries,
    ):
        logger.info("Radar configuration successful")
        return EXIT_SUCCESS
    else:
        logger.error("Radar configuration failed")
        return EXIT_CONFIG_FAILED


if __name__ == "__main__":
    sys.exit(main())
