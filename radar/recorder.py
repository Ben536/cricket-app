"""
Radar Recorder - captures radar frames to crash-safe JSONL files.

The recorder does NOT own the serial port: it subscribes to the shared
RadarSource (radar/reader.py), so recording and the live stream can run at
the same time without corrupting each other's TLV streams.

File format (one JSON object per line, recordings/<type>/<timestamp>.jsonl):
  {"type":"meta", session_type, start_time, max_duration_seconds, mock}
  {"type":"frame", t_ms, frame_number, cpu_time_ms, num_points, points}
  {"type":"annotation", t_ms, ...ground-truth fields from the UI...}
  {"type":"mode_change", t_ms, mock}        # radar (un)plugged mid-session
  {"type":"end", end_time, duration_seconds, frame_count, annotation_count}

Timing: t_ms is milliseconds on the HOST recording clock (annotations use the
same clock, so marks align to frames trivially). frame_number and cpu_time_ms
are the radar hardware's own counter/clock - authoritative for tracking,
since host receive time jitters by a whole serial read batch.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from radar.reader import get_radar_source, RadarSource
from radar.tlv import RadarFrame, RadarPoint, TLVParser  # re-exported for compat

logger = logging.getLogger(__name__)

# Recording limits
MAX_RECORDING_SECONDS = 15          # default for short clips (legacy modal)
MAX_GATHERING_SECONDS = 7200        # 2h safety backstop; gathering sessions are stopped manually

# Durability / capacity. Measured on real captures: ~8.7KB per frame at 20Hz,
# i.e. ~170KiB/s, so a full 2h gathering session writes ~1.25GB. A card that
# fills mid-session makes write() raise inside the frame callback, which the
# reader logs and swallows - recording silently stops while the UI still says
# "recording". Refuse to start rather than discover that at the nets.
FSYNC_INTERVAL_SECONDS = 1.0
# Python-side write buffer. Frames are ~8-10KB of JSON, so this batches
# roughly a second of them into one SD write. Bounded by the explicit flush
# at the fsync cadence, so it never delays durability.
WRITE_BUFFER_BYTES = 1 << 20
BYTES_PER_SECOND_ESTIMATE = 175_000
DISK_HEADROOM_BYTES = 200 * 1024 * 1024   # never fill the card to the brim

# Capture types -> recordings/<type>/ subdirectories. Cricket types plus
# radar-signature test proxies (foil-wrapped ball, tennis racket, racket+foil).
SESSION_TYPES = [
    "bowling", "batting", "both",
    "foil_ball", "racket", "racket_foil",
]


@dataclass
class RecordingSession:
    """Metadata for a recording session. Frames stream straight to the JSONL
    file (crash-safe) and are never held in memory here."""
    session_type: str  # one of SESSION_TYPES
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: float = 0.0
    frame_count: int = 0
    max_duration_seconds: float = MAX_RECORDING_SECONDS
    annotation_count: int = 0
    is_mock: bool = False  # True = radar absent at start, frames are FABRICATED
    # Frames written while the radar was absent. is_mock covers "absent at
    # START"; this covers a radar that DROPS OUT mid-session (a knocked USB
    # cable, a sagging supply), which otherwise leaves a file that says
    # mock=false while half its frames are fabricated.
    mock_frame_count: int = 0
    mode_changes: int = 0
    annotations: list[dict] = field(default_factory=list)
    file_path: Optional[str] = None
    # Set when the recording stopped ITSELF because a write failed (card
    # full, I/O error). The file is truncated at that point.
    error: Optional[str] = None

    @property
    def partial_mock(self) -> bool:
        """Started real but lost the radar: the worst case, because nothing
        in the UI flagged it at the time and the file looks genuine."""
        return not self.is_mock and self.mock_frame_count > 0

    def to_dict(self) -> dict:
        return {
            "session_type": self.session_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "frame_count": self.frame_count,
            "annotation_count": self.annotation_count,
            "is_mock": self.is_mock,
            "mock_frame_count": self.mock_frame_count,
            "mode_changes": self.mode_changes,
            "partial_mock": self.partial_mock,
            "annotations": self.annotations,
            "error": self.error,
        }


class RadarRecorder:
    """
    Records radar frames from the shared RadarSource to JSONL files.

    Manages recording sessions with start/stop control and auto-stop after
    the configured max duration.
    """

    def __init__(
        self,
        recordings_dir: str = "recordings",
        source: Optional[RadarSource] = None,
    ):
        self.recordings_dir = Path(recordings_dir)
        self._source = source or get_radar_source()

        # Ensure directories exist
        for session_type in SESSION_TYPES:
            (self.recordings_dir / session_type).mkdir(parents=True, exist_ok=True)

        # Recording state. File writes and counters are guarded by _lock.
        # Start and stop are serialised by _lifecycle_lock, which covers the
        # is_recording CHECK together with the ASSIGNMENT: two clients whose
        # start_recording calls interleaved used to both pass the check and
        # both build a session on this one singleton - the second open()
        # leaked the first file handle, two auto-stop timers were armed, and
        # whichever fired first stopped the other client's recording. The
        # lock also makes a manual stop racing the auto-stop timer safe.
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._current_session: Optional[RecordingSession] = None
        self._last_session: Optional[RecordingSession] = None
        self._start_time: Optional[float] = None
        self._last_fsync: float = 0.0
        self._jsonl = None                      # open file handle during recording
        self._frame_count = 0
        self._annotation_count = 0
        self._mock_frame_count = 0
        self._mode_changes = 0
        self._max_duration = float(MAX_RECORDING_SECONDS)
        self._last_mock: bool = False
        self._auto_stop_timer: Optional[threading.Timer] = None
        # First write failure of the current recording (card full, I/O
        # error). Once set, the recording stops itself and reports it.
        self.write_error: Optional[str] = None

        logger.info(f"RadarRecorder initialized, recordings_dir={self.recordings_dir}")

    @property
    def is_recording(self) -> bool:
        return self._current_session is not None

    @property
    def current_session(self) -> Optional[RecordingSession]:
        return self._current_session

    @property
    def last_session(self) -> Optional[RecordingSession]:
        """The most recently finished recording (None if none has finished)."""
        return self._last_session

    def start_recording(
        self,
        session_type: str,
        max_duration_seconds: Optional[float] = None,
    ) -> RecordingSession:
        """
        Start a new recording session.

        Args:
            session_type: One of SESSION_TYPES
            max_duration_seconds: Hard cap before auto-stop. Defaults to the
                short-clip limit (MAX_RECORDING_SECONDS). For data gathering pass
                a larger value; it is clamped to MAX_GATHERING_SECONDS.

        Returns:
            The new RecordingSession. Check its is_mock flag: if the radar is
            absent the session records FABRICATED mock frames.

        Raises:
            ValueError: If already recording, invalid session type, or not
                enough free disk for the requested duration
        """
        with self._lifecycle_lock:
            if self.is_recording:
                raise ValueError("Already recording")

            if session_type not in SESSION_TYPES:
                raise ValueError(f"Invalid session type: {session_type}")

            # Resolve and clamp the duration cap
            if max_duration_seconds is None:
                self._max_duration = float(MAX_RECORDING_SECONDS)
            else:
                self._max_duration = max(1.0, min(float(max_duration_seconds), MAX_GATHERING_SECONDS))

            self._check_disk_space(self._max_duration)

            # Open the crash-safe JSONL output file now and write a meta
            # header. Frames and annotations are appended line-by-line as
            # they occur, so a crash/disconnect mid-session keeps everything
            # captured so far. The file is opened BEFORE the reader is
            # started: an open() failure must not leave a subscriber-less
            # reader generating frames into a queue nobody drains.
            type_dir = self.recordings_dir / session_type
            type_dir.mkdir(parents=True, exist_ok=True)
            file_path = self._unique_file_path(type_dir)
            # A generous buffer so frames batch into few, large SD writes
            # instead of many small ones (see _write_line). The explicit
            # flush at the fsync cadence keeps at most ~1s unwritten.
            jsonl = open(file_path, "w", buffering=WRITE_BUFFER_BYTES)

            # Resolve mock/real BEFORE subscribing, so the meta line and the
            # first frames agree. A missing radar must never silently produce
            # a plausible-looking dataset - it is flagged in the file and
            # response.
            try:
                self._source.ensure_running()
                self._source.wait_until_ready(timeout=2.0)
            except Exception:
                jsonl.close()
                raise
            is_mock = self._source.is_mock

            start_time = datetime.now(timezone.utc)
            session = RecordingSession(
                session_type=session_type,
                start_time=start_time.isoformat(),
                max_duration_seconds=self._max_duration,
                is_mock=is_mock,
            )
            self._last_mock = is_mock
            if is_mock:
                logger.warning(
                    "Radar unavailable - this session records MOCK data "
                    "(flagged in the file and the start_recording response)"
                )

            self.write_error = None
            self._frame_count = 0
            self._annotation_count = 0
            self._mock_frame_count = 0
            self._mode_changes = 0
            self._jsonl = jsonl
            session.file_path = str(file_path)
            self._start_time = time.time()
            self._last_fsync = self._start_time
            # Publish the session only once everything it needs exists
            self._current_session = session

            self._write_line({
                "type": "meta",
                "session_type": session_type,
                "start_time": session.start_time,
                "max_duration_seconds": self._max_duration,
                "mock": is_mock,
            })

            # Frames start flowing only now, with mode already classified
            self._source.subscribe(self._on_frame)

            # Auto-stop timer replaces the old per-recording thread. It
            # carries the session it was armed for: a timer that fired while
            # a manual stop held the lock (a join can take seconds) used to
            # run AFTER that stop, and if a new recording had started in
            # between it stopped the NEW one, silently.
            self._auto_stop_timer = threading.Timer(self._max_duration, self._auto_stop, args=(session,))
            self._auto_stop_timer.daemon = True
            self._auto_stop_timer.start()

            logger.info(
                f"Started recording: type={session_type}, "
                f"max_duration={self._max_duration:.0f}s, file={file_path.name}, "
                f"mock={is_mock}"
            )
            return session

    @staticmethod
    def _unique_file_path(type_dir: Path) -> Path:
        """Millisecond-resolution name, plus a counter if it still collides.
        One-second resolution meant two same-second starts of the same type
        truncated each other's file."""
        now = datetime.now()
        stem = now.strftime("%Y-%m-%d_%H-%M-%S") + f"-{now.microsecond // 1000:03d}"
        path = type_dir / f"{stem}.jsonl"
        n = 1
        while path.exists():
            n += 1
            path = type_dir / f"{stem}_{n}.jsonl"
        return path

    def _check_disk_space(self, duration_seconds: float) -> None:
        """Refuse to start a recording the card cannot hold.

        Raises ValueError, which start_recording's caller already surfaces to
        the UI - far better than a session that stops writing halfway through
        with the UI still reporting "recording".
        """
        needed = int(duration_seconds * BYTES_PER_SECOND_ESTIMATE) + DISK_HEADROOM_BYTES
        try:
            free = shutil.disk_usage(self.recordings_dir).free
        except OSError as e:
            logger.warning(f"Could not check disk space: {e}")
            return
        if free < needed:
            raise ValueError(
                f"Not enough disk space: {free // (1024*1024)}MB free, "
                f"a {duration_seconds:.0f}s recording needs ~{needed // (1024*1024)}MB "
                f"(including {DISK_HEADROOM_BYTES // (1024*1024)}MB headroom). "
                f"Delete old recordings first."
            )

    def _write_line(self, obj: dict) -> None:
        """Thread-safe append of one JSON object as a line to the JSONL file.

        A failed write (card full, I/O error) is recorded in `write_error`
        rather than raised: raising here reached the reader's dispatch loop,
        which logged and swallowed it, so the recording silently stopped
        writing while the UI still said "recording". The frame path checks
        the flag and stops the recording so the state is honest.
        """
        with self._lock:
            if self._jsonl is None or self.write_error is not None:
                return
            try:
                self._jsonl.write(json.dumps(obj) + "\n")
            except OSError as e:
                self.write_error = str(e)
                logger.error(f"Recording write failed ({e}) - the recording will stop")
                return
            # Durability lives HERE, not in a per-frame flush().
            #
            # flush() only pushes bytes into the OS page cache - it does not
            # survive the realistic failure (a battery/power cut), which is
            # what fsync is for. But it IS a write syscall to the SD card, and
            # doing one per frame measurably starved the reader thread of the
            # serial port: measured on the Pi 2026-09-06, per-frame flush gave
            # 5.6 Hz recorded where flushing only at the fsync cadence gave
            # 14.0 Hz, with identical fsync timing and zero queue drops.
            #
            # So: buffer the writes, and flush+fsync together on the interval.
            # Worst case on a PROCESS crash is the same ~1s the fsync cadence
            # already allowed; power-cut durability is unchanged.
            now = time.time()
            if now - self._last_fsync >= FSYNC_INTERVAL_SECONDS:
                try:
                    self._jsonl.flush()
                    os.fsync(self._jsonl.fileno())
                    self._last_fsync = now
                except OSError as e:
                    self.write_error = f"fsync failed: {e}"
                    logger.error(f"fsync failed (disk full?): {e} - the recording will stop")

    def _stop_on_write_error(self) -> None:
        """Called on the frame/annotation path once a write has failed."""
        logger.error(f"Stopping recording after write failure: {self.write_error}")
        self.stop_recording()

    def _on_frame(self, frame: RadarFrame) -> None:
        """RadarSource subscriber: write one frame to the JSONL stream."""
        if not self.is_recording or self._start_time is None:
            return
        if self.write_error is not None:
            self._stop_on_write_error()
            return

        # Radar plugged/unplugged mid-session: record the transition so the
        # dataset says which stretches are real.
        source_mock = self._source.is_mock
        t_ms = int((time.time() - self._start_time) * 1000)
        if source_mock != self._last_mock:
            self._last_mock = source_mock
            self._mode_changes += 1
            self._write_line({"type": "mode_change", "t_ms": t_ms, "mock": source_mock})
            logger.warning(
                f"Recording mode changed mid-session: mock={source_mock}. "
                + ("The radar has DROPPED OUT - frames from here are fabricated."
                   if source_mock else "The radar is back - frames are real again.")
            )
        if source_mock:
            self._mock_frame_count += 1

        self._write_line({
            "type": "frame",
            "t_ms": t_ms,                        # host recording clock (aligns with annotations)
            "frame_number": frame.frame_number,  # radar hardware counter
            "cpu_time_ms": frame.cpu_time_ms,    # radar hardware clock
            "num_points": frame.num_points,
            "points": [
                {"x": p.x, "y": p.y, "z": p.z, "doppler": p.doppler, "snr": p.snr, "noise": p.noise}
                for p in frame.points
            ],
        })
        with self._lock:
            self._frame_count += 1
            if self._current_session is not None:
                self._current_session.frame_count = self._frame_count
        if self.write_error is not None:
            self._stop_on_write_error()

    def add_annotation(self, mark: Optional[dict] = None) -> Optional[dict]:
        """
        Mark an event during recording, timestamped against the recording clock.

        `mark` is a free-form dict supplied by the UI. For a hit ball it carries
        the ground-truth direction (and optionally outcome / shot type), e.g.:
            {"label": "4", "direction_deg": 35.0, "zone": "cover", "shot_type": "drive"}

        The timestamp (t_ms) aligns the mark to the radar frames captured around
        it. Returns the stored annotation, or None if not recording.
        """
        # Snapshot the start time under the lock: a stop on another thread
        # nulls it between a check and a read.
        with self._lock:
            start_time = self._start_time
            recording = self._current_session is not None
        if not recording or start_time is None:
            return None

        t_ms = int((time.time() - start_time) * 1000)
        # The mark's own keys never override the record's type or clock: a
        # mark {"type": "frame"} was counted as a frame by the crash
        # recovery scan, and a string t_ms broke its max().
        annotation = {**(mark or {}), "type": "annotation", "t_ms": t_ms}
        self._write_line(annotation)
        with self._lock:
            self._annotation_count += 1
            if self._current_session is not None:
                self._current_session.annotations.append(annotation)
                self._current_session.annotation_count = self._annotation_count
        logger.info(f"Annotation @ {t_ms}ms: {mark}")
        if self.write_error is not None:
            self._stop_on_write_error()
        return annotation

    def _auto_stop(self, session: RecordingSession) -> None:
        """Timer callback: max duration reached for THIS session only."""
        with self._lifecycle_lock:
            if self._current_session is not session:
                logger.debug("Auto-stop timer fired for a session that already ended - ignoring")
                return
            logger.info("Max recording duration reached, auto-stopping")
            self._stop_locked()

    def stop_recording(self) -> Optional[RecordingSession]:
        """
        Stop the current recording and finalize the file.

        Idempotent: a manual stop racing the auto-stop timer returns the
        already-finalized session instead of raising.

        Returns:
            The completed RecordingSession, or None if never recorded
        """
        with self._lifecycle_lock:
            if not self.is_recording:
                return self._last_session
            return self._stop_locked()

    def _stop_locked(self) -> RecordingSession:
        """Stop the current recording. Caller holds _lifecycle_lock."""
        # Order matters: unsubscribe first so no frame callback can write
        # to the file while it is being finalized.
        self._source.unsubscribe(self._on_frame)
        if self._auto_stop_timer is not None:
            self._auto_stop_timer.cancel()
            self._auto_stop_timer = None

        session = self._finalize_session()

        logger.info(
            f"Stopped recording: type={session.session_type}, "
            f"frames={session.frame_count}, duration={session.duration_seconds:.1f}s"
        )
        return session

    def _finalize_session(self) -> RecordingSession:
        """Finalize the current session: append an end marker and close the file.

        Frames/annotations were already written incrementally to the JSONL file,
        so there is nothing to dump here - this just stamps the totals and closes
        the crash-safe stream.
        """
        session = self._current_session
        if not session:
            raise RuntimeError("No session to finalize")

        session.end_time = datetime.now(timezone.utc).isoformat()
        session.duration_seconds = (time.time() - self._start_time) if self._start_time else 0.0
        session.frame_count = self._frame_count
        session.annotation_count = self._annotation_count
        session.mock_frame_count = self._mock_frame_count
        session.mode_changes = self._mode_changes
        session.error = self.write_error

        self._write_line({
            "type": "end",
            "end_time": session.end_time,
            "duration_seconds": round(session.duration_seconds, 2),
            "frame_count": session.frame_count,
            "annotation_count": session.annotation_count,
            # Written into the end line so the LISTING can flag a partially
            # fabricated file without scanning it: _summarize_jsonl only
            # reads the head and tail.
            "mock_frame_count": session.mock_frame_count,
            "mode_changes": session.mode_changes,
        })
        with self._lock:
            if self._jsonl is not None:
                try:
                    self._jsonl.close()
                except Exception as e:
                    logger.error(f"Failed to close recording file: {e}")
                self._jsonl = None

        logger.info(
            f"Saved recording to {session.file_path} "
            f"({session.frame_count} frames, {session.annotation_count} marks, "
            f"{session.duration_seconds:.1f}s)"
        )

        # Clear current session (keep it as last for idempotent stop)
        with self._lock:
            self._current_session = None
            self._start_time = None
            self._last_session = session

        return session

    def get_recording_counts(self) -> dict[str, int]:
        """Get count of recordings by session type."""
        counts = {}
        for session_type in SESSION_TYPES:
            dir_path = self.recordings_dir / session_type
            if dir_path.exists():
                # .jsonl = new crash-safe format; .json = legacy single-file format
                counts[session_type] = (
                    len(list(dir_path.glob("*.jsonl")))
                    + len(list(dir_path.glob("*.json")))
                )
            else:
                counts[session_type] = 0
        return counts

    def list_recordings(self, session_type: Optional[str] = None) -> list[dict]:
        """List all recordings (.jsonl and legacy .json), optionally filtered."""
        recordings = []
        types = [session_type] if session_type else SESSION_TYPES

        for st in types:
            dir_path = self.recordings_dir / st
            if not dir_path.exists():
                continue

            files = sorted(
                list(dir_path.glob("*.jsonl")) + list(dir_path.glob("*.json")),
                key=lambda p: p.name,
                reverse=True,
            )
            for file_path in files:
                try:
                    if file_path.suffix == ".jsonl":
                        recordings.append(self._summarize_jsonl(file_path, st))
                    else:
                        with open(file_path) as f:
                            data = json.load(f)
                        recordings.append({
                            "file": str(file_path),
                            "session_type": data.get("session_type", st),
                            "start_time": data.get("start_time"),
                            "duration_seconds": data.get("duration_seconds", 0),
                            "frame_count": data.get("frame_count", 0),
                            "annotation_count": data.get("annotation_count", 0),
                        })
                except Exception as e:
                    logger.warning(f"Could not read {file_path}: {e}")

        return recordings

    # Bound the reply: a 2h session can hold hundreds of marks, and this goes
    # over a WebSocket to a phone.
    MAX_ANNOTATIONS_RETURNED = 2000

    def resolve_recording(self, file_path: str) -> Path:
        """Resolve a client-supplied recording path INSIDE recordings_dir.

        The path arrives from the network. Without this, '../../etc/passwd'
        or an absolute path would be read straight off the Pi and sent back.
        Accepts either a full path as listed by list_recordings, or a
        '<type>/<name>.jsonl' relative one.

        Raises ValueError if it escapes the directory or does not exist.
        """
        root = self.recordings_dir.resolve()
        candidate = Path(file_path)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError(f"Recording path is outside {root}") from None
        if not resolved.is_file():
            raise ValueError(f"No such recording: {file_path}")
        if resolved.suffix not in (".jsonl", ".json"):
            raise ValueError("Not a recording file")
        return resolved

    def read_annotations(self, file_path: str) -> dict:
        """Read a recording's meta, marks and totals - NEVER its frames.

        A gathering session's frames are ~1.25GB; only the labels are wanted
        for review, and they are a few KB. The file is streamed line by line
        so memory stays flat regardless of size.
        """
        path = self.resolve_recording(file_path)
        meta: dict = {}
        end: dict = {}
        annotations: list[dict] = []
        truncated = False
        frames = 0
        mock_frames = 0
        mode_changes = 0
        cur_mock = False   # set from meta below on the first line

        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a crash can truncate the final line
                kind = obj.get("type")
                if kind == "frame":
                    frames += 1
                    if cur_mock:
                        mock_frames += 1
                elif kind == "mode_change":
                    cur_mock = bool(obj.get("mock"))
                    mode_changes += 1
                elif kind == "annotation":
                    if len(annotations) < self.MAX_ANNOTATIONS_RETURNED:
                        annotations.append(obj)
                    else:
                        truncated = True
                elif kind == "meta":
                    meta = obj
                    cur_mock = bool(obj.get("mock", False))
                elif kind == "end":
                    end = obj

        started_mock = bool(meta.get("mock", False))
        return {
            "file": str(path),
            "session_type": meta.get("session_type", path.parent.name),
            "start_time": meta.get("start_time"),
            "mock": started_mock,
            "mock_frame_count": mock_frames,
            "mode_changes": mode_changes,
            # Looked genuine at the time but is not: the radar dropped out
            # part-way and those frames are fabricated.
            "partial_mock": (not started_mock) and mock_frames > 0,
            "max_duration_seconds": meta.get("max_duration_seconds"),
            "duration_seconds": end.get("duration_seconds", 0),
            "frame_count": end.get("frame_count", frames),
            "annotation_count": end.get("annotation_count", len(annotations)),
            "incomplete": not end,
            "annotations": annotations,
            "annotations_truncated": truncated,
            "size_bytes": path.stat().st_size,
        }

    def _summarize_jsonl(self, file_path: Path, st: str) -> dict:
        """Summarize a JSONL recording from its meta (first) and end (last) lines.

        Only the head and tail of the file are read - gathering sessions can run
        for hours (~72k lines at 10Hz), so scanning every line on each listing
        would crawl on the Pi. A file with no end marker (crashed/interrupted
        session) falls back to a full scan to recover what was captured.
        """
        meta: dict = {}
        end: dict = {}

        with open(file_path, "rb") as f:
            first_line = f.readline()
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace")

        try:
            obj = json.loads(first_line)
            if obj.get("type") == "meta":
                meta = obj
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # The last complete line decides: end marker = clean stop, anything
        # else = crashed session. Unparseable trailing lines (truncated by a
        # crash mid-write, or clipped by the tail seek) are skipped.
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "end":
                end = obj
            break

        summary = {
            "file": str(file_path),
            "session_type": meta.get("session_type", st),
            "start_time": meta.get("start_time"),
            "mock": meta.get("mock", False),
            "mock_frame_count": end.get("mock_frame_count", 0),
            "mode_changes": end.get("mode_changes", 0),
            "duration_seconds": end.get("duration_seconds", 0),
            "frame_count": end.get("frame_count", 0),
            "annotation_count": end.get("annotation_count", 0),
        }
        if not end:
            summary.update(self._recover_crashed_jsonl(file_path))
            summary["incomplete"] = True
        summary["partial_mock"] = (not summary["mock"]) and summary["mock_frame_count"] > 0
        return summary

    def _recover_crashed_jsonl(self, file_path: Path) -> dict:
        """Full scan of a JSONL file with no end marker (session crashed or was
        interrupted): count frames/annotations and take the duration from the
        last recorded timestamp. Slow path, only hit for incomplete files."""
        frames = 0
        annotations = 0
        last_t_ms = 0
        mock_frames = 0
        mode_changes = 0
        cur_mock = False
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = obj.get("type")
                if kind == "frame":
                    frames += 1
                    if cur_mock:
                        mock_frames += 1
                    # t_ms is the current field; timestamp_ms appears in older files
                    last_t_ms = max(last_t_ms, obj.get("t_ms", obj.get("timestamp_ms", 0)))
                elif kind == "meta":
                    cur_mock = bool(obj.get("mock", False))
                elif kind == "mode_change":
                    cur_mock = bool(obj.get("mock"))
                    mode_changes += 1
                elif kind == "annotation":
                    annotations += 1
                    last_t_ms = max(last_t_ms, obj.get("t_ms", 0))
        return {
            "duration_seconds": round(last_t_ms / 1000, 2),
            "frame_count": frames,
            "annotation_count": annotations,
            "mock_frame_count": mock_frames,
            "mode_changes": mode_changes,
        }


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
