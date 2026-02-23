"""
Cricket App REST API

FastAPI server exposing the database layer as HTTP endpoints.
Run with: uvicorn api:app --host 0.0.0.0 --port 8000

Requires: pip install fastapi uvicorn
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from database import (
    init_db,
    create_player,
    get_all_players,
    get_player_by_id,
    delete_player,
    create_session,
    get_all_sessions,
    get_sessions_by_player,
    get_session_by_id,
    delete_session,
    insert_delivery,
    get_deliveries_for_session,
    get_session_summary_stats,
    get_scoring_breakdown_by_zone,
    get_deliveries_by_over,
    get_player_session_summaries,
    get_speed_statistics,
)

# Initialise database on startup
init_db()

app = FastAPI(
    title="Cricket Net Simulator API",
    description="API for the radar-based cricket shot outcome simulator",
    version="1.0.0",
)

# Allow requests from the frontend (iPad/browser)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Request/Response Models
# =============================================================================

class PlayerCreate(BaseModel):
    name: str
    batting_hand: str = Field(pattern="^(left|right)$")


class PlayerResponse(BaseModel):
    id: int
    name: str
    batting_hand: str


class SessionCreate(BaseModel):
    player_id: int
    date: str  # ISO format: YYYY-MM-DD
    field_config_json: Optional[dict] = None
    notes: Optional[str] = None


class SessionResponse(BaseModel):
    id: int
    player_id: int
    date: str
    field_config_json: Optional[str]
    notes: Optional[str]


class DeliveryCreate(BaseModel):
    session_id: int
    timestamp: str  # ISO format datetime
    ball_number: int
    outcome: str = Field(pattern="^(dot|1|2|3|4|6|caught|dropped|misfield)$")
    runs: int = Field(ge=0, le=6)
    bowling_speed: Optional[float] = None
    exit_speed: float
    horizontal_angle: float = Field(ge=-180, le=180)
    vertical_angle: float = Field(ge=-90, le=90)
    landing_x: float
    landing_y: float
    projected_distance: float
    max_height: float
    fielder_position: Optional[str] = None
    is_boundary: bool = False
    is_aerial: bool = False
    radar_frames_captured: Optional[int] = None
    detection_confidence: Optional[float] = Field(default=None, ge=0, le=1)


class SessionSummary(BaseModel):
    total_runs: int
    balls_faced: int
    strike_rate: float
    fours: int
    sixes: int
    dismissals: int


class ZoneBreakdown(BaseModel):
    zone: str
    balls: int
    total_runs: int
    avg_exit_speed: Optional[float]


class OverData(BaseModel):
    over_number: int
    runs: int
    balls: int
    dots: int
    boundaries: int


class SpeedStats(BaseModel):
    avg_exit_speed: Optional[float]
    max_exit_speed: Optional[float]
    avg_bowling_speed: Optional[float]
    max_bowling_speed: Optional[float]


# =============================================================================
# Player Endpoints
# =============================================================================

@app.post("/api/players", response_model=dict, status_code=201)
def api_create_player(player: PlayerCreate):
    """Create a new player."""
    player_id = create_player(player.name, player.batting_hand)
    return {"id": player_id}


@app.get("/api/players", response_model=list[PlayerResponse])
def api_get_all_players():
    """Get all players."""
    return get_all_players()


@app.get("/api/players/{player_id}", response_model=PlayerResponse)
def api_get_player(player_id: int):
    """Get a player by ID."""
    player = get_player_by_id(player_id)
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


@app.delete("/api/players/{player_id}")
def api_delete_player(player_id: int):
    """Delete a player and all their data."""
    if not delete_player(player_id):
        raise HTTPException(status_code=404, detail="Player not found")
    return {"deleted": True}


# =============================================================================
# Session Endpoints
# =============================================================================

@app.post("/api/sessions", response_model=dict, status_code=201)
def api_create_session(session: SessionCreate):
    """Create a new session."""
    session_id = create_session(
        player_id=session.player_id,
        date=session.date,
        field_config=session.field_config_json,
        notes=session.notes,
    )
    return {"id": session_id}


@app.get("/api/sessions")
def api_get_all_sessions():
    """Get all sessions with player names."""
    return get_all_sessions()


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: int):
    """Get a session by ID."""
    session = get_session_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/api/players/{player_id}/sessions")
def api_get_player_sessions(player_id: int):
    """Get all sessions for a player."""
    return get_sessions_by_player(player_id)


@app.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: int):
    """Delete a session and all its deliveries."""
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}


# =============================================================================
# Delivery Endpoints
# =============================================================================

@app.post("/api/deliveries", response_model=dict, status_code=201)
def api_insert_delivery(delivery: DeliveryCreate):
    """Record a new delivery."""
    delivery_id = insert_delivery(
        session_id=delivery.session_id,
        timestamp=delivery.timestamp,
        ball_number=delivery.ball_number,
        outcome=delivery.outcome,
        runs=delivery.runs,
        bowling_speed=delivery.bowling_speed,
        exit_speed=delivery.exit_speed,
        horizontal_angle=delivery.horizontal_angle,
        vertical_angle=delivery.vertical_angle,
        landing_x=delivery.landing_x,
        landing_y=delivery.landing_y,
        projected_distance=delivery.projected_distance,
        max_height=delivery.max_height,
        fielder_position=delivery.fielder_position,
        is_boundary=delivery.is_boundary,
        is_aerial=delivery.is_aerial,
        radar_frames_captured=delivery.radar_frames_captured,
        detection_confidence=delivery.detection_confidence,
    )
    return {"id": delivery_id}


@app.get("/api/sessions/{session_id}/deliveries")
def api_get_session_deliveries(session_id: int):
    """Get all deliveries for a session (wagon wheel data)."""
    return get_deliveries_for_session(session_id)


# =============================================================================
# Analytics Endpoints
# =============================================================================

@app.get("/api/sessions/{session_id}/summary", response_model=SessionSummary)
def api_get_session_summary(session_id: int):
    """Get summary statistics for a session."""
    return get_session_summary_stats(session_id)


@app.get("/api/sessions/{session_id}/zones")
def api_get_session_zones(session_id: int):
    """Get scoring breakdown by zone for a session."""
    return get_scoring_breakdown_by_zone(session_id)


@app.get("/api/sessions/{session_id}/overs")
def api_get_session_overs(session_id: int):
    """Get runs per over for a session (manhattan chart data)."""
    return get_deliveries_by_over(session_id)


@app.get("/api/sessions/{session_id}/speeds", response_model=SpeedStats)
def api_get_session_speeds(session_id: int):
    """Get speed statistics for a session."""
    return get_speed_statistics(session_id)


@app.get("/api/players/{player_id}/progress")
def api_get_player_progress(player_id: int):
    """Get all session summaries for a player (progress tracking)."""
    return get_player_session_summaries(player_id)


# =============================================================================
# Health Check
# =============================================================================

@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
