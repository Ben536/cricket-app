"""
Shared serial-port helpers for the IWR6843 radar.

Used by both the recorder and the streamer so the HUPCL workaround lives in
exactly one place.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def disable_serial_hupcl(ser) -> None:
    """Disable HUPCL on an open serial port so the modem control lines (DTR/RTS)
    are NOT dropped when the port closes.

    On this radar board the CP2105's DTR line resets/stops the IWR6843 when it
    drops on close - which would silently kill the radar stream after every
    recording (the chip can't restart without a power-cycle). Keeping the lines
    asserted across open/close lets the radar keep streaming.
    """
    try:
        import termios
        attrs = termios.tcgetattr(ser.fd)
        attrs[2] &= ~termios.HUPCL  # clear HUPCL in the control flags
        termios.tcsetattr(ser.fd, termios.TCSANOW, attrs)
    except Exception as e:
        logger.warning(f"Could not disable HUPCL on serial port: {e}")


def open_radar_serial(port: str, baud_rate: int, timeout: float = 0.1):
    """Open the radar serial port with HUPCL disabled.

    Returns the open serial.Serial, or None if pyserial is missing or the port
    cannot be opened. Callers that fall back to mock data on None MUST flag the
    output as mock - mock frames must never be mistaken for real captures.
    """
    try:
        import serial
    except ImportError:
        logger.warning("pyserial not installed - radar unavailable")
        return None

    try:
        ser = serial.Serial(port, baud_rate, timeout=timeout)
    except Exception as e:
        logger.warning(f"Could not open serial port {port}: {e}")
        return None

    disable_serial_hupcl(ser)
    logger.info(f"Opened radar serial port: {port}")
    return ser
