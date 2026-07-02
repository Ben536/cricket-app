"""
Radar Streamer - fans live radar frames out to WebSocket callbacks.

The streamer does NOT own the serial port: it subscribes to the shared
RadarSource (radar/reader.py), so the live view and the recorder can run at
the same time. Its own lifecycle is just a subscription - if the serial port
dies, the reader handles reconnect/mock fallback and the stream keeps flowing.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from radar.reader import get_radar_source, RadarSource
from radar.tlv import RadarFrame

logger = logging.getLogger(__name__)


class RadarStreamer:
    """Streams radar frames to callbacks for WebSocket broadcast."""

    def __init__(
        self,
        source: Optional[RadarSource] = None,
    ):
        self._source = source or get_radar_source()
        self._lock = threading.Lock()
        self._callbacks: list[Callable[[dict], None]] = []
        self._streaming = False
        self._start_time: Optional[float] = None

    @property
    def serial_port(self) -> str:
        return self._source.serial_port

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    @property
    def is_mock(self) -> bool:
        """True when the reader is synthesizing frames (radar absent)."""
        return self._source.is_mock

    def add_callback(self, callback: Callable[[dict], None]):
        """Add a callback to receive frame dicts."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[dict], None]):
        """Remove a callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def start(self):
        """Start streaming (subscribes to the shared reader)."""
        if self._streaming:
            return
        self._streaming = True
        self._start_time = time.time()
        self._source.subscribe(self._on_frame)
        logger.info("Radar streamer started")

    def stop(self):
        """Stop streaming (unsubscribes; reader stops when no one is left)."""
        if not self._streaming:
            return
        self._streaming = False
        self._source.unsubscribe(self._on_frame)
        logger.info("Radar streamer stopped")

    def _on_frame(self, frame: RadarFrame) -> None:
        """RadarSource subscriber: broadcast the frame to all callbacks."""
        if not self._streaming:
            return
        elapsed_ms = int((time.time() - self._start_time) * 1000) if self._start_time else 0
        frame_data = frame.to_stream_dict(timestamp_ms=elapsed_ms)
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
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
