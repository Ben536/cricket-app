"""End-to-end WebSocket server tests over a real connection.

Covers the game-session protocol flow plus the disconnect-cleanup and
recording paths the old script never touched.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
import websockets

from server.websocket_server import CricketWebSocketServer

import socket


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# A free ephemeral port: away from the real 5002, and safe when several test
# processes run at once (a fixed 5099 collided).
PORT = _free_port()


def envelope(msg_type, payload=None):
    return json.dumps({
        "type": msg_type,
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload or {},
    })


async def recv_type(ws, expected, timeout=5.0):
    """Receive until a message of the expected type arrives (skips pushes)."""
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg["type"] == expected:
            return msg


async def request(ws, msg_type, payload=None, expect=None, timeout=5.0):
    """Send a request and receive its correlated reply (matches in_reply_to,
    so unsolicited pushes like the initial session_state are skipped)."""
    raw = envelope(msg_type, payload)
    msg_id = json.loads(raw)["message_id"]
    await ws.send(raw)
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("in_reply_to") == msg_id and (expect is None or msg["type"] == expect):
            return msg


@pytest.fixture
async def server(migrated_db):
    srv = CricketWebSocketServer(host="127.0.0.1", port=PORT, db_path=migrated_db)
    await srv.start()
    yield srv
    await srv.stop()


@pytest.mark.asyncio
async def test_full_session_flow_with_extras(server):
    async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
        await recv_type(ws, "connection_status")

        state = await request(ws, "create_profile", {"name": "P", "batting_hand": "right"},
                              expect="session_state")
        profile_id = state["payload"]["profiles"][0]["id"]

        state = await request(ws, "start_session", {"profile_id": profile_id},
                              expect="session_state")
        assert state["payload"]["session"] is not None

        # W / wd / nb - the outcomes migration 003 exists for
        for result in ("2", "W", "wd", "nb"):
            state = await request(ws, "manual_input", {"result": result},
                                  expect="session_state")
        assert state["payload"]["session"]["runs"] == 2

        await request(ws, "undo", {"session_id": str(state["payload"]["session"]["id"])},
                      expect="session_state")


@pytest.mark.asyncio
async def test_disconnect_autocompletes_session(server):
    async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
        await recv_type(ws, "connection_status")
        state = await request(ws, "create_profile", {"name": "D", "batting_hand": "left"},
                              expect="session_state")
        profile_id = state["payload"]["profiles"][0]["id"]
        state = await request(ws, "start_session", {"profile_id": profile_id},
                              expect="session_state")
        session_id = int(state["payload"]["session"]["id"])
    # Connection dropped without end_session - give cleanup a beat
    await asyncio.sleep(0.3)

    db_session = server.repository.get_session(session_id)
    assert db_session.is_completed, "disconnect must auto-complete the session"
    rows = server.repository.get_active_sessions()
    assert all(r.session_id != session_id for r in rows), "active_sessions row must be removed"
    assert server.session_manager.get_session_by_id(session_id) is None


@pytest.mark.asyncio
async def test_seeded_simulate_shot_is_reproducible(server):
    async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
        await recv_type(ws, "connection_status")
        payload = {
            "exit_speed": 90, "horizontal_angle": 20, "vertical_angle": 15,
            "field_config": [{"x": 20, "y": 30, "name": "mid-off"}],
            "boundary_distance": 70, "difficulty": "medium", "seed": 42,
        }
        results = []
        for _ in range(2):
            await ws.send(envelope("simulate_shot", payload))
            msg = await recv_type(ws, "simulate_result")
            results.append(msg["payload"]["simulation"])
        assert results[0] == results[1]
        assert results[0]["seed"] == 42


@pytest.mark.asyncio
async def test_malformed_payload_keeps_connection_and_session(server):
    """T1.13 end to end: a malformed payload used to raise out of the router,
    close the socket with code 1000 and auto-complete the session."""
    async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
        await recv_type(ws, "connection_status")
        state = await request(ws, "create_profile", {"name": "M", "batting_hand": "right"},
                              expect="session_state")
        profile_id = state["payload"]["profiles"][0]["id"]
        state = await request(ws, "start_session", {"profile_id": profile_id},
                              expect="session_state")
        session_id = int(state["payload"]["session"]["id"])

        for msg_type, payload in [
            ("set_field", {"fielders": [{"x": 1, "y": 2, "name": "a"}], "boundary_distance": None}),
            ("set_field", {"fielders": 3}),
            ("create_profile", {"name": 12345, "batting_hand": "right"}),
            ("simulate_shot", {"exit_speed": "fast", "horizontal_angle": 0,
                               "vertical_angle": 0, "field_config": []}),
            ("start_recording", {"session_type": "both", "max_duration": float("nan")}),
        ]:
            err = await request(ws, msg_type, payload, expect="error")
            assert err["payload"]["code"].startswith("E3"), err

        # A fielder with no name is VALID on the wire; the handler used to
        # KeyError on it (the router deliberately accepts it).
        state = await request(ws, "set_field", {"fielders": [{"x": 1, "y": 2}, {"x": 5, "y": 9}]},
                              expect="session_state")
        names = [f["name"] for f in state["payload"]["field_config"]]
        assert names == ["fielder_0", "fielder_1"]

        # Still connected, session still active, scoring still works
        pong = await request(ws, "ping", expect="pong")
        assert pong["type"] == "pong"
        state = await request(ws, "manual_input", {"result": "4"}, expect="session_state")
        assert state["payload"]["session"]["runs"] == 4

    await asyncio.sleep(0.2)
    assert server.repository.get_session(session_id).is_completed  # normal close cleanup


@pytest.mark.asyncio
async def test_invalid_message_gets_error(server):
    async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
        await recv_type(ws, "connection_status")
        await ws.send(envelope("no_such_type", {}))
        msg = await recv_type(ws, "error")
        assert msg["payload"]["code"]


@pytest.mark.asyncio
async def test_recording_lifecycle_mock(server):
    async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
        await recv_type(ws, "connection_status")

        await ws.send(envelope("start_recording", {"session_type": "both", "max_duration": 4}))
        started = await recv_type(ws, "recording_started")
        assert started["payload"]["mock"] is True  # no radar on the test host

        await asyncio.sleep(0.5)
        await ws.send(envelope("add_annotation", {"direction_deg": -30.0}))
        added = await recv_type(ws, "annotation_added")
        assert added["payload"]["annotation_count"] == 1

        await ws.send(envelope("stop_recording", {}))
        stopped = await recv_type(ws, "recording_stopped")
        assert stopped["payload"]["annotation_count"] == 1
        assert stopped["payload"]["frame_count"] > 0
