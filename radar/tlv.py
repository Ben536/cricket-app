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
import math
import struct
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAGIC_BYTES = bytes([0x02, 0x01, 0x04, 0x03, 0x06, 0x05, 0x08, 0x07])
HEADER_SIZE = 32  # 8 fields x 4 bytes, after the magic word

# Sanity bounds for totalPacketLen. The magic word can occur inside point
# payload bytes; locking onto such a false magic yields a garbage header.
# A zero length would stall the buffer forever (infinite busy-loop), a huge
# one would grow the buffer without bound waiting for a packet that never
# completes.
#
# The cap was 8192, which is NOT a real bound: with guiMonitor emitting point
# cloud + range profile (512B) + noise profile (512B) + stats + side info, the
# packet passes 8192 at roughly 350 detected points. Every frame beyond that was
# silently rejected as a false magic (DEBUG only) - so the busier the scene, the
# blinder the system, which is precisely backwards. An empty room already
# averages 60 points/frame; a net with batter, bowler and ground clutter reaches
# 400+. 64KB is the SDK's practical ceiling for a full point cloud.
MIN_PACKET_LENGTH = len(MAGIC_BYTES) + HEADER_SIZE
MAX_PACKET_LENGTH = 65536
MAX_BUFFER_SIZE = 262144  # hard cap on unparsed bytes; must exceed one packet

# The TI demo pads totalPacketLen up to a 32-byte boundary, so a valid frame can
# carry trailing bytes after its last TLV. Anything beyond that means the walk
# and the header disagree about where the packet ends.
TLV_PADDING_SLACK = 31

# Physical plausibility bounds for a decoded point. These are GARBAGE FILTERS,
# not tuning: the radar profile's range is 0.25-12m and its unambiguous velocity
# is well under 40 m/s, so anything out here is not a measurement. Corrupt
# frames decode arbitrary bytes as float32 and produce values like -5.1e+38 and
# NaN - both already present in the committed recordings and in the regression
# fixture. Kept deliberately loose so that re-tuning the radar profile can never
# make this reject real data.
MAX_ABS_COORD_M = 100.0
MAX_ABS_DOPPLER_MS = 200.0

LOG_EVERY_N_DROPS = 20  # rate-limit for corrupt-frame warnings


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
        # Observability: silent corruption is the failure mode this parser is
        # guarding against, so every rejection is counted rather than swallowed.
        self.frames_dropped = 0        # structurally invalid or physically implausible
        self.lengths_rejected = 0      # implausible totalPacketLen (false magic)
        self.frames_lost = 0           # gaps in the hardware frame counter
        self.gap_events = 0            # how many separate gaps (for rate-limiting)
        self._last_frame_number: int | None = None

    def _note_drop(self, reason: str, detail: str) -> None:
        self.frames_dropped += 1
        if self.frames_dropped % LOG_EVERY_N_DROPS == 1:
            logger.warning(
                f"Dropped corrupt radar frame ({reason}): {detail} "
                f"[{self.frames_dropped} dropped so far]"
            )

    def _track_frame_number(self, frame_number: int) -> None:
        """Count gaps in the hardware counter - the visible symptom of byte loss.

        RATE-LIMITED, and that is not cosmetic. This runs on the reader
        thread, and a logger.warning goes synchronously to journald, which on
        the Pi writes to the SD card. Logging every gap therefore DELAYS the
        very loop whose lateness caused the gap - each lost frame bought
        another write, which bought more lost frames. Measured on the Pi
        (2026-09-06): the radar emitted a clean 20.0 Hz while the parser
        surfaced 6.4 Hz with 162 lost and 81 corrupt frames, on an idle CPU.
        The counters below are always exact; only the logging is thinned.
        """
        prev = self._last_frame_number
        self._last_frame_number = frame_number
        if prev is None or frame_number <= prev:
            return
        missing = frame_number - prev - 1
        if missing > 0:
            self.frames_lost += missing
            self.gap_events += 1
            if self.gap_events % LOG_EVERY_N_DROPS == 1:
                logger.warning(
                    f"Radar frame gap: {prev} -> {frame_number} ({missing} lost) "
                    f"[{self.frames_lost} lost over {self.gap_events} gaps so far]"
                )

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
            num_detected = struct.unpack('<I', header[20:24])[0]
            num_tlvs = struct.unpack('<I', header[24:28])[0]

            # Sanity-check the length BEFORE trusting it. On failure this is
            # a false magic (or corruption): skip past it and resync.
            if not (MIN_PACKET_LENGTH <= total_length <= MAX_PACKET_LENGTH):
                self.lengths_rejected += 1
                if self.lengths_rejected % LOG_EVERY_N_DROPS == 1:
                    logger.warning(
                        f"Implausible packet length {total_length} "
                        f"(valid {MIN_PACKET_LENGTH}-{MAX_PACKET_LENGTH}), resyncing "
                        f"[{self.lengths_rejected} so far]"
                    )
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
            tlvs_parsed = 0
            truncated = False

            # Bounded by numTLVs, not by "bytes remain": the 32-byte padding the
            # TI demo appends parses as a run of phantom zero-length TLVs, and
            # walking into it makes every real padded frame look corrupt.
            while tlvs_parsed < num_tlvs and tlv_start + 8 <= len(packet):
                tlv_type = struct.unpack('<I', packet[tlv_start:tlv_start + 4])[0]
                tlv_length = struct.unpack('<I', packet[tlv_start + 4:tlv_start + 8])[0]

                if tlv_start + 8 + tlv_length > len(packet):
                    # A TLV claiming to run past the packet is unambiguous
                    # corruption - previously this just stopped the walk and the
                    # half-parsed frame was emitted as if valid.
                    truncated = True
                    break

                tlv_data = packet[tlv_start + 8:tlv_start + 8 + tlv_length]
                tlvs_parsed += 1

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

            # ---- Structural validation ----------------------------------
            # When bytes are lost mid-packet, `len(buffer) >= total_length` is
            # satisfied using the NEXT packet's bytes, so the tail decodes as
            # arbitrary float32 and the successor frame is consumed with it.
            # None of this was detected: numTLVs was never read, and nothing
            # checked that the walk actually reached the end of the packet.
            leftover = len(packet) - tlv_start
            if truncated:
                self._note_drop("truncated TLV", f"frame {frame_number}")
                continue
            if leftover > TLV_PADDING_SLACK:
                self._note_drop(
                    "trailing bytes",
                    f"frame {frame_number}: {leftover} bytes after the last TLV "
                    f"(slack {TLV_PADDING_SLACK})",
                )
                continue
            if tlvs_parsed != num_tlvs:
                self._note_drop(
                    "TLV count mismatch",
                    f"frame {frame_number}: header says {num_tlvs}, walked {tlvs_parsed}",
                )
                continue
            if frame.num_points != num_detected:
                self._note_drop(
                    "point count mismatch",
                    f"frame {frame_number}: header says {num_detected}, "
                    f"decoded {frame.num_points}",
                )
                continue

            # ---- Physical validation ------------------------------------
            bad = next(
                (p for p in frame.points
                 if not (math.isfinite(p.x) and math.isfinite(p.y)
                         and math.isfinite(p.z) and math.isfinite(p.doppler))
                 or abs(p.x) > MAX_ABS_COORD_M or abs(p.y) > MAX_ABS_COORD_M
                 or abs(p.z) > MAX_ABS_COORD_M or abs(p.doppler) > MAX_ABS_DOPPLER_MS),
                None,
            )
            if bad is not None:
                self._note_drop(
                    "implausible point",
                    f"frame {frame_number}: x={bad.x:.3g} y={bad.y:.3g} "
                    f"z={bad.z:.3g} doppler={bad.doppler:.3g}",
                )
                continue

            self._track_frame_number(frame_number)

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
