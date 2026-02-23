#!/usr/bin/env python3
"""
Simple API server for the cricket game engine.

Run with: python3 api.py
Server runs on http://localhost:5001
"""

import json
import math
from http.server import HTTPServer, BaseHTTPRequestHandler
from game_engine import simulate_delivery


def calculate_trajectory(speed_kmh: float, h_angle: float, v_angle: float) -> dict:
    """Calculate ball trajectory from speed and angles."""
    speed_ms = speed_kmh / 3.6
    h_rad = math.radians(h_angle)
    v_rad = math.radians(v_angle)

    v_horizontal = speed_ms * math.cos(v_rad)
    v_vertical = speed_ms * math.sin(v_rad)
    g = 9.81

    if v_vertical > 0:
        t_up = v_vertical / g
        apex_height = 1 + (v_vertical ** 2) / (2 * g)
        t_down = math.sqrt(2 * apex_height / g)
        t_flight = t_up + t_down
        max_height = apex_height
    else:
        t_flight = math.sqrt(2 * 1.0 / g)
        max_height = 1.0

    distance = v_horizontal * t_flight
    # +angle = off side, field coords: +x = leg side, +y = toward bowler
    landing_x = -distance * math.sin(h_rad)
    landing_y = distance * math.cos(h_rad)  # Positive = toward bowler

    return {
        'projected_distance': distance,
        'max_height': max_height,
        'landing_x': landing_x,
        'landing_y': landing_y,
    }


# Default field configuration
DEFAULT_FIELD = [
    {'x': 0, 'y': 3, 'name': 'wicketkeeper'},
    {'x': 5, 'y': 4, 'name': 'first slip'},
    {'x': 7, 'y': 5, 'name': 'second slip'},
    {'x': 8, 'y': -2, 'name': 'gully'},
    {'x': 15, 'y': -15, 'name': 'point'},
    {'x': 20, 'y': -30, 'name': 'cover'},
    {'x': 5, 'y': -35, 'name': 'mid-off'},
    {'x': -5, 'y': -35, 'name': 'mid-on'},
    {'x': -20, 'y': -25, 'name': 'midwicket'},
    {'x': -15, 'y': -10, 'name': 'square leg'},
    {'x': -45, 'y': -45, 'name': 'deep midwicket'},
]


class GameEngineHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == '/simulate':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)

            try:
                data = json.loads(post_data.decode('utf-8'))

                # Extract parameters
                speed = float(data.get('speed', 80))
                angle = float(data.get('angle', 0))
                elevation = float(data.get('elevation', 10))
                difficulty = data.get('difficulty', 'medium')
                field_config = data.get('field_config', DEFAULT_FIELD)

                # Calculate trajectory
                traj = calculate_trajectory(speed, angle, elevation)

                # Run simulation
                result = simulate_delivery(
                    exit_speed=speed,
                    horizontal_angle=angle,
                    vertical_angle=elevation,
                    landing_x=traj['landing_x'],
                    landing_y=traj['landing_y'],
                    projected_distance=traj['projected_distance'],
                    max_height=traj['max_height'],
                    field_config=field_config,
                    boundary_distance=70.0,
                    difficulty=difficulty,
                )

                # Add trajectory info to result
                result['trajectory'] = traj

                # Send response
                self.send_response(200)
                self._send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))

            except Exception as e:
                self.send_response(500)
                self._send_cors_headers()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[API] {args[0]}")


def run_server(port=5001):
    server = HTTPServer(('localhost', port), GameEngineHandler)
    print(f"Game Engine API running on http://localhost:{port}")
    print("Endpoints:")
    print("  POST /simulate - Simulate a shot")
    print("\nPress Ctrl+C to stop")
    server.serve_forever()


if __name__ == '__main__':
    run_server()
