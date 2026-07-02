"""Quick check that a capture type records over WebSocket (run on the Pi).
Usage: python3 ws_type_test.py <session_type>
"""
import asyncio, json, uuid, sys, os
from datetime import datetime, timezone
import websockets

TYPE = sys.argv[1] if len(sys.argv) > 1 else "foil_ball"

def env(t, p=None):
    return json.dumps({
        "type": t, "message_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": p or {},
    })

async def recv_until(ws, t, to=6):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), to))
        if m.get("type") == t:
            return m
        if m.get("type") == "error":
            raise SystemExit(f"server error: {m['payload']}")

async def main():
    async with websockets.connect("ws://localhost:5002") as ws:
        await ws.send(env("start_recording", {"session_type": TYPE, "max_duration": 5}))
        s = await recv_until(ws, "recording_started")
        print("started type:", s["payload"]["session_type"])
        await asyncio.sleep(4)  # real radar streams ~10Hz; give it time to capture frames
        await ws.send(env("add_annotation", {"label": TYPE, "direction_deg": 20.0}))
        await recv_until(ws, "annotation_added")
        await ws.send(env("stop_recording"))
        p = (await recv_until(ws, "recording_stopped"))["payload"]
        print("stopped: frames=%s marks=%s file=%s" % (
            p["frame_count"], p["annotation_count"], os.path.basename(p["file_path"])))
        assert ("/%s/" % TYPE) in p["file_path"], "file not in the right type dir"
        assert p["frame_count"] > 5, "no frames captured"
        print("OK: '%s' recorded with real radar -> %s" % (TYPE, p["file_path"]))

asyncio.run(main())
