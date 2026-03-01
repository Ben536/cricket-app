"""
Cricket App Server Package

Real-time WebSocket server and REST API for the Cricket App.
Handles WebSocket connections, message routing, state management,
and provides REST endpoints for historical data access.

Usage:
    # WebSocket Server (real-time)
    from server import CricketWebSocketServer
    server = CricketWebSocketServer(host="0.0.0.0", port=5002)
    await server.start()

    # REST API Server (historical data)
    from server import RestAPIServer
    api_server = RestAPIServer(host="0.0.0.0", port=5003)
    api_server.start()
"""

from server.websocket_server import CricketWebSocketServer
from server.connection_manager import ConnectionManager, ClientConnection
from server.message_router import MessageRouter
from server.session_manager import SessionManager
from server.handlers import MessageHandlers, register_handlers
from server.rest_api import RestAPIServer

__all__ = [
    # WebSocket Server
    "CricketWebSocketServer",
    "ConnectionManager",
    "ClientConnection",
    "MessageRouter",
    "SessionManager",
    "MessageHandlers",
    "register_handlers",
    # REST API Server
    "RestAPIServer",
]

__version__ = "1.0.0"
