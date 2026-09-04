"""
RadarSource - the single owner of the radar data UART.

Exactly one thread opens /dev/ttyUSB1, parses the TLV stream once, and fans
complete frames out to subscribers (recorder, live streamer, future ball
detector). This is what lets recording and the live view run at the same time:
two independent readers on one tty silently steal bytes from each other and
corrupt both streams.

Lifecycle: the reader thread starts with the first subscriber and stops
(releasing the port) when the last one unsubscribes. If the serial port dies
mid-stream (USB unplug), the reader falls back to mock frames and retries the
real port with backoff - subscribers keep receiving frames either way and can
check `is_mock` to know what they're getting.

Generations: every start creates a fresh stop Event that its two threads
capture, and a start joins the previous generation's threads first. A single
shared Event used to be cleared by a new start while the old threads were
still winding down (the last unsubscribe sets the event and joins OUTSIDE the
lock), so the old threads never saw the stop - two dispatch threads, and with
a real port two readers on one tty. The serial handle is likewise local to
the reader thread: an old thread's `finally` used to close whatever handle
the instance held, i.e. the NEW thread's port.
"""

from __future__ import annotations

import logging
import math
import queue
import random
import threading
import time
from typing import Callable, Optional

from radar.serial_utils import open_radar_serial
from radar.tlv import RadarFrame, RadarPoint, TLVParser

logger = logging.getLogger(__name__)

FRAME_RATE_HZ = 20  # radar profile frame rate (profile_cricket.cfg, 2026-06-27)
MOCK_FRAME_INTERVAL = 1.0 / FRAME_RATE_HZ  # match the real rate so mock timing is representative
SERIAL_RETRY_SECONDS = 5.0  # how often to re-try the real port while mocking

# Frames buffered between the reader thread and the dispatch thread. At 20Hz
# this is ~2.5s of slack - long enough to ride out an SD-card writeback stall,
# short enough that a wedged subscriber cannot accumulate memory.
FRAME_QUEUE_MAX = 50

# How long a start waits for the previous generation's threads to exit.
# The reader blocks at most one serial read timeout (0.1s) per loop.
GENERATION_JOIN_SECONDS = 2.0

FrameCallback = Callable[[RadarFrame], None]


class RadarSource:
    """Single-owner serial reader with subscriber fan-out."""

    def __init__(
        self,
        serial_port: str = "/dev/ttyUSB1",
        baud_rate: int = 921600,
    ):
        self.serial_port = serial_port
        self.baud_rate = baud_rate

        self._lock = threading.RLock()
        self._subscribers: list[FrameCallback] = []
        self._thread: Optional[threading.Thread] = None
        self._dispatch_thread: Optional[threading.Thread] = None
        # The CURRENT generation's stop event (threads capture their own)
        self._stop_event = threading.Event()
        # Threads of a generation that has been told to stop but may still be
        # running; the next start joins them before opening the port again.
        self._retired: list[threading.Thread] = []
        self._is_mock = True
        self._mode_known = threading.Event()  # set after the first port attempt

        # Subscribers run on their OWN thread, fed by a bounded queue. They used
        # to be invoked inline on the reader thread, which meant a slow sink
        # stalled the UART: the recorder's per-frame json.dumps + write + flush
        # is a blocking SD-card write, and while it ran nothing drained the tty,
        # the kernel buffer overflowed, and bytes were lost MID-PACKET. Measured
        # at 788 reads/0.5s with a fast subscriber vs 18 with a 50ms one (-98%).
        # That byte loss is what produced the corrupt frames in the committed
        # recordings. Backpressure must lose whole frames (visible, counted),
        # never bytes (silent corruption).
        self._frame_queue: queue.Queue = queue.Queue(maxsize=FRAME_QUEUE_MAX)

        # Data-flow stats - lets health checks measure "frames are arriving"
        # instead of poking the UART (which can reset the chip via DTR).
        self.frames_total = 0
        self.frames_dropped_backpressure = 0
        self.last_frame_time: float = 0.0

    @property
    def is_mock(self) -> bool:
        """True when frames are synthesized (radar absent/unopenable)."""
        return self._is_mock

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def ensure_running(self) -> None:
        """Start the reader thread if it isn't running (without subscribing).

        Lets a caller resolve `is_mock` BEFORE registering its callback, so
        the first frames it receives are already correctly classified.
        """
        with self._lock:
            if self.is_running:
                return
            # Let the previous generation finish before opening the port
            # again (it holds the port exclusively until its loop exits).
            for t in self._retired:
                if t is not threading.current_thread():
                    t.join(timeout=GENERATION_JOIN_SECONDS)
            self._retired = [t for t in self._retired if t.is_alive() and t is not threading.current_thread()]
            if self._retired:
                logger.warning(f"{len(self._retired)} previous radar thread(s) still alive at restart")

            stop_event = threading.Event()
            self._stop_event = stop_event
            self._mode_known.clear()
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_loop, args=(stop_event,), daemon=True, name="radar-dispatch"
            )
            self._dispatch_thread.start()
            self._thread = threading.Thread(
                target=self._run, args=(stop_event,), daemon=True, name="radar-reader"
            )
            self._thread.start()
            logger.info("Radar reader started")

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        """Block until the first serial-port attempt has resolved mock/real
        mode (or timeout). Returns True if resolved."""
        return self._mode_known.wait(timeout)

    def subscribe(self, callback: FrameCallback) -> None:
        """Register a frame callback (invoked on the dispatch thread).

        Starts the reader on the first subscription.
        """
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
            self.ensure_running()

    def unsubscribe(self, callback: FrameCallback) -> None:
        """Deregister a callback; stops the reader when none remain."""
        stop_threads = []
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
            if not self._subscribers and self.is_running:
                self._stop_event.set()
                stop_threads = [t for t in (self._thread, self._dispatch_thread) if t]
                self._retired.extend(stop_threads)
                self._thread = None
                self._dispatch_thread = None

        # Join outside the lock; never join from one of these threads itself.
        # A subscriber callback runs on the DISPATCH thread and may unsubscribe
        # (the recorder's auto-stop does), so both need the self-join guard.
        for t in stop_threads:
            if t is not threading.current_thread():
                t.join(timeout=GENERATION_JOIN_SECONDS)
        if stop_threads:
            with self._lock:
                self._retired = [t for t in self._retired if t.is_alive()]
            logger.info("Radar reader stopped (no subscribers)")

    def _dispatch(self, frame: RadarFrame) -> None:
        """Enqueue a frame for the dispatch thread. MUST NOT BLOCK.

        Called from the reader thread, which has to get straight back to
        draining the serial port.
        """
        self.frames_total += 1
        self.last_frame_time = time.time()
        try:
            self._frame_queue.put_nowait(frame)
        except queue.Full:
            # Drop the OLDEST frame, not this one: for live view and detection
            # the freshest data is the useful data.
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait(frame)
            except (queue.Empty, queue.Full):
                pass
            self.frames_dropped_backpressure += 1
            if self.frames_dropped_backpressure % 20 == 1:
                logger.warning(
                    f"Radar subscriber(s) too slow - dropped oldest frame "
                    f"[{self.frames_dropped_backpressure} dropped so far]"
                )

    def _dispatch_loop(self, stop_event: threading.Event) -> None:
        """Deliver queued frames to subscribers, off the reader thread."""
        while not stop_event.is_set():
            try:
                frame = self._frame_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                subscribers = list(self._subscribers)
            for callback in subscribers:
                try:
                    callback(frame)
                except Exception as e:
                    logger.error(f"Radar subscriber error: {e}")

    def _open_serial(self):
        """Try to open the real port; update mock state on transitions.
        Returns the handle (or None); the caller owns it."""
        ser = open_radar_serial(self.serial_port, self.baud_rate)
        was_mock = self._is_mock
        self._is_mock = ser is None
        self._mode_known.set()
        if was_mock and not self._is_mock:
            logger.info("Radar serial acquired - streaming REAL data")
        elif not was_mock and self._is_mock:
            logger.warning("Radar serial lost - falling back to MOCK data")
        return ser

    @staticmethod
    def _close_serial(ser) -> None:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    def _run(self, stop_event: threading.Event) -> None:
        """Reader loop: real serial when available, mock otherwise, with
        periodic retries of the real port. The serial handle is LOCAL to
        this thread."""
        parser = TLVParser()
        ser = None
        next_retry = 0.0
        mock_start = time.time()
        mock_frame_number = 0

        try:
            while not stop_event.is_set():
                # (Re)try the real port on schedule
                if ser is None and time.time() >= next_retry:
                    ser = self._open_serial()
                    if ser is None:
                        next_retry = time.time() + SERIAL_RETRY_SECONDS
                    else:
                        parser = TLVParser()

                if ser is not None:
                    try:
                        data = ser.read(4096)
                    except Exception as e:
                        logger.warning(f"Radar serial read failed: {e}")
                        self._close_serial(ser)
                        ser = None
                        self._is_mock = True
                        next_retry = time.time() + 1.0
                        continue
                    if data:
                        for frame in parser.add_data(data):
                            self._dispatch(frame)
                else:
                    # Mock frame: a "ball" sweeping through every few seconds
                    # plus some noise points, so the UI has something to show.
                    if stop_event.wait(MOCK_FRAME_INTERVAL):
                        break
                    mock_frame_number += 1
                    elapsed = time.time() - mock_start
                    points: list[RadarPoint] = []
                    if int(elapsed) % 3 < 1:
                        t = elapsed * 2
                        points.append(RadarPoint(
                            x=math.sin(t) * 2,
                            y=3 + math.cos(t * 0.5),
                            z=0.5,
                            doppler=15 + math.sin(t) * 5,
                            snr=18.0,
                            noise=5.0,
                        ))
                    for _ in range(random.randint(0, 3)):
                        points.append(RadarPoint(
                            x=random.uniform(-3, 3),
                            y=random.uniform(0, 6),
                            z=random.uniform(0, 2),
                            doppler=random.uniform(-2, 2),
                            snr=random.uniform(8, 14),
                            noise=random.uniform(4, 8),
                        ))
                    self._dispatch(RadarFrame(
                        frame_number=mock_frame_number,
                        cpu_time_ms=int(elapsed * 1000),
                        num_points=len(points),
                        points=points,
                    ))
        except Exception as e:
            logger.error(f"Radar reader crashed: {e}")
        finally:
            self._close_serial(ser)
            # Only this generation may report mock: a later generation may
            # already have the real port.
            if self._stop_event is stop_event:
                self._is_mock = True


# Singleton
_source: Optional[RadarSource] = None


def get_radar_source() -> RadarSource:
    """Get or create the global RadarSource instance."""
    global _source
    if _source is None:
        _source = RadarSource()
    return _source
