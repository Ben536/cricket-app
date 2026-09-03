"""
One wedged client must not block everyone else.

2026-08 review T1.9: broadcasts and the heartbeat loop were sequential
`await send_to_client(...)` with no timeout. websockets' send() awaits
drain(), which never completes while the peer's TCP window is full - a phone
that walked out of AP range keeps the kernel retransmitting for ~15 minutes.
Every other phone stopped getting heartbeats for that window, and the reaper
(which IS the heartbeat loop) was parked on the very client it should reap.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from server.connection_manager import ConnectionManager


class FakeSocket:
    def __init__(self, wedged: bool = False, delay: float = 0.0):
        self.wedged = wedged
        self.delay = delay
        self.sent: list[str] = []
        self.closed = False
        self._never = asyncio.Event()

    async def send(self, data: str) -> None:
        if self.wedged:
            await self._never.wait()
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_wedged_client_is_evicted_and_others_still_receive():
    cm = ConnectionManager(send_timeout=0.2)
    good, bad = FakeSocket(), FakeSocket(wedged=True)
    await cm.add_client("good", good)
    await cm.add_client("bad", bad)
    await cm.join_session("good", "s1")
    await cm.join_session("bad", "s1")

    t0 = time.monotonic()
    sent = await cm.broadcast_to_session("s1", {"type": "session_state", "message_id": "m1"})
    elapsed = time.monotonic() - t0

    assert sent == 1
    assert elapsed < 1.0, f"broadcast blocked for {elapsed:.2f}s on the wedged client"
    assert len(good.sent) == 1
    assert cm.get_client("bad") is None, "the wedged client must be evicted"
    assert cm.get_client("good") is not None

    # The close handshake runs in the background so the caller never waits on it
    await asyncio.sleep(0.05)
    assert bad.closed


@pytest.mark.asyncio
async def test_fan_out_is_concurrent_not_sequential():
    cm = ConnectionManager(send_timeout=2.0)
    sockets = [FakeSocket(delay=0.2) for _ in range(4)]
    for i, s in enumerate(sockets):
        await cm.add_client(f"c{i}", s)

    t0 = time.monotonic()
    sent = await cm.broadcast_to_all({"type": "connection_status", "message_id": "m"})
    elapsed = time.monotonic() - t0

    assert sent == 4
    # Sequential would be ~0.8s; concurrent is ~0.2s
    assert elapsed < 0.5, f"fan-out took {elapsed:.2f}s - sends are still sequential"


@pytest.mark.asyncio
async def test_send_timeout_returns_false_and_removes_client():
    cm = ConnectionManager(send_timeout=0.1)
    bad = FakeSocket(wedged=True)
    await cm.add_client("bad", bad)
    ok = await cm.send_to_client("bad", {"type": "pong", "message_id": "p"})
    assert ok is False
    assert cm.client_count == 0
