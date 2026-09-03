"""
The live radar stream to one client is bounded and cancellable.

2026-08 review T1.10: every frame scheduled a run_coroutine_threadsafe()
whose Future was discarded - no bound, no backpressure, exceptions lost.
Measured then: pending sends grew 5 -> 18 in 3s against a slow socket and
18 stayed parked after unsubscribing.
"""

from __future__ import annotations

import asyncio

import pytest

from server import handlers as handlers_mod
from server.handlers import MessageHandlers, STREAM_QUEUE_MAX
from server.session_manager import SessionManager


class FakeStreamer:
    """Stands in for radar.streamer.get_streamer(): records callbacks."""
    def __init__(self):
        self.callbacks = []
        self.is_streaming = False
        self.starts = 0
        self.stops = 0

    def add_callback(self, cb):
        self.callbacks.append(cb)

    def remove_callback(self, cb):
        if cb in self.callbacks:
            self.callbacks.remove(cb)

    def start(self):
        self.is_streaming = True
        self.starts += 1

    def stop(self):
        self.is_streaming = False
        self.stops += 1


class SlowConnectionManager:
    """send_to_client takes `delay` seconds - a phone on a bad link."""
    def __init__(self, delay: float):
        self.delay = delay
        self.sent = []
        self.gate = asyncio.Event()

    async def send_to_client(self, client_id, message):
        await asyncio.sleep(self.delay)
        self.sent.append(message)
        return True


@pytest.fixture
def streamer(monkeypatch):
    fake = FakeStreamer()
    monkeypatch.setattr(handlers_mod, "get_streamer", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_slow_client_gets_a_bounded_queue_and_drops_are_counted(streamer):
    cm = SlowConnectionManager(delay=0.05)
    h = MessageHandlers(repository=None, session_manager=SessionManager(), connection_manager=cm)  # type: ignore[arg-type]

    reply = await h.handle_start_radar_stream("client-1", {"message_id": "m"})
    assert reply["type"] == "radar_stream_started"
    assert streamer.is_streaming and len(streamer.callbacks) == 1
    callback = streamer.callbacks[0]

    # 60 frames arrive from the radar thread far faster than the socket drains
    for i in range(60):
        callback({"frame_number": i, "point_count": 0, "points": []})
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    sub = h._streams["client-1"]
    assert sub.queue.qsize() <= STREAM_QUEUE_MAX
    assert sub.dropped >= 60 - STREAM_QUEUE_MAX - 2, f"only {sub.dropped} drops counted"

    # Stop: the drain task is cancelled and the streamer released
    await asyncio.sleep(0.12)
    stopped = await h.handle_stop_radar_stream("client-1", {"message_id": "s"})
    assert stopped["type"] == "radar_stream_stopped"
    assert stopped["payload"]["frames_dropped"] == sub.dropped
    assert sub.task.done()
    assert "client-1" not in h._streams
    assert streamer.callbacks == []
    assert streamer.stops == 1

    # Nothing keeps sending after stop
    sent_before = len(cm.sent)
    await asyncio.sleep(0.15)
    assert len(cm.sent) == sent_before


@pytest.mark.asyncio
async def test_freshest_frames_win_when_dropping(streamer):
    cm = SlowConnectionManager(delay=10.0)  # never drains during the test
    h = MessageHandlers(repository=None, session_manager=SessionManager(), connection_manager=cm)  # type: ignore[arg-type]
    await h.handle_start_radar_stream("c", {"message_id": "m"})
    callback = streamer.callbacks[0]
    for i in range(20):
        callback({"frame_number": i})
    await asyncio.sleep(0)
    sub = h._streams["c"]
    # The first frame was taken by the drain task; the queue holds the newest
    queued = [sub.queue.get_nowait()["frame_number"] for _ in range(sub.queue.qsize())]
    assert queued == list(range(20 - len(queued), 20))
    await h._release_stream("c")


@pytest.mark.asyncio
async def test_unreachable_client_ends_its_own_stream(streamer):
    class DeadConnectionManager:
        async def send_to_client(self, client_id, message):
            return False  # evicted / closed

    h = MessageHandlers(repository=None, session_manager=SessionManager(), connection_manager=DeadConnectionManager())  # type: ignore[arg-type]
    await h.handle_start_radar_stream("gone", {"message_id": "m"})
    streamer.callbacks[0]({"frame_number": 1})
    await asyncio.sleep(0.05)
    assert "gone" not in h._streams, "a dead client's subscription must self-clean"
    assert streamer.callbacks == []
    assert streamer.is_streaming is False


@pytest.mark.asyncio
async def test_repeat_start_replaces_the_subscription(streamer):
    cm = SlowConnectionManager(delay=0.01)
    h = MessageHandlers(repository=None, session_manager=SessionManager(), connection_manager=cm)  # type: ignore[arg-type]
    await h.handle_start_radar_stream("c", {"message_id": "1"})
    first = h._streams["c"]
    await h.handle_start_radar_stream("c", {"message_id": "2"})
    second = h._streams["c"]
    assert first is not second and first.task.done()
    assert len(streamer.callbacks) == 1
    await h.cleanup_client("c")
    assert streamer.callbacks == [] and not streamer.is_streaming
