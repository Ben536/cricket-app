"""
Radar Recorder - Captures and stores raw radar TLV data.

Records radar frames to JSON files organized by session type
(bowling, batting, both) for later analysis.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# TLV Magic bytes for IWR6843
MAGIC_BYTES = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
HEADER_SIZE = 32  # 8 fields × 4 bytes each

# Recording limits
MAX_RECORDING_SECONDS = 15
FRAME_RATE_HZ = 10  # Radar outputs at 10Hz


@dataclass
class RadarPoint:
    """A single detected point from the radar."""
    x: float
    y: float
    z: float
    doppler: float  # Velocity in m/s
    snr: float = 0.0      # Signal-to-noise ratio (dB)
    noise: float = 0.0    # Noise level (dB)


@dataclass
class RadarFrame:
    """A single frame of radar data."""
    frame_number: int
    timestamp_ms: int
    num_points: int
    points: list[RadarPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "frame_number": self.frame_number,
            "timestamp_ms": self.timestamp_ms,
            "num_points": self.num_points,
            "points": [
                {"x": p.x, "y": p.y, "z": p.z, "doppler": p.doppler, "snr": p.snr, "noise": p.noise}
                for p in self.points
            ],
        }


@dataclass
class RecordingSession:
    """Metadata and frames for a recording session."""
    session_type: str  # "bowling", "batting", or "both"
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    frame_count: int = 0
    frames: list[RadarFrame] = field(default_factory=list)
    file_path: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "session_type": self.session_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "frame_count": self.frame_count,
            "frames": [f.to_dict() for f in self.frames],
        }


class TLVParser:
    """
    Parser for TI radar TLV (Type-Length-Value) frames.

    Frame structure:
    - Magic (8 bytes): 02 01 04 03 06 05 08 07
    - Header (40 bytes):
      - Bytes 0-3: Version
      - Bytes 4-7: Total packet length
      - Bytes 8-11: Platform (0x6843 for IWR6843)
      - Bytes 12-15: Frame number
      - Bytes 16-19: CPU time
      - Bytes 20-23: Number of detected objects
      - Bytes 24-27: Number of TLVs
    - TLVs (variable):
      - Type (4 bytes)
      - Length (4 bytes)
      - Data (Length bytes)

    TLV Type 1 = Detected Points (each point is 16 bytes: x, y, z, doppler as floats)
    """

    def __init__(self):
        self.buffer = bytearray()

    def add_data(self, data: bytes) -> list[RadarFrame]:
        """Add data to buffer and extract complete frames."""
        self.buffer.extend(data)
        frames = []

        while True:
            # Find magic bytes
            magic_idx = self.buffer.find(MAGIC_BYTES)
            if magic_idx == -1:
                # No magic found, keep last 7 bytes (partial magic possible)
                if len(self.buffer) > 7:
                    self.buffer = self.buffer[-7:]
                break

            # Discard data before magic
            if magic_idx > 0:
                self.buffer = self.buffer[magic_idx:]

            # Check if we have enough for header
            if len(self.buffer) < len(MAGIC_BYTES) + HEADER_SIZE:
                break

            # Parse header
            header_start = len(MAGIC_BYTES)
            header = self.buffer[header_start:header_start + HEADER_SIZE]

            # Extract header fields (little-endian)
            version = struct.unpack('<I', header[0:4])[0]
            total_length = struct.unpack('<I', header[4:8])[0]
            platform = struct.unpack('<I', header[8:12])[0]
            frame_number = struct.unpack('<I', header[12:16])[0]
            cpu_time = struct.unpack('<I', header[16:20])[0]
            num_detected = struct.unpack('<I', header[20:24])[0]
            num_tlvs = struct.unpack('<I', header[24:28])[0]

            # Check if we have complete packet
            if len(self.buffer) < total_length:
                break

            # Extract complete packet
            packet = bytes(self.buffer[:total_length])
            self.buffer = self.buffer[total_length:]

            # Parse TLVs
            frame = RadarFrame(
                frame_number=frame_number,
                timestamp_ms=cpu_time,
                num_points=0,
                points=[],
            )

            # First pass: collect all TLV data
            tlv_start = len(MAGIC_BYTES) + HEADER_SIZE
            side_info_data = None

            while tlv_start + 8 <= len(packet):
                tlv_type = struct.unpack('<I', packet[tlv_start:tlv_start + 4])[0]
                tlv_length = struct.unpack('<I', packet[tlv_start + 4:tlv_start + 8])[0]

                if tlv_start + 8 + tlv_length > len(packet):
                    break

                tlv_data = packet[tlv_start + 8:tlv_start + 8 + tlv_length]

                # Type 1 = Detected Points
                if tlv_type == 1:
                    # Each point is 16 bytes: x, y, z, doppler (4 floats)
                    num_points = tlv_length // 16
                    for i in range(num_points):
                        offset = i * 16
                        x, y, z, doppler = struct.unpack(
                            '<ffff',
                            tlv_data[offset:offset + 16]
                        )
                        frame.points.append(RadarPoint(x=x, y=y, z=z, doppler=doppler))
                    frame.num_points = len(frame.points)

                # Type 7 = Side Info (SNR + Noise per point)
                elif tlv_type == 7:
                    side_info_data = tlv_data

                tlv_start += 8 + tlv_length

            # Second pass: add SNR/noise to points if available
            if side_info_data and len(frame.points) > 0:
                # Each point has: SNR (int16) + Noise (int16) = 4 bytes
                num_side_info = len(side_info_data) // 4
                for i in range(min(num_side_info, len(frame.points))):
                    offset = i * 4
                    snr_raw, noise_raw = struct.unpack('<hh', side_info_data[offset:offset + 4])
                    # Convert from 0.1 dB units to dB
                    frame.points[i].snr = snr_raw * 0.1
                    frame.points[i].noise = noise_raw * 0.1

            frames.append(frame)

        return frames


class RadarRecorder:
    """
    Records radar data to files.

    Manages recording sessions with start/stop control and auto-stop
    after MAX_RECORDING_SECONDS.
    """

    def __init__(
        self,
        recordings_dir: str = "recordings",
        serial_port: str = "/dev/ttyUSB1",
        baud_rate: int = 921600,
    ):
        self.recordings_dir = Path(recordings_dir)
        self.serial_port = serial_port
        self.baud_rate = baud_rate

        # Ensure directories exist
        for session_type in ["bowling", "batting", "both"]:
            (self.recordings_dir / session_type).mkdir(parents=True, exist_ok=True)

        # Recording state
        self._current_session: Optional[RecordingSession] = None
        self._recording_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._start_time: Optional[float] = None
        self._serial = None
        self._parser = TLVParser()

        # Callbacks
        self._on_progress: Optional[Callable[[int, int], None]] = None
        self._on_stopped: Optional[Callable[[RecordingSession], None]] = None

        logger.info(f"RadarRecorder initialized, recordings_dir={self.recordings_dir}")

    @property
    def is_recording(self) -> bool:
        return self._current_session is not None

    @property
    def current_session(self) -> Optional[RecordingSession]:
        return self._current_session

    def set_callbacks(
        self,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_stopped: Optional[Callable[[RecordingSession], None]] = None,
    ):
        """Set callbacks for recording events."""
        self._on_progress = on_progress
        self._on_stopped = on_stopped

    def start_recording(self, session_type: str) -> RecordingSession:
        """
        Start a new recording session.

        Args:
            session_type: One of "bowling", "batting", or "both"

        Returns:
            The new RecordingSession

        Raises:
            ValueError: If already recording or invalid session type
            RuntimeError: If serial port cannot be opened
        """
        if self.is_recording:
            raise ValueError("Already recording")

        if session_type not in ["bowling", "batting", "both"]:
            raise ValueError(f"Invalid session type: {session_type}")

        # Create session
        start_time = datetime.now(timezone.utc)
        self._current_session = RecordingSession(
            session_type=session_type,
            start_time=start_time.isoformat(),
        )

        # Reset parser
        self._parser = TLVParser()
        self._stop_event.clear()
        self._start_time = time.time()

        # Start recording thread
        self._recording_thread = threading.Thread(
            target=self._recording_loop,
            daemon=True,
        )
        self._recording_thread.start()

        logger.info(f"Started recording: type={session_type}")
        return self._current_session

    def stop_recording(self) -> Optional[RecordingSession]:
        """
        Stop the current recording and save to file.

        Returns:
            The completed RecordingSession, or None if not recording
        """
        if not self.is_recording:
            return None

        # Signal thread to stop
        self._stop_event.set()

        # Wait for thread to finish
        if self._recording_thread:
            self._recording_thread.join(timeout=2.0)
            self._recording_thread = None

        # Finalize session
        session = self._finalize_session()

        logger.info(
            f"Stopped recording: type={session.session_type}, "
            f"frames={session.frame_count}, duration={session.duration_seconds:.1f}s"
        )

        return session

    def _recording_loop(self):
        """Main recording loop - runs in separate thread."""
        try:
            # Try to open serial port
            try:
                import serial
                self._serial = serial.Serial(
                    self.serial_port,
                    self.baud_rate,
                    timeout=0.1,
                )
                logger.info(f"Opened serial port: {self.serial_port}")
            except ImportError:
                logger.warning("pyserial not installed, using mock data")
                self._serial = None
            except Exception as e:
                logger.warning(f"Could not open serial port: {e}, using mock data")
                self._serial = None

            frame_count = 0
            last_progress_time = time.time()

            while not self._stop_event.is_set():
                elapsed = time.time() - self._start_time

                # Auto-stop after max duration
                if elapsed >= MAX_RECORDING_SECONDS:
                    logger.info("Max recording duration reached, auto-stopping")
                    break

                # Read data
                if self._serial:
                    data = self._serial.read(4096)
                    if data:
                        frames = self._parser.add_data(data)
                        for frame in frames:
                            frame.timestamp_ms = int(elapsed * 1000)
                            self._current_session.frames.append(frame)
                            frame_count += 1
                else:
                    # Mock data for testing without radar
                    time.sleep(0.1)  # 10Hz
                    mock_frame = RadarFrame(
                        frame_number=frame_count,
                        timestamp_ms=int(elapsed * 1000),
                        num_points=0,
                        points=[],
                    )
                    self._current_session.frames.append(mock_frame)
                    frame_count += 1

                # Progress callback (every second)
                if self._on_progress and time.time() - last_progress_time >= 1.0:
                    self._on_progress(int(elapsed), frame_count)
                    last_progress_time = time.time()

        except Exception as e:
            logger.error(f"Recording error: {e}")

        finally:
            if self._serial:
                self._serial.close()
                self._serial = None

            # Finalize if auto-stopped
            if self._current_session and not self._stop_event.is_set():
                session = self._finalize_session()
                if self._on_stopped:
                    self._on_stopped(session)

    def _finalize_session(self) -> RecordingSession:
        """Finalize and save the current session."""
        session = self._current_session
        if not session:
            raise RuntimeError("No session to finalize")

        # Set end time and duration
        end_time = datetime.now(timezone.utc)
        session.end_time = end_time.isoformat()
        session.duration_seconds = time.time() - self._start_time
        session.frame_count = len(session.frames)

        # Generate filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}.json"
        file_path = self.recordings_dir / session.session_type / filename

        # Save to file
        try:
            with open(file_path, 'w') as f:
                json.dump(session.to_dict(), f, indent=2)
            session.file_path = str(file_path)
            logger.info(f"Saved recording to {file_path}")
        except Exception as e:
            logger.error(f"Failed to save recording: {e}")

        # Clear current session
        self._current_session = None
        self._start_time = None

        return session

    def get_recording_counts(self) -> dict[str, int]:
        """Get count of recordings by session type."""
        counts = {}
        for session_type in ["bowling", "batting", "both"]:
            dir_path = self.recordings_dir / session_type
            if dir_path.exists():
                counts[session_type] = len(list(dir_path.glob("*.json")))
            else:
                counts[session_type] = 0
        return counts

    def list_recordings(self, session_type: Optional[str] = None) -> list[dict]:
        """List all recordings, optionally filtered by type."""
        recordings = []
        types = [session_type] if session_type else ["bowling", "batting", "both"]

        for st in types:
            dir_path = self.recordings_dir / st
            if not dir_path.exists():
                continue

            for file_path in sorted(dir_path.glob("*.json"), reverse=True):
                try:
                    with open(file_path) as f:
                        data = json.load(f)
                    recordings.append({
                        "file": str(file_path),
                        "session_type": data.get("session_type", st),
                        "start_time": data.get("start_time"),
                        "duration_seconds": data.get("duration_seconds", 0),
                        "frame_count": data.get("frame_count", 0),
                    })
                except Exception as e:
                    logger.warning(f"Could not read {file_path}: {e}")

        return recordings


# Singleton instance for server use
_recorder: Optional[RadarRecorder] = None


def get_recorder() -> RadarRecorder:
    """Get or create the global RadarRecorder instance."""
    global _recorder
    if _recorder is None:
        # Use recordings dir relative to cricket-app
        recordings_dir = Path(__file__).parent.parent / "recordings"
        _recorder = RadarRecorder(recordings_dir=str(recordings_dir))
    return _recorder
