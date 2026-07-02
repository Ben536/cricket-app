"""End-to-end test for data-gathering over WebSocket (run on the Pi).

Drives: start_recording (both) -> add_annotation x2 (with direction) ->
get_recording_status -> stop_recording, and verifies the saved JSONL.
"""
import asyncio, json, uuid, glob, os
from datetime import datetime, timezone
import websockets

URI = "ws://localhost:5002"

def envelope(mtype, payload=None):
    return json.dumps({
        "type": mtype,
        "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload or {},
    })

async def recv_until(ws, mtype, timeout=5):
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("type") == mtype:
            return msg

async def main():
    async with websockets.connect(URI) as ws:
        await ws.send(envelope("start_recording", {"session_type": "both", "max_duration": 4}))
        started = await recv_until(ws, "recording_started")
        print("started:", started["payload"]["session_type"], "max:", started["payload"]["max_duration"],
              "mock:", started["payload"].get("mock"))

        await asyncio.sleep(0.6)
        await ws.send(envelope("add_annotation", {"direction_deg": 42.5, "x": 0.5, "y": 0.7, "label": "4"}))
        added = await recv_until(ws, "annotation_added")
        print("annotation_added count:", added["payload"]["annotation_count"], "ann:", added["payload"]["annotation"])

        await asyncio.sleep(0.4)
        await ws.send(envelope("add_annotation", {"direction_deg": -65.0, "x": -0.8, "y": 0.3}))
        await recv_until(ws, "annotation_added")

        await ws.send(envelope("get_recording_status"))
        st = await recv_until(ws, "recording_status")
        print("status: recording=%s frames=%s marks=%s" % (
            st["payload"]["is_recording"], st["payload"]["frame_count"], st["payload"]["annotation_count"]))

        await ws.send(envelope("stop_recording"))
        stopped = await recv_until(ws, "recording_stopped")
        p = stopped["payload"]
        print("stopped: frames=%s marks=%s dur=%.1fs file=%s" % (
            p["frame_count"], p["annotation_count"], p["duration_seconds"], os.path.basename(p["file_path"])))

        assert p["annotation_count"] == 2, "expected 2 annotations"
        assert p["frame_count"] > 5, "expected frames captured (mock or real)"

        # Verify the saved JSONL contains frames + the direction annotation
        kinds, dirs = {}, []
        with open(p["file_path"]) as f:
            for line in f:
                o = json.loads(line)
                kinds[o["type"]] = kinds.get(o["type"], 0) + 1
                if o["type"] == "annotation" and "direction_deg" in o:
                    dirs.append(o["direction_deg"])
        print("jsonl line kinds:", kinds, "directions:", dirs)
        assert kinds.get("frame", 0) > 5 and kinds.get("annotation") == 2
        assert 42.5 in dirs
        print("\nEND-TO-END WS DATA-GATHERING TEST PASSED ✓")

asyncio.run(main())
