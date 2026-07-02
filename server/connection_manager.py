"""
Connection Manager for Cricket App WebSocket Server

Tracks connected clients, manages sessions, and handles broadcasts.
Supports multiple clients per session (spectators) and reconnection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from enum import Enum

import websockets
from websockets.server import WebSocketServerProtocol

import uuid

# Define essential types locally to avoid import issues with complex dataclass inheritance
class ConnectionState(str, Enum):
    """Connection states from api_types."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class SessionState(str, Enum):
    """Session states from api_types."""
    NONE = "none"
    ACTIVE = "active"
    ENDED = "ended"


def generate_message_id() -> str:
    """Generate a UUID v4 for message IDs."""
    return str(uuid.uuid4())


def create_timestamp() -> str:
    """Create an ISO 8601 timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

logger = logging.getLogger(__name__)


class ClientState(str, Enum):
    """Internal client connection state."""
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"


@dataclass
class ClientConnection:
    """Represents a connected WebSocket client."""
    client_id: str
    websocket: WebSocketServerProtocol
    connected_at: datetime
    session_id: Optional[str] = None
    last_message_id: Optional[str] = None
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    state: ClientState = ClientState.CONNECTING

    # Reconnection tracking
    reconnect_count: int = 0
    previous_client_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/debugging."""
        return {
            "client_id": self.client_id,
            "connected_at": self.connected_at.isoformat(),
            "session_id": self.session_id,
            "last_message_id": self.last_message_id,
            "last_activity": self.last_activity.isoformat(),
            "state": self.state.value,
            "reconnect_count": self.reconnect_count,
        }


class ConnectionManager:
    """
    Manages WebSocket client connections.

    Responsibilities:
    - Track connected clients
    - Support multiple clients per session (spectators)
    - Broadcast messages to clients in a session
    - Handle reconnection with state recovery
    """

    def __init__(self) -> None:
        # Map client_id -> ClientConnection
        self._clients: dict[str, ClientConnection] = {}

        # Map session_id -> set of client_ids
        self._session_clients: dict[str, set[str]] = {}

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        # Message history for reconnection (last N messages per session)
        self._message_history: dict[str, list[dict]] = {}
        self._history_limit = 100  # Keep last 100 messages per session

        logger.info("ConnectionManager initialized")

    @property
    def client_count(self) -> int:
        """Number of connected clients."""
        return len(self._clients)

    @property
    def session_count(self) -> int:
        """Number of active sessions with clients."""
        return len(self._session_clients)

    async def add_client(
        self,
        client_id: str,
        websocket: WebSocketServerProtocol,
        reconnect_client_id: Optional[str] = None,
    ) -> ClientConnection:
        """
        Add a new client connection.

        Args:
            client_id: Unique identifier for this client
            websocket: The WebSocket connection
            reconnect_client_id: If reconnecting, the previous client_id

        Returns:
            The created ClientConnection
        """
        async with self._lock:
            now = datetime.now(timezone.utc)

            # Handle reconnection
            previous_session_id = None
            reconnect_count = 0

            if reconnect_client_id and reconnect_client_id in self._clients:
                old_conn = self._clients[reconnect_client_id]
                previous_session_id = old_conn.session_id
                reconnect_count = old_conn.reconnect_count + 1

                # Remove old connection
                await self._remove_client_internal(reconnect_client_id)

                logger.info(
                    f"Client reconnecting: {reconnect_client_id} -> {client_id}, "
                    f"session={previous_session_id}, count={reconnect_count}"
                )

            # Create new connection
            connection = ClientConnection(
                client_id=client_id,
                websocket=websocket,
                connected_at=now,
                session_id=previous_session_id,
                state=ClientState.CONNECTED,
                reconnect_count=reconnect_count,
                previous_client_id=reconnect_client_id,
            )

            self._clients[client_id] = connection

            # Re-add to session if reconnecting
            if previous_session_id:
                if previous_session_id not in self._session_clients:
                    self._session_clients[previous_session_id] = set()
                self._session_clients[previous_session_id].add(client_id)

            logger.info(
                f"Client connected: {client_id}, "
                f"total_clients={self.client_count}"
            )

            return connection

    async def remove_client(self, client_id: str) -> Optional[ClientConnection]:
        """
        Remove a client connection.

        Args:
            client_id: The client to remove

        Returns:
            The removed connection, or None if not found
        """
        async with self._lock:
            return await self._remove_client_internal(client_id)

    async def _remove_client_internal(self, client_id: str) -> Optional[ClientConnection]:
        """Internal remove without lock (must be called with lock held)."""
        if client_id not in self._clients:
            return None

        connection = self._clients.pop(client_id)
        connection.state = ClientState.DISCONNECTED

        # Remove from session
        if connection.session_id:
            session_clients = self._session_clients.get(connection.session_id)
            if session_clients:
                session_clients.discard(client_id)
                if not session_clients:
                    del self._session_clients[connection.session_id]

        logger.info(
            f"Client disconnected: {client_id}, "
            f"session={connection.session_id}, "
            f"total_clients={self.client_count}"
        )

        return connection

    def get_client(self, client_id: str) -> Optional[ClientConnection]:
        """Get a client connection by ID."""
        return self._clients.get(client_id)

    async def join_session(self, client_id: str, session_id: str) -> bool:
        """
        Add a client to a session.

        Args:
            client_id: The client to add
            session_id: The session to join

        Returns:
            True if successful, False if client not found
        """
        async with self._lock:
            connection = self._clients.get(client_id)
            if not connection:
                logger.warning(f"join_session: client {client_id} not found")
                return False

            # Leave current session if any
            if connection.session_id and connection.session_id != session_id:
                old_clients = self._session_clients.get(connection.session_id)
                if old_clients:
                    old_clients.discard(client_id)
                    if not old_clients:
                        del self._session_clients[connection.session_id]

            # Join new session
            connection.session_id = session_id
            if session_id not in self._session_clients:
                self._session_clients[session_id] = set()
            self._session_clients[session_id].add(client_id)

            logger.info(
                f"Client {client_id} joined session {session_id}, "
                f"session_clients={len(self._session_clients[session_id])}"
            )

            return True

    async def leave_session(self, client_id: str) -> bool:
        """
        Remove a client from their current session.

        Args:
            client_id: The client to remove

        Returns:
            True if successful, False if client not found or not in session
        """
        async with self._lock:
            connection = self._clients.get(client_id)
            if not connection or not connection.session_id:
                return False

            session_id = connection.session_id
            session_clients = self._session_clients.get(session_id)

            if session_clients:
                session_clients.discard(client_id)
                if not session_clients:
                    del self._session_clients[session_id]

            connection.session_id = None

            logger.info(f"Client {client_id} left session {session_id}")

            return True

    def get_session_clients(self, session_id: str) -> list[ClientConnection]:
        """Get all clients in a session."""
        client_ids = self._session_clients.get(session_id, set())
        return [
            self._clients[cid]
            for cid in client_ids
            if cid in self._clients
        ]

    async def send_to_client(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> bool:
        """
        Send a message to a specific client.

        Args:
            client_id: The target client
            message: The message to send (will be JSON encoded)

        Returns:
            True if sent successfully, False otherwise
        """
        connection = self._clients.get(client_id)
        if not connection or connection.state != ClientState.CONNECTED:
            logger.warning(f"send_to_client: client {client_id} not available")
            return False

        try:
            message_json = json.dumps(message)
            await connection.websocket.send(message_json)
            # NOTE: last_activity is deliberately NOT updated here. Activity
            # means INBOUND traffic - if outbound sends refreshed it, the 30s
            # heartbeat broadcast would forever reset the 60s dead-client
            # timeout and a half-open connection could never be reaped.

            # Track message for reconnection
            if "message_id" in message:
                connection.last_message_id = message["message_id"]

            logger.debug(
                f"Sent to {client_id}: type={message.get('type')}, "
                f"id={message.get('message_id', 'N/A')[:8]}"
            )
            return True

        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"Connection closed while sending to {client_id}")
            await self.remove_client(client_id)
            return False
        except Exception as e:
            logger.error(f"Error sending to {client_id}: {e}")
            return False

    async def broadcast_to_session(
        self,
        session_id: str,
        message: dict[str, Any],
        exclude_client: Optional[str] = None,
    ) -> int:
        """
        Broadcast a message to all clients in a session.

        Args:
            session_id: The target session
            message: The message to broadcast
            exclude_client: Optional client to exclude (e.g., the sender)

        Returns:
            Number of clients the message was sent to
        """
        client_ids = self._session_clients.get(session_id, set()).copy()

        if exclude_client:
            client_ids.discard(exclude_client)

        if not client_ids:
            return 0

        # Store in message history for reconnection
        self._store_message(session_id, message)

        # Send to all clients
        sent_count = 0
        for client_id in client_ids:
            if await self.send_to_client(client_id, message):
                sent_count += 1

        logger.debug(
            f"Broadcast to session {session_id}: "
            f"type={message.get('type')}, sent={sent_count}/{len(client_ids)}"
        )

        return sent_count

    async def broadcast_to_all(
        self,
        message: dict[str, Any],
        exclude_client: Optional[str] = None,
    ) -> int:
        """
        Broadcast a message to all connected clients.

        Args:
            message: The message to broadcast
            exclude_client: Optional client to exclude

        Returns:
            Number of clients the message was sent to
        """
        client_ids = set(self._clients.keys())

        if exclude_client:
            client_ids.discard(exclude_client)

        sent_count = 0
        for client_id in client_ids:
            if await self.send_to_client(client_id, message):
                sent_count += 1

        logger.debug(
            f"Broadcast to all: type={message.get('type')}, "
            f"sent={sent_count}/{len(client_ids)}"
        )

        return sent_count

    def _store_message(self, session_id: str, message: dict[str, Any]) -> None:
        """Store message in history for reconnection recovery."""
        if session_id not in self._message_history:
            self._message_history[session_id] = []

        history = self._message_history[session_id]
        history.append(message)

        # Trim to limit
        if len(history) > self._history_limit:
            self._message_history[session_id] = history[-self._history_limit:]

    def get_messages_since(
        self,
        session_id: str,
        last_message_id: str,
    ) -> list[dict[str, Any]]:
        """
        Get messages since a given message ID (for reconnection).

        Args:
            session_id: The session to get messages for
            last_message_id: The last message the client received

        Returns:
            List of messages since the given ID
        """
        history = self._message_history.get(session_id, [])

        # Find the index of the last message
        last_idx = -1
        for i, msg in enumerate(history):
            if msg.get("message_id") == last_message_id:
                last_idx = i
                break

        # Return messages after that point
        if last_idx >= 0:
            return history[last_idx + 1:]

        # If message not found, return all (client might be very out of date)
        return history

    def update_activity(self, client_id: str) -> None:
        """Update last activity timestamp for a client."""
        connection = self._clients.get(client_id)
        if connection:
            connection.last_activity = datetime.now(timezone.utc)

    def get_inactive_clients(self, timeout_seconds: float) -> list[str]:
        """Get clients that haven't had activity within timeout."""
        now = datetime.now(timezone.utc)
        inactive = []

        for client_id, conn in self._clients.items():
            delta = (now - conn.last_activity).total_seconds()
            if delta > timeout_seconds:
                inactive.append(client_id)

        return inactive

    def get_all_clients(self) -> list[ClientConnection]:
        """Get all connected clients."""
        return list(self._clients.values())

    def cleanup_session(self, session_id: str) -> None:
        """Remove all session data (call when session ends)."""
        # Remove message history
        self._message_history.pop(session_id, None)

        # Note: Don't remove clients, they might start a new session
        logger.info(f"Cleaned up session data: {session_id}")
