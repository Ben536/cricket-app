#!/usr/bin/env python3
"""
Test script for Cricket App WebSocket Server

Run this to verify the server works:
    python3 -m server.test_server
"""

import asyncio
import json
import sys
import tempfile
import os

sys.path.insert(0, '/Users/Ben/Documents/cricket-app')

import websockets

from server.websocket_server import CricketWebSocketServer
from server.connection_manager import generate_message_id, create_timestamp
from db.repository import Repository
from pathlib import Path


def setup_test_database():
    """Create a temporary test database with full schema."""
    # Create a temp file for the database
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    db_path = Path(db_path)

    # Run migrations to get full schema
    from db.migrate import MigrationRunner
    runner = MigrationRunner(db_path)
    runner.connect()
    runner.ensure_migrations_table()
    count = runner.run_all_pending()
    runner.close()

    print(f"[Setup] Created test database at {db_path} ({count} migrations)")
    return db_path


async def test_client(db_path: Path):
    """Test client that connects and sends messages."""
    await asyncio.sleep(0.5)  # Wait for server to start

    uri = "ws://localhost:5002"
    print(f"[Client] Connecting to {uri}...")

    try:
        async with websockets.connect(uri) as websocket:
            print("[Client] Connected!")

            # Receive connection status
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'connection_status', f"Expected connection_status, got {msg['type']}"
            print(f"[Client] Server version: {msg['payload']['server_version']}")

            # Send ping
            ping_msg = {
                "type": "ping",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
            }
            print(f"[Client] Sending ping...")
            await websocket.send(json.dumps(ping_msg))

            # Receive pong
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'pong', f"Expected pong, got {msg['type']}"
            assert msg['in_reply_to'] == ping_msg['message_id'], "Pong should reference ping message_id"
            print("[Client] PASS: Ping/Pong works")

            # Test create_profile
            create_profile_msg = {
                "type": "create_profile",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "name": "Test Player",
                    "batting_hand": "right"
                }
            }
            print(f"[Client] Sending create_profile...")
            await websocket.send(json.dumps(create_profile_msg))

            # Receive session_state
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'session_state', f"Expected session_state, got {msg['type']}"
            assert len(msg['payload']['profiles']) >= 1, "Expected at least one profile"
            profile_id = msg['payload']['profiles'][0]['id']
            print(f"[Client] PASS: Created profile with id={profile_id}")

            # Test start_session
            start_session_msg = {
                "type": "start_session",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "profile_id": profile_id,
                    "difficulty": "medium"
                }
            }
            print(f"[Client] Sending start_session...")
            await websocket.send(json.dumps(start_session_msg))

            # Receive session_state
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'session_state', f"Expected session_state, got {msg['type']}"
            assert msg['payload']['session'] is not None, "Expected active session"
            session_id = msg['payload']['session']['id']
            print(f"[Client] PASS: Started session with id={session_id}")

            # Test set_difficulty
            set_difficulty_msg = {
                "type": "set_difficulty",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "difficulty": "hard"
                }
            }
            print(f"[Client] Sending set_difficulty...")
            await websocket.send(json.dumps(set_difficulty_msg))

            # Receive session_state
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'session_state', f"Expected session_state, got {msg['type']}"
            assert msg['payload']['difficulty'] == 'hard', f"Expected difficulty=hard"
            print("[Client] PASS: Set difficulty to hard")

            # Test manual_input (simple runs)
            manual_input_msg = {
                "type": "manual_input",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "result": "2"
                }
            }
            print(f"[Client] Sending manual_input (2 runs)...")
            await websocket.send(json.dumps(manual_input_msg))

            # Receive session_state
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'session_state', f"Expected session_state, got {msg['type']}"
            assert msg['payload']['session']['runs'] == 2, f"Expected 2 runs"
            assert msg['payload']['session']['balls'] == 1, f"Expected 1 ball"
            print("[Client] PASS: Manual input recorded 2 runs")

            # Test manual_input (boundary 4 - recorded verbatim, no simulation)
            manual_input_msg = {
                "type": "manual_input",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "result": "4",
                    "is_boundary": True
                }
            }
            print(f"[Client] Sending manual_input (boundary 4)...")
            await websocket.send(json.dumps(manual_input_msg))

            # Manual input is recorded verbatim and only returns session_state
            responses = []
            while True:
                response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                msg = json.loads(response)
                responses.append(msg)
                print(f"[Client] Received: {msg['type']}")
                if msg['type'] == 'session_state':
                    break

            # Check we got the right messages
            msg_types = [m['type'] for m in responses]
            assert 'session_state' in msg_types, "Expected session_state"
            session_state = next(m for m in responses if m['type'] == 'session_state')
            assert session_state['payload']['session']['runs'] >= 4, f"Expected at least 4 runs"
            print("[Client] PASS: Boundary 4 recorded")

            # Test undo
            undo_msg = {
                "type": "undo",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "session_id": session_id
                }
            }
            print(f"[Client] Sending undo...")
            await websocket.send(json.dumps(undo_msg))

            # Receive session_state
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'session_state', f"Expected session_state, got {msg['type']}"
            # After undo, we should have fewer balls
            assert msg['payload']['session']['balls'] == 1, f"Expected 1 ball after undo"
            print("[Client] PASS: Undo worked")

            # Test end_session
            end_session_msg = {
                "type": "end_session",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": {
                    "session_id": session_id
                }
            }
            print(f"[Client] Sending end_session...")
            await websocket.send(json.dumps(end_session_msg))

            # Receive session_state
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'session_state', f"Expected session_state, got {msg['type']}"
            assert msg['payload']['session'] is None, "Expected no active session"
            print("[Client] PASS: Session ended")

            # Test invalid message type
            invalid_msg = {
                "type": "invalid_type",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
            }
            print(f"[Client] Sending invalid message...")
            await websocket.send(json.dumps(invalid_msg))

            # Receive error
            response = await websocket.recv()
            msg = json.loads(response)
            print(f"[Client] Received: {msg['type']}")
            assert msg['type'] == 'error', f"Expected error, got {msg['type']}"
            assert msg['payload']['code'] == 'E3002', f"Expected E3002, got {msg['payload']['code']}"
            print("[Client] PASS: Error handling works")

            print("\n[Client] All tests passed!")
            return True

    except Exception as e:
        print(f"[Client] Error: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run server and client tests."""
    print("=" * 50)
    print("Cricket App WebSocket Server Test")
    print("=" * 50)

    # Setup test database
    db_path = setup_test_database()

    # Create server with test database
    server = CricketWebSocketServer(host="127.0.0.1", port=5002, db_path=db_path)

    try:
        # Start server
        await server.start()
        print(f"[Server] Running on ws://127.0.0.1:5002")

        # Run client tests
        success = await test_client(db_path)

        if success:
            print("\n" + "=" * 50)
            print("ALL TESTS PASSED")
            print("=" * 50)
        else:
            print("\n" + "=" * 50)
            print("TESTS FAILED")
            print("=" * 50)
            sys.exit(1)

    finally:
        # Stop server
        await server.stop()
        print("[Server] Stopped")

        # Cleanup test database
        try:
            os.unlink(db_path)
            print(f"[Cleanup] Removed test database")
        except:
            pass


if __name__ == "__main__":
    asyncio.run(main())
