"""
Shared TLV parser for the TI IWR6843 radar.

One parser implementation for the whole pipeline (reader/recorder/streamer/
detector) - the previous duplicated copies in recorder.py and streamer.py had
already started to diverge.

Frame structure (TI mmWave SDK 3.x):
- Magic (8 bytes): 02 01 04 03 06 05 08 07
- Header (32 bytes after magic): version, totalPacketLen, platform,
  frameNumber, timeCpuCycles, numDetectedObj, numTLVs, subFrameNumber
- TLVs: type (4) + length (4) + data
  - Type 1 = Detected Points (x, y, z, doppler as 4 floats = 16 bytes/point)
  - Type 7 = Side Info (SNR + noise as 2 int16 = 4 bytes/point, 0.1 dB units)

totalPacketLen includes the magic word.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAGIC_BYTES = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
HEADER_SIZE = 32  # 8 fields x 4 bytes, after the magic word

# Sanity bounds for totalPacketLen. The magic word can occur inside point
# payload bytes; locking onto such a false magic yields a garbage header.
# A zero length would stall the buffer forever (infinite busy-loop), a huge
# one would grow the buffer without bound waiting for a packet that never
# completes. Real packets are 40 bytes (empty frame) to a few KB.
MIN_PACKET_LENGTH = len(MAGIC_BYTES) + HEADER_SIZE
MAX_PACKET_LENGTH = 8192
MAX_BUFFER_SIZE = 65536  # hard cap on unparsed bytes


@dataclass
class RadarPoint:
    """A single detected point from the radar."""
    x: float
    y: float
    z: float
    doppler: float  # Radial velocity in m/s
    snr: float = 0.0      # Signal-to-noise ratio (dB)
    noise: float = 0.0    # Noise level (dB)


@dataclass
class RadarFrame:
    """A single frame of radar data.

    frame_number and cpu_time_ms come from the radar hardware header - they
    are the authoritative timing for tracking (host receive time jitters by a
    whole read batch). Consumers stamp their own host times as needed.
    """
    frame_number: int
    cpu_time_ms: int
    num_points: int
    points: list[RadarPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Canonical dict (recorder/JSONL vocabulary)."""
        return {
            "frame_number": self.frame_number,
            "cpu_time_ms": self.cpu_time_ms,
            "num_points": self.num_points,
            "points": [
                {"x": p.x, "y": p.y, "z": p.z, "doppler": p.doppler, "snr": p.snr, "noise": p.noise}
                for p in self.points
            ],
        }

    def to_stream_dict(self, timestamp_ms: int) -> dict:
        """Wire dict for the live UI stream (visualizer vocabulary)."""
        return {
            "frame_number": self.frame_number,
            "timestamp_ms": timestamp_ms,
            "point_count": len(self.points),
            "points": [
                {"x": p.x, "y": p.y, "z": p.z, "v": p.doppler, "snr": p.snr, "noise": p.noise}
                for p in self.points
            ],
        }


class TLVParser:
    """
    Incremental parser: feed raw serial bytes, get complete frames back.

    Resilient to garbage: resyncs on the magic word, validates the header's
    totalPacketLen against sanity bounds (skipping past false magics), and
    caps the internal buffer so a corrupt stream can't grow it unboundedly.
    """

    def __init__(self):
        self.buffer = bytearray()

    def add_data(self, data: bytes) -> list[RadarFrame]:
        """Add data to buffer and extract complete frames."""
        self.buffer.extend(data)
        frames = []

        # A corrupt stream must never grow the buffer without bound. Keeping
        # the tail preserves any partial packet still in flight.
        if len(self.buffer) > MAX_BUFFER_SIZE:
            logger.warning(
                f"TLV buffer exceeded {MAX_BUFFER_SIZE} bytes without a "
                f"complete packet - dropping to tail (stream corrupt?)"
            )
            self.buffer = self.buffer[-MAX_PACKET_LENGTH:]

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
            if len(self.buffer) < MIN_PACKET_LENGTH:
                break

            # Parse header (fields after the magic word, little-endian)
            header_start = len(MAGIC_BYTES)
            header = self.buffer[header_start:header_start + HEADER_SIZE]

            total_length = struct.unpack('<I', header[4:8])[0]
            frame_number = struct.unpack('<I', header[12:16])[0]
            cpu_time = struct.unpack('<I', header[16:20])[0]

            # Sanity-check the length BEFORE trusting it. On failure this is
            # a false magic (or corruption): skip past it and resync.
            if not (MIN_PACKET_LENGTH <= total_length <= MAX_PACKET_LENGTH):
                logger.debug(f"Implausible packet length {total_length}, resyncing")
                self.buffer = self.buffer[len(MAGIC_BYTES):]
                continue

            # Check if we have complete packet
            if len(self.buffer) < total_length:
                break

            # Extract complete packet
            packet = bytes(self.buffer[:total_length])
            self.buffer = self.buffer[total_length:]

            frame = RadarFrame(
                frame_number=frame_number,
                cpu_time_ms=cpu_time,
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
