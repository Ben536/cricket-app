"""
Cricket App WebSocket Server

Main WebSocket server that handles connections, message routing,
and heartbeat management.

Usage:
    server = CricketWebSocketServer(host="0.0.0.0", port=5002)
    await server.start()
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import websockets
from websockets.server import WebSocketServerProtocol, serve

# Import from local server modules (types defined there to avoid import issues)
from server.connection_manager import (
    ConnectionState,
    SessionState,
    generate_message_id,
    create_timestamp,
)
from server.message_router import Difficulty

from server.connection_manager import ConnectionManager, ClientConnection, ClientState
from server.message_router import MessageRouter, ErrorCode, create_error_response
from server.session_manager import SessionManager

# Database repository
from db.repository import Repository

logger = logging.getLogger(__name__)

# Server version
SERVER_VERSION = "1.0.0"


@dataclass
class ServerConfig:
    """Server configuration options."""
    host: str = "0.0.0.0"
    port: int = 5002
    heartbeat_interval: float = 30.0  # seconds
    heartbeat_timeout: float = 60.0   # seconds before client is considered dead
    max_clients: int = 100
    connection_timeout: float = 10.0  # seconds for connection handshake


class CricketWebSocketServer:
    """
    WebSocket server for the Cricket App.

    Features:
    - Multiple concurrent client connections
    - Message routing with validation
    - Heartbeat ping/pong every 30s
    - Graceful disconnect handling
    - Session-based broadcasting
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5002,
        config: Optional[ServerConfig] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.config = config or ServerConfig(host=host, port=port)

        # Core components
        self.connection_manager = ConnectionManager()
        self.message_router = MessageRouter()
        self.session_manager = SessionManager()

        # Database repository
        if db_path:
            self.repository = Repository(db_path)
        else:
            self.repository = Repository()

        # Server state
        self._server: Optional[websockets.WebSocketServer] = None
        self._running = False
        self._start_time: Optional[float] = None

        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None

        # Game state
        self._current_difficulty = Difficulty.MEDIUM
        self._session_state = SessionState.NONE

        # Message handlers (will be set by _register_handlers)
        self._handlers = None

        # Register handlers with game engine integration
        self._register_handlers()

        logger.info(
            f"CricketWebSocketServer initialized: "
            f"{self.config.host}:{self.config.port}"
        )

    def _register_handlers(self) -> None:
        """Register message handlers with game engine integration."""
        # Import handlers module here to avoid circular imports
        from server.handlers import register_handlers

        # Register all handlers
        self._handlers = register_handlers(
            server=self,
            repository=self.repository,
            session_manager=self.session_manager,
        )

        logger.info("Registered game engine message handlers")

    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._running:
            logger.warning("Server already running")
            return

        self._running = True
        self._start_time = time.time()

        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        # Start WebSocket server
        self._server = await serve(
            self._handle_connection,
            self.config.host,
            self.config.port,
            ping_interval=None,  # We handle our own heartbeats
            ping_timeout=None,
            close_timeout=5,
        )

        logger.info(
            f"WebSocket server started on "
            f"ws://{self.config.host}:{self.config.port}"
        )

    async def stop(self) -> None:
        """Stop the WebSocket server gracefully."""
        if not self._running:
            return

        logger.info("Stopping WebSocket server...")
        self._running = False

        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Close all client connections
        for client in self.connection_manager.get_all_clients():
            try:
                await client.websocket.close(1001, "Server shutting down")
            except Exception:
                pass

        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        logger.info("WebSocket server stopped")

    async def run_forever(self) -> None:
        """Start server and run until interrupted."""
        await self.start()

        # Wait for shutdown signal
        stop_event = asyncio.Event()

        def signal_handler():
            logger.info("Received shutdown signal")
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, signal_handler)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        await stop_event.wait()
        await self.stop()

    async def _handle_connection(
        self,
        websocket: WebSocketServerProtocol,
        path: str = "",
    ) -> None:
        """Handle a new WebSocket connection."""
        client_id = generate_message_id()

        logger.info(f"New connection from {websocket.remote_address}")

        # Check max clients
        if self.connection_manager.client_count >= self.config.max_clients:
            logger.warning("Max clients reached, rejecting connection")
            await websocket.close(1013, "Server at capacity")
            return

        # Add client
        connection = await self.connection_manager.add_client(
            client_id=client_id,
            websocket=websocket,
        )

        try:
            # Send connection status
            await self._send_connection_status(client_id)

            # Handle messages
            async for raw_message in websocket:
                if not self._running:
                    break

                # Update activity
                self.connection_manager.update_activity(client_id)

                # Route message
                response = await self.message_router.route(client_id, raw_message)

                if response:
                    await self.connection_manager.send_to_client(
                        client_id, response
                    )

        except websockets.exceptions.ConnectionClosedError as e:
            logger.info(f"Connection closed: {client_id} - {e.code} {e.reason}")

        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"Connection closed normally: {client_id}")

        except Exception as e:
            logger.exception(f"Error handling connection {client_id}: {e}")

        finally:
            # Remove client
            await self.connection_manager.remove_client(client_id)

    async def _send_connection_status(self, client_id: str) -> None:
        """Send connection status message to a client."""
        uptime = time.time() - self._start_time if self._start_time else 0

        message = {
            "type": "connection_status",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": None,
            "payload": {
                "connection_state": ConnectionState.CONNECTED.value,
                "session_state": self._session_state.value,
                "radar_connected": False,  # Will be updated by radar integration
                "radar_status": None,
                "server_version": SERVER_VERSION,
                "uptime_seconds": uptime,
            }
        }

        await self.connection_manager.send_to_client(client_id, message)

    async def _heartbeat_loop(self) -> None:
        """Background task to send heartbeats to clients."""
        logger.info("Heartbeat loop started")

        while self._running:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)

                if not self._running:
                    break

                # Check for inactive clients
                inactive = self.connection_manager.get_inactive_clients(
                    self.config.heartbeat_timeout
                )

                for client_id in inactive:
                    logger.warning(f"Client {client_id} timed out")
                    connection = self.connection_manager.get_client(client_id)
                    if connection:
                        try:
                            await connection.websocket.close(
                                1002, "Heartbeat timeout"
                            )
                        except Exception:
                            pass
                        await self.connection_manager.remove_client(client_id)

                # Send server heartbeat to all clients
                heartbeat = {
                    "type": "connection_status",
                    "message_id": generate_message_id(),
                    "timestamp": create_timestamp(),
                    "in_reply_to": None,
                    "payload": {
                        "connection_state": ConnectionState.CONNECTED.value,
                        "session_state": self._session_state.value,
                        "radar_connected": False,
                        "radar_status": None,
                        "server_version": SERVER_VERSION,
                        "uptime_seconds": time.time() - self._start_time if self._start_time else 0,
                    }
                }

                await self.connection_manager.broadcast_to_all(heartbeat)

                logger.debug(
                    f"Heartbeat sent to {self.connection_manager.client_count} clients"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in heartbeat loop: {e}")
                await asyncio.sleep(5)

        logger.info("Heartbeat loop stopped")

    async def _cleanup_loop(self) -> None:
        """Background task for periodic cleanup."""
        logger.info("Cleanup loop started")

        while self._running:
            try:
                # Run cleanup every 5 minutes
                await asyncio.sleep(300)

                if not self._running:
                    break

                # Future: cleanup old sessions, compact message history, etc.
                logger.debug("Running periodic cleanup")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Error in cleanup loop: {e}")
                await asyncio.sleep(60)

        logger.info("Cleanup loop stopped")

    @property
    def uptime(self) -> float:
        """Server uptime in seconds."""
        if self._start_time:
            return time.time() - self._start_time
        return 0.0

    @property
    def is_running(self) -> bool:
        """Whether the server is running."""
        return self._running


# Main entry point
async def main():
    """Run the WebSocket server."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Create and run server
    server = CricketWebSocketServer(
        host="0.0.0.0",
        port=5002,
    )

    await server.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
