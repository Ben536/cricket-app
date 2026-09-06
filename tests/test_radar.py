"""Radar stack: TLV hardening, reader fan-out, recorder lifecycle."""

import json
import struct
import tempfile
import threading
import time

from radar.reader import RadarSource
from radar.recorder import RadarRecorder
from radar.streamer import RadarStreamer
from radar.tlv import MAGIC_BYTES, TLVParser


def make_packet(frame_number=1, cpu_time=100, points=()):
    tlv_data = b"".join(struct.pack("<ffff", *p) for p in points)
    tlvs = struct.pack("<II", 1, len(tlv_data)) + tlv_data if points else b""
    total = len(MAGIC_BYTES) + 32 + len(tlvs)
    header = struct.pack("<8I", 0x0304, total, 0x6843, frame_number, cpu_time,
                         len(points), 1 if points else 0, 0)
    return MAGIC_BYTES + header + tlvs


class TestTLVParser:
    def test_parses_valid_packet(self):
        frames = TLVParser().add_data(make_packet(7, 500, [(1.0, 2.0, 0.5, 15.0)]))
        assert len(frames) == 1
        assert frames[0].frame_number == 7
        assert frames[0].points[0].doppler == 15.0

    def test_zero_length_packet_does_not_hang(self):
        """A corrupt header with totalPacketLen=0 used to busy-loop forever."""
        bad = MAGIC_BYTES + struct.pack("<8I", 0x0304, 0, 0x6843, 1, 1, 0, 0, 0)
        parser = TLVParser()
        result = []
        t = threading.Thread(
            target=lambda: result.append(parser.add_data(bad + make_packet(9, 600))),
            daemon=True,
        )
        t.start()
        t.join(timeout=3)
        assert not t.is_alive(), "parser hung on zero-length packet"
        assert result[0][0].frame_number == 9  # resynced past the false magic

    def test_huge_length_skipped(self):
        huge = MAGIC_BYTES + struct.pack("<8I", 0x0304, 0x40000000, 0x6843, 1, 1, 0, 0, 0)
        frames = TLVParser().add_data(huge + make_packet(11, 700))
        assert len(frames) == 1 and frames[0].frame_number == 11

    def test_buffer_capped_on_garbage(self):
        parser = TLVParser()
        for _ in range(40):
            parser.add_data(b"\x00" * 4096)
        assert len(parser.buffer) <= 65536 + 4096


class TestRadarStack:
    def test_fanout_and_lifecycle(self):
        src = RadarSource(serial_port="/dev/nonexistent")
        a, b = [], []
        src.subscribe(a.append)
        src.subscribe(b.append)
        time.sleep(0.6)
        assert src.is_running and src.is_mock
        assert len(a) >= 3 and len(b) >= 3
        src.unsubscribe(a.append)
        src.unsubscribe(b.append)
        time.sleep(0.3)
        assert not src.is_running

    def test_concurrent_record_and_stream(self):
        """The workflow that used to corrupt both TLV streams."""
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        recorder = RadarRecorder(recordings_dir=tmp, source=src)
        streamer = RadarStreamer(source=src)

        stream_frames = []
        streamer.add_callback(stream_frames.append)
        streamer.start()
        session = recorder.start_recording("both", max_duration_seconds=3)
        assert session.is_mock is True
        time.sleep(0.8)
        recorder.add_annotation({"direction_deg": 42.5})
        done = recorder.stop_recording()

        assert done.frame_count > 4 and done.annotation_count == 1
        assert len(stream_frames) > 4
        assert streamer.is_streaming, "stopping the recorder must not stop the stream"
        streamer.stop()
        assert not src.is_running

        lines = [json.loads(l) for l in open(done.file_path)]
        assert lines[0]["type"] == "meta" and lines[0]["mock"] is True
        frame = next(l for l in lines if l["type"] == "frame")
        assert {"t_ms", "frame_number", "cpu_time_ms"} <= set(frame)
        assert lines[-1]["type"] == "end"

    def test_auto_stop_and_idempotent_stop(self):
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        recorder = RadarRecorder(recordings_dir=tmp, source=src)
        recorder.start_recording("racket", max_duration_seconds=1)
        time.sleep(1.5)
        assert not recorder.is_recording, "auto-stop should have fired"
        again = recorder.stop_recording()  # manual stop after auto-stop
        assert again is not None and again.session_type == "racket"
        summary = recorder.list_recordings("racket")[0]
        assert "incomplete" not in summary and summary["frame_count"] > 3

    def test_concurrent_starts_admit_exactly_one(self):
        """T1.7: two clients starting at once used to BOTH build a session on
        the singleton (leaked handle, two timers, cross-stopped recordings)."""
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        rec = RadarRecorder(recordings_dir=tmp, source=src)
        results = []
        barrier = threading.Barrier(4)

        def go():
            barrier.wait()
            try:
                results.append(("ok", rec.start_recording("both", max_duration_seconds=5)))
            except ValueError as e:
                results.append(("err", str(e)))

        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        oks = [r for r in results if r[0] == "ok"]
        errs = [r for r in results if r[0] == "err"]
        assert len(oks) == 1 and len(errs) == 3, results
        assert all("Already recording" in e for _, e in errs)
        assert rec._auto_stop_timer is not None
        rec.stop_recording()
        assert not rec.is_recording

    def test_stale_auto_stop_timer_cannot_end_the_next_recording(self):
        """A timer that fired while a manual stop held the lock ran AFTER it,
        and if a new recording had started meanwhile it stopped THAT one."""
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        rec = RadarRecorder(recordings_dir=tmp, source=src)
        first = rec.start_recording("both", max_duration_seconds=60)
        rec.stop_recording()
        second = rec.start_recording("both", max_duration_seconds=60)
        rec._auto_stop(first)  # the stale timer's callback, late
        assert rec.is_recording and rec.current_session is second
        rec._auto_stop(second)  # the right one still works
        assert not rec.is_recording

    def test_annotation_keys_cannot_override_record_type_or_clock(self):
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        rec = RadarRecorder(recordings_dir=tmp, source=src)
        rec.start_recording("both", max_duration_seconds=10)
        ann = rec.add_annotation({"type": "frame", "t_ms": "later", "direction_deg": 12.0})
        assert ann["type"] == "annotation" and isinstance(ann["t_ms"], int)
        assert ann["direction_deg"] == 12.0
        done = rec.stop_recording()
        assert done.annotation_count == 1

    def test_restart_after_stop_leaves_exactly_one_generation(self):
        """unsubscribe() sets the stop event and joins OUTSIDE the lock; a
        concurrent subscribe() used to clear the same event and start new
        threads while the old ones were still running - two dispatch
        threads, and with a real port two readers on one tty.

        Asserts on THIS source's own threads rather than every thread named
        'radar-*' in the process: other tests own sources too, and a global
        assertion made this flaky when run alongside them.
        """
        src = RadarSource(serial_port="/dev/nonexistent")
        got_a, got_b = [], []

        def live_threads():
            own = [t for t in (src._thread, src._dispatch_thread) if t is not None]
            return [t for t in own if t.is_alive()], [t for t in src._retired if t.is_alive()]

        for _ in range(5):
            src.subscribe(got_a.append)
            time.sleep(0.15)
            stopper = threading.Thread(target=src.unsubscribe, args=(got_a.append,))
            stopper.start()
            src.subscribe(got_b.append)  # races the stop
            stopper.join(timeout=5)
            time.sleep(0.4)

            current, retired = live_threads()
            assert len(current) == 2, f"expected one reader + one dispatch, got {current}"
            assert not retired, f"a previous generation is still alive: {retired}"

            before = len(got_b)
            time.sleep(0.25)
            assert len(got_b) > before, "the surviving generation must deliver frames"

            src.unsubscribe(got_b.append)
            time.sleep(0.15)
            current, retired = live_threads()
            assert not current and not retired, f"threads outlived the last unsubscribe: {current} {retired}"

    def test_same_second_starts_get_distinct_files(self):
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        rec = RadarRecorder(recordings_dir=tmp, source=src)
        paths = set()
        for _ in range(3):
            s = rec.start_recording("racket", max_duration_seconds=5)
            paths.add(s.file_path)
            rec.stop_recording()
        assert len(paths) == 3, paths

    def test_write_failure_stops_the_recording_and_is_reported(self):
        """A full card made write() raise inside the frame callback, which
        the reader swallowed: the recording silently stopped writing while
        the UI still said 'recording'."""
        tmp = tempfile.mkdtemp()
        src = RadarSource(serial_port="/dev/nonexistent")
        rec = RadarRecorder(recordings_dir=tmp, source=src)
        rec.start_recording("both", max_duration_seconds=30)
        time.sleep(0.3)  # a few real frames first

        real = rec._jsonl

        class FullCard:
            def write(self, s):
                raise OSError(28, "No space left on device")

            def flush(self):
                pass

            def fileno(self):
                return real.fileno()

            def close(self):
                real.close()

        with rec._lock:
            rec._jsonl = FullCard()

        # The next frame trips the failure and the recording stops itself
        deadline = time.time() + 3
        while rec.is_recording and time.time() < deadline:
            time.sleep(0.05)
        assert not rec.is_recording, "recording must stop itself on a write failure"
        assert rec.write_error and "No space" in rec.write_error
        last = rec.stop_recording()  # idempotent: returns the finished session
        assert last is not None and last.error and "No space" in last.error
        assert last.frame_count > 0

    def test_crashed_file_recovery(self):
        tmp = tempfile.mkdtemp()
        recorder = RadarRecorder(recordings_dir=tmp, source=RadarSource("/dev/nonexistent"))
        crashed = recorder.recordings_dir / "bowling" / "2026-07-03_10-00-00.jsonl"
        with open(crashed, "w") as f:
            f.write(json.dumps({"type": "meta", "session_type": "bowling",
                                "start_time": "T", "mock": False}) + "\n")
            for i in range(50):
                f.write(json.dumps({"type": "frame", "t_ms": i * 100,
                                    "frame_number": i, "cpu_time_ms": i * 50,
                                    "num_points": 0, "points": []}) + "\n")
            f.write('{"type": "frame", "trunc')  # crash mid-write
        summary = recorder.list_recordings("bowling")[0]
        assert summary["incomplete"] is True
        assert summary["frame_count"] == 50
        assert summary["duration_seconds"] == 4.9
