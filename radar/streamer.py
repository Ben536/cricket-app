"""
Radar Streamer - Streams radar data to WebSocket clients.

Reads TLV frames from radar and broadcasts to connected clients
for real-time visualization.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# TLV Magic bytes for IWR6843
MAGIC_BYTES = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
HEADER_SIZE = 40


@dataclass
class RadarPoint:
    x: float
    y: float
    z: float
    doppler: float


@dataclass
class RadarFrame:
    frame_number: int
    timestamp_ms: int
    points: list[RadarPoint]

    def to_dict(self) -> dict:
        return {
            "frame_number": self.frame_number,
            "timestamp_ms": self.timestamp_ms,
            "point_count": len(self.points),
            "points": [
                {"x": p.x, "y": p.y, "z": p.z, "v": p.doppler}
                for p in self.points
            ],
        }


class TLVParser:
    """Parser for TI radar TLV frames."""

    def __init__(self):
        self.buffer = bytearray()

    def add_data(self, data: bytes) -> list[RadarFrame]:
        self.buffer.extend(data)
        frames = []

        while True:
            magic_idx = self.buffer.find(MAGIC_BYTES)
            if magic_idx == -1:
                if len(self.buffer) > 7:
                    self.buffer = self.buffer[-7:]
                break

            if magic_idx > 0:
                self.buffer = self.buffer[magic_idx:]

            if len(self.buffer) < len(MAGIC_BYTES) + HEADER_SIZE:
                break

            header_start = len(MAGIC_BYTES)
            header = self.buffer[header_start:header_start + HEADER_SIZE]

            total_length = struct.unpack('<I', header[4:8])[0]
            frame_number = struct.unpack('<I', header[12:16])[0]
            cpu_time = struct.unpack('<I', header[16:20])[0]

            if len(self.buffer) < total_length:
                break

            packet = bytes(self.buffer[:total_length])
            self.buffer = self.buffer[total_length:]

            frame = RadarFrame(
                frame_number=frame_number,
                timestamp_ms=cpu_time,
                points=[],
            )

            tlv_start = len(MAGIC_BYTES) + HEADER_SIZE
            while tlv_start + 8 <= len(packet):
                tlv_type = struct.unpack('<I', packet[tlv_start:tlv_start + 4])[0]
                tlv_length = struct.unpack('<I', packet[tlv_start + 4:tlv_start + 8])[0]

                if tlv_start + 8 + tlv_length > len(packet):
                    break

                tlv_data = packet[tlv_start + 8:tlv_start + 8 + tlv_length]

                if tlv_type == 1:  # Detected Points
                    num_points = tlv_length // 16
                    for i in range(num_points):
                        offset = i * 16
                        x, y, z, doppler = struct.unpack(
                            '<ffff',
                            tlv_data[offset:offset + 16]
                        )
                        frame.points.append(RadarPoint(x=x, y=y, z=z, doppler=doppler))

                tlv_start += 8 + tlv_length

            frames.append(frame)

        return frames


class RadarStreamer:
    """Streams radar data to callbacks for WebSocket broadcast."""

    def __init__(
        self,
        serial_port: str = "/dev/ttyUSB1",
        baud_rate: int = 921600,
    ):
        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._parser = TLVParser()
        self._callbacks: list[Callable[[dict], None]] = []
        self._serial = None
        self._use_mock = False

    @property
    def is_streaming(self) -> bool:
        return self._running

    def add_callback(self, callback: Callable[[dict], None]):
        """Add a callback to receive frame data."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[dict], None]):
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def start(self):
        """Start streaming radar data."""
        if self._running:
            return

        self._running = True
        self._parser = TLVParser()
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()
        logger.info("Radar streamer started")

    def stop(self):
        """Stop streaming."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Radar streamer stopped")

    def _stream_loop(self):
        """Main streaming loop."""
        try:
            # Try to open serial
            try:
                import serial
                self._serial = serial.Serial(
                    self.serial_port,
                    self.baud_rate,
                    timeout=0.1,
                )
                self._use_mock = False
                logger.info(f"Opened radar on {self.serial_port}")
            except Exception as e:
                logger.warning(f"Could not open serial: {e}, using mock data")
                self._use_mock = True
                self._serial = None

            frame_count = 0
            start_time = time.time()

            while self._running:
                if self._use_mock:
                    # Generate mock data for testing
                    time.sleep(0.1)  # 10Hz
                    elapsed = time.time() - start_time
                    frame_count += 1

                    # Generate some moving points for visual feedback
                    import math
                    t = elapsed * 2
                    mock_points = []
                    # Simulate a ball moving across field of view
                    if int(elapsed) % 3 < 1:  # Every 3 seconds, show a "ball"
                        ball_x = math.sin(t) * 2
                        ball_y = 3 + math.cos(t * 0.5)
                        ball_v = 15 + math.sin(t) * 5
                        mock_points.append({"x": ball_x, "y": ball_y, "z": 0.5, "v": ball_v})

                    # Some noise points
                    import random
                    for _ in range(random.randint(0, 3)):
                        mock_points.append({
                            "x": random.uniform(-3, 3),
                            "y": random.uniform(0, 6),
                            "z": random.uniform(0, 2),
                            "v": random.uniform(-2, 2),
                        })

                    frame_data = {
                        "frame_number": frame_count,
                        "timestamp_ms": int(elapsed * 1000),
                        "point_count": len(mock_points),
                        "points": mock_points,
                    }
                    self._broadcast(frame_data)
                else:
                    # Real radar data
                    data = self._serial.read(4096)
                    if data:
                        frames = self._parser.add_data(data)
                        for frame in frames:
                            self._broadcast(frame.to_dict())

        except Exception as e:
            logger.error(f"Streamer error: {e}")
        finally:
            if self._serial:
                self._serial.close()
                self._serial = None

    def _broadcast(self, frame_data: dict):
        """Send frame to all callbacks."""
        for callback in self._callbacks:
            try:
                callback(frame_data)
            except Exception as e:
                logger.error(f"Callback error: {e}")


# Singleton
_streamer: Optional[RadarStreamer] = None


def get_streamer() -> RadarStreamer:
    global _streamer
    if _streamer is None:
        _streamer = RadarStreamer()
    return _streamer
