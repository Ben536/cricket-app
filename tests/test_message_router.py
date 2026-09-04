"""
The router must answer a malformed message with an error frame - never raise.

2026-08 review T1.13: `_validate_type_specific` did bare `len()` / `<` on
untrusted values, and `route()` called it outside its try. A TypeError
escaped the router, the connection handler's catch-all closed the socket
with code 1000, and its `finally` marked the session complete. Every case
below used to do exactly that.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from server.message_router import MessageRouter, ErrorCode

VALIDATION_CODES = {
    ErrorCode.INVALID_MESSAGE_FORMAT.value,
    ErrorCode.MISSING_REQUIRED_FIELD.value,
    ErrorCode.INVALID_FIELD_VALUE.value,
    ErrorCode.FIELD_OUT_OF_RANGE.value,
}


def envelope(msg_type: str, payload=None, **overrides) -> str:
    msg = {
        "type": msg_type,
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": {} if payload is None else payload,
    }
    msg.update(overrides)
    return json.dumps(msg)


@pytest.fixture
def router():
    r = MessageRouter()
    calls = []

    async def handler(client_id, message):
        calls.append(message)
        return {"type": "handled", "message_id": "x", "timestamp": "t"}

    for t in ("set_field", "set_difficulty", "select_profile", "create_profile", "update_profile",
              "manual_input", "start_session", "end_session", "undo", "ping", "simulate_shot",
              "start_recording", "stop_recording", "get_recording_status", "add_annotation",
              "start_radar_stream", "stop_radar_stream"):
        r.register_handler(t, handler)
    r.calls = calls  # type: ignore[attr-defined]
    return r


GOOD_FIELD = [{"x": 20, "y": 30, "name": "mid-off"}]
GOOD_SIM = {"exit_speed": 90, "horizontal_angle": 20, "vertical_angle": 15, "field_config": GOOD_FIELD}

MALFORMED = [
    ("set_field", {"fielders": GOOD_FIELD, "boundary_distance": None}),
    ("set_field", {"fielders": GOOD_FIELD, "boundary_distance": "70"}),
    ("set_field", {"fielders": 3}),
    ("set_field", {"fielders": [{"x": "a", "y": 2}]}),
    ("set_field", {"fielders": [{"x": 1, "y": 2, "name": 5}]}),
    ("create_profile", {"name": 12345, "batting_hand": "right"}),
    ("create_profile", {"name": "ok", "batting_hand": None}),
    ("create_profile", {"name": "   ", "batting_hand": "left"}),
    ("update_profile", {"profile_id": "1", "name": ["x"]}),
    ("select_profile", {"profile_id": {"id": 1}}),
    ("select_profile", {"profile_id": True}),
    ("start_session", {"profile_id": "1", "field_config": "none"}),
    ("start_session", {"profile_id": "1", "difficulty": "god"}),
    ("end_session", {"session_id": [1]}),
    ("undo", {"session_id": {"a": 1}}),
    ("simulate_shot", {**GOOD_SIM, "exit_speed": "fast"}),
    ("simulate_shot", {**GOOD_SIM, "exit_speed": True}),
    ("simulate_shot", {**GOOD_SIM, "horizontal_angle": None}),
    ("simulate_shot", {**GOOD_SIM, "field_config": "none"}),
    ("simulate_shot", {**GOOD_SIM, "field_config": [{"x": "1", "y": 2}]}),
    ("simulate_shot", {**GOOD_SIM, "field_config": [{"x": 1, "y": 2}] * 60}),
    ("simulate_shot", {**GOOD_SIM, "boundary_distance": "70"}),
    ("simulate_shot", {**GOOD_SIM, "boundary_distance": 0}),
    ("simulate_shot", {**GOOD_SIM, "difficulty": "god"}),
    ("simulate_shot", {**GOOD_SIM, "seed": True}),
    ("simulate_shot", {**GOOD_SIM, "seed": "42"}),
    ("start_recording", {"session_type": "both", "max_duration": "long"}),
    ("start_recording", {"session_type": "both", "max_duration": float("nan")}),
    ("start_recording", {"session_type": "both", "max_duration": float("inf")}),
    ("start_recording", {"session_type": "both", "max_duration": 10 ** 400}),
    ("start_recording", {"session_type": ["both"]}),
    ("simulate_shot", {**GOOD_SIM, "exit_speed": 10 ** 400}),
    ("add_annotation", {f"k{i}": i for i in range(40)}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("msg_type,payload", MALFORMED, ids=[f"{t}:{list(p)[0] if p else ''}" for t, p in MALFORMED])
async def test_malformed_payload_returns_error_frame(router, msg_type, payload):
    response = await router.route("client", envelope(msg_type, payload))
    assert response is not None, "must answer, not swallow"
    assert response["type"] == "error"
    assert response["payload"]["code"] in VALIDATION_CODES, response["payload"]
    assert router.calls == [], "a malformed message must never reach a handler"


@pytest.mark.asyncio
async def test_payload_that_is_not_an_object_is_rejected(router):
    for bad in ([1, 2], "str", 5, True):
        response = await router.route("client", envelope("ping", payload=bad))
        assert response["type"] == "error"
        assert response["payload"]["code"] == ErrorCode.INVALID_MESSAGE_FORMAT.value


@pytest.mark.asyncio
async def test_error_frames_echo_the_message_id_and_a_bounded_value(router):
    raw = envelope("simulate_shot", {**GOOD_SIM, "exit_speed": "x" * 500})
    msg_id = json.loads(raw)["message_id"]
    response = await router.route("client", raw)
    assert response["in_reply_to"] == msg_id
    assert len(response["payload"]["details"]["value"]) < 200


@pytest.mark.asyncio
async def test_error_frames_are_always_valid_json(router):
    """A rejected NaN used to be echoed as a bare NaN literal, which the
    browser's JSON.parse rejects - the client never saw the error."""
    for payload in ({**GOOD_SIM, "exit_speed": float("nan")},
                    {**GOOD_SIM, "boundary_distance": float("inf")}):
        response = await router.route("client", envelope("simulate_shot", payload))
        assert response["type"] == "error"
        encoded = json.dumps(response, allow_nan=False)  # raises on NaN/Infinity
        assert "NaN" not in encoded.replace('"nan"', "")


@pytest.mark.asyncio
async def test_non_string_type_is_an_error_frame(router):
    for bad in (["ping"], {"t": "ping"}, 7, None):
        response = await router.route("client", envelope("ping", type=bad))
        assert response["type"] == "error"
        assert response["payload"]["code"] in (ErrorCode.INVALID_MESSAGE_TYPE.value,
                                               ErrorCode.MISSING_REQUIRED_FIELD.value)


@pytest.mark.asyncio
async def test_well_formed_messages_still_route(router):
    ok = [
        ("simulate_shot", {**GOOD_SIM, "boundary_distance": 70, "difficulty": "hard", "seed": 42}),
        ("simulate_shot", GOOD_SIM),  # optional fields absent
        ("set_field", {"fielders": GOOD_FIELD, "boundary_distance": 70}),
        ("set_field", {"fielders": [{"x": 1, "y": 2}]}),  # name optional
        ("create_profile", {"name": "Ben", "batting_hand": "left"}),
        ("update_profile", {"profile_id": 3}),
        ("select_profile", {"profile_id": "7"}),
        ("start_session", {"profile_id": "1", "field_config": GOOD_FIELD, "difficulty": "easy"}),
        ("manual_input", {"result": "wd"}),
        ("undo", {"session_id": "9"}),
        ("ping", {}),
        ("add_annotation", {"direction_deg": 35.0, "label": "4"}),
        ("start_recording", {"session_type": "both", "max_duration": 300}),
    ]
    for t, p in ok:
        response = await router.route("client", envelope(t, p))
        assert response["type"] == "handled", (t, p, response)
    assert len(router.calls) == len(ok)


@pytest.mark.asyncio
async def test_a_crash_inside_validation_is_contained(router, monkeypatch):
    """Belt and braces: even a bug in the validator must produce an error
    frame, not close the connection."""
    def boom(raw):
        raise RuntimeError("validator bug")

    monkeypatch.setattr(router, "validate_message", boom)
    response = await router.route("client", envelope("ping"))
    assert response["type"] == "error"
    assert response["payload"]["code"] == ErrorCode.INVALID_MESSAGE_FORMAT.value
