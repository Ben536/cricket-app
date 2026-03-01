#!/usr/bin/env python3
"""
Cricket App REST API Server

Provides REST endpoints for accessing detailed session and delivery data.
Complements the WebSocket server which sends slim real-time payloads.

Run with: python3 -m server.rest_api
Server runs on http://localhost:5003

Endpoints:
    GET /api/sessions/:session_id          - Full session data with all deliveries
    GET /api/sessions/:session_id/deliveries - All deliveries with full data
    GET /api/players/:player_id/sessions   - All sessions for a player
    GET /api/players/:player_id/stats      - Aggregated player statistics
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Optional

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.repository import (
    Repository,
    RecordNotFoundError,
    RepositoryError,
    Session,
    Player,
    Delivery,
)

# Default port
DEFAULT_PORT = 5003


def dataclass_to_dict(obj: Any) -> Any:
    """Convert dataclass instances to dictionaries recursively."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: dataclass_to_dict(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, list):
        return [dataclass_to_dict(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def delivery_to_dict(delivery: Delivery) -> dict:
    """Convert Delivery entity to dictionary with parsed JSON fields."""
    result = {
        "id": delivery.id,
        "session_id": delivery.session_id,
        "ball_number": delivery.ball_number,
        "timestamp": delivery.timestamp,
        "outcome": delivery.outcome,
        "runs": delivery.runs,
        "is_boundary": delivery.is_boundary,
        "is_aerial": delivery.is_aerial,
        "is_manual_input": delivery.is_manual_input,
        "description": delivery.description,
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
        # Trajectory data
        "trajectory": {
            "projected_distance": delivery.projected_distance,
            "max_height": delivery.max_height,
            "landing_x": delivery.landing_x,
            "landing_y": delivery.landing_y,
            "exit_speed": delivery.exit_speed,
            "horizontal_angle": delivery.horizontal_angle,
            "vertical_angle": delivery.vertical_angle,
            "bowling_speed": delivery.bowling_speed,
        },
        # Radar data
        "radar_data": {
            "frames_captured": delivery.radar_frames_captured,
            "detection_confidence": delivery.detection_confidence,
        },
        # Fielding data
        "fielding": {
            "fielder_position": _parse_json_field(delivery.fielder_position),
            "fielding_position": _parse_json_field(delivery.fielding_position),
            "end_position": _parse_json_field(delivery.end_position),
            "fielding_time": delivery.fielding_time,
            "collection_difficulty": delivery.collection_difficulty,
            "alignment_score": delivery.alignment_score,
            "priority_score": delivery.priority_score,
            "fielder_arrival_time": delivery.fielder_arrival_time,
            "ball_arrival_time": delivery.ball_arrival_time,
        },
        # Catch analysis (if applicable)
        "catch_analysis": _parse_json_field(delivery.catch_analysis),
    }
    return result


def _parse_json_field(value: Optional[str]) -> Optional[dict]:
    """Parse a JSON string field, returning None if invalid or empty."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def session_to_dict(session: Session) -> dict:
    """Convert Session entity to dictionary with parsed JSON fields."""
    return {
        "id": session.id,
        "player_id": session.player_id,
        "date": session.date,
        "field_config": _parse_json_field(session.field_config_json),
        "boundary_distance": session.boundary_distance,
        "difficulty": session.difficulty,
        "notes": session.notes,
        "is_completed": session.is_completed,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def player_to_dict(player: Player) -> dict:
    """Convert Player entity to dictionary."""
    return {
        "id": player.id,
        "user_id": player.user_id,
        "name": player.name,
        "batting_hand": player.batting_hand,
        "created_at": player.created_at,
        "updated_at": player.updated_at,
    }


class RestAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the REST API."""

    # Route patterns
    SESSION_PATTERN = re.compile(r'^/api/sessions/(\d+)$')
    SESSION_DELIVERIES_PATTERN = re.compile(r'^/api/sessions/(\d+)/deliveries$')
    PLAYER_SESSIONS_PATTERN = re.compile(r'^/api/players/(\d+)/sessions$')
    PLAYER_STATS_PATTERN = re.compile(r'^/api/players/(\d+)/stats$')

    def __init__(self, *args, repository: Repository = None, **kwargs):
        self.repository = repository or Repository()
        super().__init__(*args, **kwargs)

    def _send_cors_headers(self):
        """Send CORS headers for browser access."""
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_json_response(self, data: Any, status: int = 200):
        """Send a JSON response."""
        self.send_response(status)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def _send_error_response(self, message: str, code: str, status: int = 500):
        """Send an error response."""
        self.send_response(status)
        self._send_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        error_data = {
            "error": {
                "code": code,
                "message": message,
            }
        }
        self.wfile.write(json.dumps(error_data).encode('utf-8'))

    def do_OPTIONS(self):
        """Handle preflight CORS requests."""
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        try:
            # Match routes
            if match := self.SESSION_PATTERN.match(self.path):
                self._handle_get_session(int(match.group(1)))
            elif match := self.SESSION_DELIVERIES_PATTERN.match(self.path):
                self._handle_get_session_deliveries(int(match.group(1)))
            elif match := self.PLAYER_SESSIONS_PATTERN.match(self.path):
                self._handle_get_player_sessions(int(match.group(1)))
            elif match := self.PLAYER_STATS_PATTERN.match(self.path):
                self._handle_get_player_stats(int(match.group(1)))
            else:
                self._send_error_response("Not found", "E4004", 404)

        except RecordNotFoundError as e:
            self._send_error_response(e.message, e.code, 404)
        except RepositoryError as e:
            self._send_error_response(e.message, e.code, 500)
        except Exception as e:
            self._send_error_response(str(e), "E5001", 500)

    def _handle_get_session(self, session_id: int):
        """
        GET /api/sessions/:session_id

        Returns full session data with all deliveries.
        """
        # Get session
        session = self.repository.get_session(session_id)
        if not session:
            raise RecordNotFoundError("Session", session_id)

        # Get player info
        player = self.repository.get_profile(session.player_id)

        # Get all deliveries
        deliveries = self.repository.get_deliveries_by_session(session_id)

        # Get session summary
        summary = self.repository.get_session_summary(session_id)

        response = {
            "session": session_to_dict(session),
            "player": player_to_dict(player) if player else None,
            "deliveries": [delivery_to_dict(d) for d in deliveries],
            "summary": summary,
        }

        self._send_json_response(response)

    def _handle_get_session_deliveries(self, session_id: int):
        """
        GET /api/sessions/:session_id/deliveries

        Returns array of all deliveries with full data.
        """
        # Verify session exists
        session = self.repository.get_session(session_id)
        if not session:
            raise RecordNotFoundError("Session", session_id)

        # Get all deliveries
        deliveries = self.repository.get_deliveries_by_session(session_id)

        response = {
            "session_id": session_id,
            "count": len(deliveries),
            "deliveries": [delivery_to_dict(d) for d in deliveries],
        }

        self._send_json_response(response)

    def _handle_get_player_sessions(self, player_id: int):
        """
        GET /api/players/:player_id/sessions

        Returns list of all sessions for a player with summary stats.
        """
        # Verify player exists
        player = self.repository.get_profile(player_id)
        if not player:
            raise RecordNotFoundError("Player", player_id)

        # Get all sessions for player
        sessions = self.repository.get_sessions_by_player(player_id)

        # Get summaries for each session
        sessions_with_summaries = []
        for session in sessions:
            summary = self.repository.get_session_summary(session.id)
            sessions_with_summaries.append({
                "session": session_to_dict(session),
                "summary": summary,
            })

        response = {
            "player": player_to_dict(player),
            "count": len(sessions),
            "sessions": sessions_with_summaries,
        }

        self._send_json_response(response)

    def _handle_get_player_stats(self, player_id: int):
        """
        GET /api/players/:player_id/stats

        Returns aggregated statistics for a player.
        """
        # Verify player exists
        player = self.repository.get_profile(player_id)
        if not player:
            raise RecordNotFoundError("Player", player_id)

        # Get all sessions and deliveries for aggregation
        sessions = self.repository.get_sessions_by_player(player_id)

        # Aggregate stats
        total_runs = 0
        total_balls = 0
        total_fours = 0
        total_sixes = 0
        total_dots = 0
        total_wickets = 0
        wagon_wheel_data = []

        for session in sessions:
            deliveries = self.repository.get_deliveries_by_session(session.id)

            for delivery in deliveries:
                total_balls += 1
                total_runs += delivery.runs

                if delivery.outcome == '4':
                    total_fours += 1
                elif delivery.outcome == '6':
                    total_sixes += 1
                elif delivery.outcome == 'dot':
                    total_dots += 1
                elif delivery.outcome == 'caught':
                    total_wickets += 1

                # Add to wagon wheel if we have position data
                end_position = _parse_json_field(delivery.end_position)
                if end_position and 'x' in end_position and 'y' in end_position:
                    wagon_wheel_data.append({
                        "session_id": session.id,
                        "delivery_id": delivery.id,
                        "ball_number": delivery.ball_number,
                        "end_x": end_position['x'],
                        "end_y": end_position['y'],
                        "runs": delivery.runs,
                        "outcome": delivery.outcome,
                        "is_boundary": delivery.is_boundary,
                    })

        # Calculate derived stats
        average = total_runs / total_wickets if total_wickets > 0 else total_runs
        strike_rate = (total_runs / total_balls * 100) if total_balls > 0 else 0
        boundary_percentage = ((total_fours + total_sixes) / total_balls * 100) if total_balls > 0 else 0
        dot_ball_percentage = (total_dots / total_balls * 100) if total_balls > 0 else 0

        response = {
            "player": player_to_dict(player),
            "stats": {
                "total_sessions": len(sessions),
                "total_runs": total_runs,
                "total_balls": total_balls,
                "average": round(average, 2),
                "strike_rate": round(strike_rate, 2),
                "fours": total_fours,
                "sixes": total_sixes,
                "dots": total_dots,
                "wickets": total_wickets,
                "boundary_percentage": round(boundary_percentage, 2),
                "dot_ball_percentage": round(dot_ball_percentage, 2),
            },
            "wagon_wheel": wagon_wheel_data,
        }

        self._send_json_response(response)

    def log_message(self, format, *args):
        """Custom log format."""
        print(f"[REST API] {args[0]}")


def make_handler(repository: Repository):
    """Create a handler class with injected repository."""
    class HandlerWithRepo(RestAPIHandler):
        def __init__(self, *args, **kwargs):
            # Inject repository before calling parent __init__
            self.repository = repository
            # Call grandparent __init__ directly to avoid double initialization
            BaseHTTPRequestHandler.__init__(self, *args, **kwargs)
    return HandlerWithRepo


class RestAPIServer:
    """REST API server wrapper for easier management."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = DEFAULT_PORT,
        repository: Optional[Repository] = None
    ):
        self.host = host
        self.port = port
        self.repository = repository or Repository()
        self._server: Optional[HTTPServer] = None

    def start(self):
        """Start the REST API server (blocking)."""
        handler = make_handler(self.repository)
        self._server = HTTPServer((self.host, self.port), handler)
        print(f"REST API server running on http://{self.host}:{self.port}")
        print("Endpoints:")
        print("  GET /api/sessions/:session_id          - Full session data")
        print("  GET /api/sessions/:session_id/deliveries - All deliveries")
        print("  GET /api/players/:player_id/sessions   - Player's sessions")
        print("  GET /api/players/:player_id/stats      - Player statistics")
        print("\nPress Ctrl+C to stop")
        self._server.serve_forever()

    def stop(self):
        """Stop the REST API server."""
        if self._server:
            self._server.shutdown()
            self._server = None


def run_server(host: str = "localhost", port: int = DEFAULT_PORT):
    """Run the REST API server."""
    server = RestAPIServer(host=host, port=port)
    try:
        server.start()
    except KeyboardInterrupt:
        print("\nShutting down REST API server...")
        server.stop()


if __name__ == '__main__':
    run_server()
