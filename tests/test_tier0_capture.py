"""
Tier 0: the capture path must not corrupt, drop or lose the data it exists to
collect. (2026-08 review, T0.1-T0.4.)

These pin four faults that made the next data-gathering trip worthless:

T0.1  A packet truncated by lost bytes was completed with the NEXT packet's
      bytes, so its tail decoded as arbitrary float32 and its successor was
      eaten. Nothing checked it: numTLVs was never read and the TLV walk was
      never required to reach the end of the packet. The committed recordings
      contain the result (coordinates near -5e38, NaNs), including in the
      regression fixture.
T0.2  Subscribers ran inline on the reader thread, so the recorder's blocking
      SD-card write stalled the UART and lost bytes mid-packet - which is what
      caused T0.1 in the first place.
T0.3  MAX_PACKET_LENGTH was 8192, below a full point cloud, so the system went
      blind exactly when the scene got busy.
T0.4  flush() without fsync loses ~30s on a battery cut, and nothing checked
      free space.
"""

from __future__ import annotations

import math
import struct
import time

import pytest

from radar.recorder import (
    BYTES_PER_SECOND_ESTIMATE,
    DISK_HEADROOM_BYTES,
    RadarRecorder,
)
from radar.reader import FRAME_QUEUE_MAX, RadarSource
from radar.tlv import (
    MAGIC_BYTES,
    MAX_ABS_COORD_M,
    MAX_PACKET_LENGTH,
    TLVParser,
)


# ---------------------------------------------------------------------------
# Frame construction - build real TI-layout bytes rather than mocking the parser
# ---------------------------------------------------------------------------

def build_packet(points, frame_number=1, num_tlvs=None, pad=0, total_length=None,
                 extra_tlvs=()):
    """Assemble a valid IWR6843 frame. Overrides let a test corrupt one field.

    `extra_tlvs` is a list of (type, payload_length) for the other TLVs the
    configured guiMonitor emits - needed to reach realistic packet sizes.
    """
    payload = b"".join(struct.pack("<ffff", *p) for p in points)
    body = struct.pack("<II", 1, len(payload)) + payload
    for tlv_type, tlv_len in extra_tlvs:
        body += struct.pack("<II", tlv_type, tlv_len) + b"\x00" * tlv_len
    body += b"\x00" * pad
    declared_tlvs = (1 + len(extra_tlvs)) if num_tlvs is None else num_tlvs

    header = struct.pack(
        "<IIIIIIII",
        0x03060005,                 # version
        0,                          # totalPacketLen - patched below
        0x6843,                     # platform
        frame_number,
        123456,                     # timeCpuCycles
        len(points),                # numDetectedObj
        declared_tlvs,              # numTLVs
        0,                          # subFrameNumber
    )
    packet = MAGIC_BYTES + header + body
    length = len(packet) if total_length is None else total_length
    return packet[:12] + struct.pack("<I", length) + packet[16:]


GOOD = [(1.0, 3.0, 0.5, 12.0), (-0.5, 4.2, 0.9, -3.5)]


def test_valid_frame_still_parses():
    """Guard against the validation rejecting real data."""
    frames = TLVParser().add_data(build_packet(GOOD))
    assert len(frames) == 1
    assert frames[0].num_points == 2
    assert frames[0].frame_number == 1


def test_valid_frame_with_32_byte_padding_still_parses():
    """
    The TI demo pads totalPacketLen to a 32-byte boundary, so trailing bytes
    after the last TLV are legal. Requiring exact consumption would drop every
    real frame - the reason TLV_PADDING_SLACK exists.
    """
    frames = TLVParser().add_data(build_packet(GOOD, pad=31))
    assert len(frames) == 1, "padded frame must not be rejected"


# ---------------------------------------------------------------------------
# T0.1 - structural corruption
# ---------------------------------------------------------------------------

def test_truncated_tlv_is_dropped_not_emitted():
    """A TLV claiming to run past the packet used to emit a half-parsed frame."""
    p = bytearray(build_packet(GOOD))
    tlv_len_at = len(MAGIC_BYTES) + 32 + 4
    p[tlv_len_at:tlv_len_at + 4] = struct.pack("<I", 9999)  # longer than the packet

    parser = TLVParser()
    assert parser.add_data(bytes(p)) == []
    assert parser.frames_dropped == 1


def test_tlv_count_mismatch_is_dropped():
    """numTLVs is now read and enforced; it was previously ignored entirely."""
    parser = TLVParser()
    assert parser.add_data(build_packet(GOOD, num_tlvs=3)) == []
    assert parser.frames_dropped == 1


def test_trailing_bytes_beyond_padding_slack_are_dropped():
    parser = TLVParser()
    assert parser.add_data(build_packet(GOOD, pad=64)) == []
    assert parser.frames_dropped == 1


def test_byte_loss_is_either_rejected_or_reported_as_a_gap():
    """
    The real-world case: chop the tail off packet A, so its declared length is
    satisfied by bytes belonging to packet B - A's tail decodes as garbage and B
    is consumed with it.

    HONEST LIMIT: structural validation cannot catch every such pattern. If the
    substituted bytes happen to form a self-consistent tail (B's magic+header
    decode to small floats, and the TLV/point counts still agree), the frame
    passes every check. What must NOT happen is silence - the eaten frame shows
    up as a gap in the hardware counter. Closing the gap properly is T0.2's job:
    stop stalling the UART so the bytes are never lost.
    """
    a = build_packet([(1.0, 3.0, 0.5, 12.0)] * 8, frame_number=100)
    b = build_packet(GOOD, frame_number=101)
    c = build_packet(GOOD, frame_number=102)
    stream = a[:-40] + b + c        # 40 bytes of A lost in transit

    parser = TLVParser()
    frames = parser.add_data(stream)
    numbers = [f.frame_number for f in frames]

    # Frame 101 was eaten either way; the loss must be visible, not silent.
    assert 101 not in numbers
    assert parser.frames_dropped >= 1 or parser.frames_lost >= 1, (
        "byte loss produced neither a dropped frame nor a counted gap"
    )
    # Whatever survives must still be physically plausible.
    for f in frames:
        for pt in f.points:
            assert math.isfinite(pt.x) and abs(pt.x) <= MAX_ABS_COORD_M
            assert math.isfinite(pt.doppler)


@pytest.mark.parametrize("bad_point", [
    (float("nan"), 3.0, 0.5, 12.0),
    (float("inf"), 3.0, 0.5, 12.0),
    (-1.198e38, 3.0, 0.5, 12.0),       # from the committed recordings
    (-2.683e31, 3.0, 0.5, 12.0),       # ditto
    (1.0, -509726.03, 0.5, 12.0),      # the actual value in the committed recordings
    (1.0, 3.0, 0.5, 1e30),             # implausible doppler
])
def test_implausible_points_drop_the_frame(bad_point):
    parser = TLVParser()
    assert parser.add_data(build_packet([bad_point])) == []
    assert parser.frames_dropped == 1


def test_frame_number_gaps_are_counted():
    """Lost frames were previously invisible; they are the symptom of byte loss."""
    parser = TLVParser()
    parser.add_data(build_packet(GOOD, frame_number=10))
    parser.add_data(build_packet(GOOD, frame_number=14))
    assert parser.frames_lost == 3
    assert parser.gap_events == 1


def test_gap_logging_is_rate_limited_but_the_count_is_exact(caplog):
    """The gap warning runs on the READER thread and journald writes it to
    the SD card, so logging every gap delays the loop whose lateness caused
    the gap - a feedback loop that turned a 20Hz stream into 6.4Hz on the Pi.
    Thin the logging; never the counters."""
    import logging

    parser = TLVParser()
    with caplog.at_level(logging.WARNING, logger="radar.tlv"):
        for i in range(1, 61):
            parser.add_data(build_packet(GOOD, frame_number=i * 3))  # a gap every frame

    assert parser.gap_events == 59, "every gap must still be counted"
    assert parser.frames_lost == 59 * 2
    gap_logs = [r for r in caplog.records if "frame gap" in r.message]
    assert len(gap_logs) <= 4, f"{len(gap_logs)} log writes for 59 gaps - not rate limited"
    assert gap_logs, "the first gap should still be reported"


def test_parser_recovers_after_corruption():
    """A dropped frame must not desync the stream."""
    parser = TLVParser()
    parser.add_data(build_packet(GOOD, num_tlvs=9))          # corrupt
    frames = parser.add_data(build_packet(GOOD, frame_number=2))
    assert len(frames) == 1 and frames[0].frame_number == 2


# ---------------------------------------------------------------------------
# T0.3 - packet size ceiling
# ---------------------------------------------------------------------------

# The frame shape `guiMonitor -1 1 1 1 0 0 1` actually produces alongside the
# point cloud: range profile, noise profile, stats, and side info (4 bytes/point).
def _gui_monitor_tlvs(n_points):
    return [(2, 512), (3, 512), (6, 32), (7, 4 * n_points)]


def test_full_point_cloud_is_not_rejected():
    """
    400 points plus the other guiMonitor TLVs is 9,128 bytes - past the old 8192
    cap, so the parser treated it as a false magic and silently discarded EVERY
    frame once the scene got busy. An empty room already averages 60
    points/frame; a net with batter, bowler and clutter reaches 400+.
    """
    assert MAX_PACKET_LENGTH >= 65536
    big = [(1.0, 3.0, 0.5, 12.0)] * 400
    packet = build_packet(big, extra_tlvs=_gui_monitor_tlvs(400))

    assert len(packet) > 8192, (
        f"test is not exercising the old cap: packet is only {len(packet)} bytes"
    )

    frames = TLVParser().add_data(packet)
    assert len(frames) == 1
    assert frames[0].num_points == 400
    # Side info rides along in the same packet and must still be applied.
    assert all(p.snr == 0.0 for p in frames[0].points)


def test_realistic_frame_with_all_guimonitor_tlvs_parses():
    """A normal 60-point frame with every configured TLV present."""
    pts = [(1.0, 3.0, 0.5, 12.0)] * 60
    frames = TLVParser().add_data(build_packet(pts, extra_tlvs=_gui_monitor_tlvs(60)))
    assert len(frames) == 1 and frames[0].num_points == 60


def test_implausible_length_still_resyncs_and_is_counted():
    p = bytearray(build_packet(GOOD))
    p[12:16] = struct.pack("<I", 2 ** 31)      # absurd totalPacketLen
    parser = TLVParser()
    parser.add_data(bytes(p))
    assert parser.lengths_rejected >= 1


# ---------------------------------------------------------------------------
# T0.2 - the reader must not be stalled by a slow subscriber
# ---------------------------------------------------------------------------

def test_slow_subscriber_does_not_block_the_producer():
    """
    The whole point: dispatch happens on its own thread behind a bounded queue,
    so a subscriber doing a blocking SD-card write cannot stall the serial read
    loop. Previously _dispatch called subscribers inline.
    """
    source = RadarSource()
    delivered = []

    def slow(frame):
        time.sleep(0.05)
        delivered.append(frame)

    source.subscribe(slow)
    try:
        start = time.time()
        for i in range(20):
            source._dispatch(_frame(i))
        elapsed = time.time() - start

        # 20 frames x 50ms = 1.0s if inline. Enqueueing must be ~instant.
        assert elapsed < 0.2, f"producer blocked for {elapsed:.2f}s"
    finally:
        source.unsubscribe(slow)


def test_backpressure_drops_frames_and_counts_them():
    """Overflow must lose whole frames (visible) rather than bytes (silent)."""
    source = RadarSource()

    def wedged(frame):
        time.sleep(10)

    source.subscribe(wedged)
    try:
        for i in range(FRAME_QUEUE_MAX + 30):
            source._dispatch(_frame(i))
        assert source.frames_dropped_backpressure > 0
        assert source._frame_queue.qsize() <= FRAME_QUEUE_MAX
    finally:
        source._stop_event.set()


def test_frames_reach_subscribers_through_the_queue():
    source = RadarSource()
    got = []
    source.subscribe(got.append)
    try:
        source._dispatch(_frame(7))
        deadline = time.time() + 2.0
        while not got and time.time() < deadline:
            time.sleep(0.01)
        assert [f.frame_number for f in got] == [7]
    finally:
        source.unsubscribe(got.append)


def _frame(n):
    from radar.tlv import RadarFrame
    return RadarFrame(frame_number=n, cpu_time_ms=0, num_points=0, points=[])


# ---------------------------------------------------------------------------
# T0.4 - durability and capacity
# ---------------------------------------------------------------------------

def test_recording_refuses_to_start_without_room(tmp_path, monkeypatch):
    """A card that fills mid-session stops writing while the UI says 'recording'."""
    rec = RadarRecorder(recordings_dir=str(tmp_path))

    class Usage:
        free = 10 * 1024 * 1024      # 10MB

    monkeypatch.setattr("shutil.disk_usage", lambda _p: Usage)
    with pytest.raises(ValueError, match="Not enough disk space"):
        rec.start_recording("both", max_duration_seconds=7200)


def test_recording_starts_when_there_is_room(tmp_path, monkeypatch):
    rec = RadarRecorder(recordings_dir=str(tmp_path))

    class Usage:
        free = DISK_HEADROOM_BYTES + 60 * BYTES_PER_SECOND_ESTIMATE + 1

    monkeypatch.setattr("shutil.disk_usage", lambda _p: Usage)
    session = rec.start_recording("both", max_duration_seconds=60)
    try:
        assert session is not None
    finally:
        rec.stop_recording()


def test_frames_are_not_flushed_one_by_one(tmp_path, monkeypatch):
    """A flush() per frame is a write syscall to the SD card and it starved
    the reader thread of the serial port: 5.6 Hz recorded vs 14.0 Hz when
    flushing only at the fsync cadence (Pi, 2026-09-06). Durability is
    unaffected - fsync, not flush, is what survives a power cut."""
    rec = RadarRecorder(recordings_dir=str(tmp_path))
    flushes = []

    class Usage:
        free = 10 ** 12

    monkeypatch.setattr("shutil.disk_usage", lambda _p: Usage)
    monkeypatch.setattr("os.fsync", lambda fd: None)
    rec.start_recording("both", max_duration_seconds=30)
    try:
        real_flush = rec._jsonl.flush
        monkeypatch.setattr(rec._jsonl, "flush", lambda: (flushes.append(1), real_flush())[1])
        rec._last_fsync = time.time()          # interval NOT elapsed
        for i in range(25):
            rec._write_line({"type": "frame", "t_ms": i})
        assert flushes == [], f"{len(flushes)} flushes for 25 frames - back to one per frame"
    finally:
        rec.stop_recording()


def test_frames_are_fsynced_not_just_flushed(tmp_path, monkeypatch):
    """flush() only reaches the page cache; a battery cut loses it."""
    rec = RadarRecorder(recordings_dir=str(tmp_path))
    synced = []
    monkeypatch.setattr("os.fsync", lambda fd: synced.append(fd))

    class Usage:
        free = 10 ** 12

    monkeypatch.setattr("shutil.disk_usage", lambda _p: Usage)
    rec.start_recording("both", max_duration_seconds=30)
    try:
        synced.clear()
        rec._last_fsync = 0.0            # force the interval to have elapsed
        rec._write_line({"type": "frame", "t_ms": 1})
        assert synced, "no fsync issued"
    finally:
        rec.stop_recording()
