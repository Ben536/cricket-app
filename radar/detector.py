"""
Ball detection from radar point clouds.

The missing link between raw radar frames and the game engine: turns
{x, y, z, doppler, snr} point clouds at 10-20Hz into discrete ball events
with speed and direction.

Pipeline (per frame):
  1. GATE   - drop points that cannot be a ball in flight: |doppler| below
              threshold kills static clutter and batsman sway; SNR floor
              kills noise. (Thresholds are tuned offline against the
              foil-ball / racket data-gathering recordings.)
  2. CLUSTER - greedy distance clustering of survivors. A ball is 1-3
              points; a moving bat/body is a large cluster - cluster size
              separates them.
  3. TRACK  - nearest-neighbour association across frames with a
              VELOCITY-SCALED gate: a 120 km/h ball moves 1.7-3.3m between
              frames, so a fixed gate can't work. The hard constraint is
              dwell: the ball crosses a ~10m field of view in ~0.3s = only
              3-6 frames, so tracks confirm on MIN_TRACK_HITS hits and are
              allowed brief coasting.

Speed comes from doppler corrected for radial geometry (doppler measures
the line-of-sight component; the track's displacement direction gives the
cos correction). Direction comes from the track's horizontal displacement.

NOTE on frames of reference: positions/directions are in the RADAR's
coordinate frame. Mapping to the field frame (batter at origin, +Y bowler)
needs the mount calibration - derived offline by comparing detected
directions against the wagon-wheel ground truth in the recordings
(tools/replay_jsonl.py reports both so the offset can be fitted).

MOUNTING GEOMETRY (product constraint): the sensor is ALWAYS mounted
OVERHEAD, above the batter, looking down (hence the IWR6843 ODS variant).
Consequences for tuning:
- point-cloud x/y maps ~directly onto the field plane, so direction is a
  fixed rotation from the mount, not a per-session fit
- the doppler null occurs AT CONTACT (horizontal motion is perpendicular
  to the boresight when the ball is directly below) - tracks start weak
  and strengthen within ~0.1s; the bridging logic matters at track START
- the dominant clutter is the BAT SWING directly beneath the sensor
  (25+ m/s tip speed, well above any doppler gate) - cluster-size
  rejection plus the racket/racket_foil recordings are the defence
- the ~9m range sees only the ball's first ~6-8m: exactly the launch
  segment (exit speed + direction); the engine simulates the rest
- the incoming delivery approaches near head-on from the bowler's end -
  ideal doppler geometry for bowling speed

Designed to run identically offline (replay over JSONL recordings) and
live (as a RadarSource subscriber) - tune offline first, then attach.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from radar.tlv import RadarFrame

logger = logging.getLogger(__name__)


# =============================================================================
# Tunable parameters (defaults from physics; tune against recordings)
# =============================================================================

@dataclass
class DetectorParams:
    min_doppler: float = 4.0        # m/s - below this it's clutter/body (14 km/h)
    min_snr: float = 6.0            # dB - noise floor
    cluster_eps: float = 0.6        # m - points closer than this merge
    max_ball_cluster_points: int = 4  # bigger clusters are bat/body, not ball
    association_slack: float = 2.5  # gate = |doppler|*dt*slack + base
    association_base: float = 0.5   # m - gate floor for slow/noisy motion
    min_track_hits: int = 3         # hits to confirm a track as a ball
    max_coast_frames: int = 2       # missed frames before a track dies
    max_track_gap_ms: int = 400     # absolute time gap that kills a track
    max_bridge_streak: int = 2      # consecutive doppler-null points a track may absorb


# =============================================================================
# Data types
# =============================================================================

@dataclass
class Detection:
    """One gated+clustered candidate point (cluster centroid)."""
    t_ms: int
    x: float
    y: float
    z: float
    doppler: float
    snr: float
    n_points: int


@dataclass
class Track:
    """A tentative ball track being built across frames."""
    detections: list[Detection] = field(default_factory=list)
    coast_count: int = 0
    bridge_streak: int = 0  # consecutive low-doppler (null-bridging) points

    @property
    def last(self) -> Detection:
        return self.detections[-1]

    @property
    def hits(self) -> int:
        return len(self.detections)


@dataclass
class BallEvent:
    """A confirmed ball passage."""
    t_start_ms: int
    t_end_ms: int
    n_hits: int
    speed_kmh: float          # doppler-derived, geometry-corrected
    radial_speed_kmh: float   # raw |doppler| average (lower bound on speed)
    direction_deg: float      # horizontal direction in the RADAR frame
    positions: list[tuple[int, float, float, float]]  # (t_ms, x, y, z)
    mean_snr: float

    def to_dict(self) -> dict:
        return {
            "t_start_ms": self.t_start_ms,
            "t_end_ms": self.t_end_ms,
            "n_hits": self.n_hits,
            "speed_kmh": round(self.speed_kmh, 1),
            "radial_speed_kmh": round(self.radial_speed_kmh, 1),
            "direction_deg": round(self.direction_deg, 1),
            "mean_snr": round(self.mean_snr, 1),
            "positions": [
                {"t_ms": t, "x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
                for t, x, y, z in self.positions
            ],
        }


# =============================================================================
# Detector
# =============================================================================

class BallDetector:
    """Stateful frame-by-frame ball detector.

    Feed frames in time order via process_frame(); completed BallEvents are
    returned as tracks finish (ball left the field of view). Call flush() at
    the end of a recording to close any live track.
    """

    def __init__(self, params: Optional[DetectorParams] = None):
        self.params = params or DetectorParams()
        self._tracks: list[Track] = []
        self._last_t_ms: Optional[int] = None

    # -- stage 1: gating ----------------------------------------------------

    def _gate(self, frame: RadarFrame, t_ms: int) -> tuple[list[Detection], list[Detection]]:
        """Split points into primary (ball-like doppler) and secondary
        (doppler below threshold but decent SNR).

        Doppler NULLS at closest approach - a ball passing near-perpendicular
        to the radar reads ~0 m/s for a frame or two even at 100+ km/h. Points
        in that null must not seed tracks (they're indistinguishable from
        clutter) but MUST be able to extend an established track, otherwise
        every crossing delivery splits into two half-tracks.
        """
        p = self.params
        primary, secondary = [], []
        for pt in frame.points:
            if pt.snr < p.min_snr:
                continue
            det = Detection(t_ms=t_ms, x=pt.x, y=pt.y, z=pt.z,
                            doppler=pt.doppler, snr=pt.snr, n_points=1)
            if abs(pt.doppler) >= p.min_doppler:
                primary.append(det)
            else:
                secondary.append(det)
        return primary, secondary

    # -- stage 2: clustering ------------------------------------------------

    def _cluster(self, detections: list[Detection]) -> list[Detection]:
        """Greedy single-linkage clustering; returns centroids.

        Clusters larger than max_ball_cluster_points are discarded entirely -
        a fast-moving bat or body throws many gated points, a ball throws 1-3.
        """
        p = self.params
        unused = list(detections)
        centroids: list[Detection] = []

        while unused:
            seed = unused.pop()
            members = [seed]
            changed = True
            while changed:
                changed = False
                for other in list(unused):
                    if any(
                        math.dist((other.x, other.y, other.z), (m.x, m.y, m.z)) <= p.cluster_eps
                        for m in members
                    ):
                        members.append(other)
                        unused.remove(other)
                        changed = True

            if len(members) > p.max_ball_cluster_points:
                continue  # bat/body-sized cluster: not a ball

            n = len(members)
            centroids.append(Detection(
                t_ms=seed.t_ms,
                x=sum(m.x for m in members) / n,
                y=sum(m.y for m in members) / n,
                z=sum(m.z for m in members) / n,
                doppler=sum(m.doppler for m in members) / n,
                snr=max(m.snr for m in members),
                n_points=n,
            ))
        return centroids

    # -- stage 3: tracking --------------------------------------------------

    def _gate_radius(self, track: Track, dt_ms: int) -> float:
        p = self.params
        # Use the track's best recent doppler, not just the last point's - a
        # bridge point in the doppler null would otherwise collapse the gate
        # to the base radius and the track could never re-acquire the ball.
        recent = track.detections[-3:]
        speed_estimate = max(abs(d.doppler) for d in recent)
        expected = speed_estimate * (dt_ms / 1000.0)
        return expected * p.association_slack + p.association_base

    def process_frame(self, frame: RadarFrame, t_ms: int) -> list[BallEvent]:
        """Process one frame; returns any tracks that COMPLETED this frame."""
        primary_raw, secondary_raw = self._gate(frame, t_ms)
        candidates = self._cluster(primary_raw)
        bridge_candidates = self._cluster(secondary_raw)
        completed: list[BallEvent] = []

        # Associate candidates to existing tracks (nearest within gate).
        # Established tracks (>=2 hits) may also take a low-doppler bridge
        # candidate - this carries a track through the doppler null at
        # closest approach. Bridge candidates never seed new tracks.
        unmatched = list(candidates)
        unmatched_bridge = list(bridge_candidates)
        for track in self._tracks:
            dt_ms = t_ms - track.last.t_ms
            if dt_ms <= 0:
                continue
            radius = self._gate_radius(track, dt_ms)

            best = None
            best_dist = radius
            is_bridge = False
            for c in unmatched:
                d = math.dist((c.x, c.y, c.z), (track.last.x, track.last.y, track.last.z))
                if d <= best_dist:
                    best = c
                    best_dist = d
            # The null at closest approach lasts 1-2 frames; a longer streak
            # of low-doppler matches means the track is absorbing clutter.
            if best is None and track.hits >= 2 and track.bridge_streak < self.params.max_bridge_streak:
                for c in unmatched_bridge:
                    d = math.dist((c.x, c.y, c.z), (track.last.x, track.last.y, track.last.z))
                    if d <= best_dist:
                        best = c
                        best_dist = d
                        is_bridge = True

            if best is not None:
                track.detections.append(best)
                track.coast_count = 0
                track.bridge_streak = track.bridge_streak + 1 if is_bridge else 0
                (unmatched_bridge if is_bridge else unmatched).remove(best)
            else:
                track.coast_count += 1

        # Retire tracks that coasted too long or timed out
        p = self.params
        survivors = []
        for track in self._tracks:
            expired = (
                track.coast_count > p.max_coast_frames
                or t_ms - track.last.t_ms > p.max_track_gap_ms
            )
            if expired:
                event = self._finalize(track)
                if event:
                    completed.append(event)
            else:
                survivors.append(track)
        self._tracks = survivors

        # Unmatched candidates start new tentative tracks
        for c in unmatched:
            self._tracks.append(Track(detections=[c]))

        self._last_t_ms = t_ms
        return completed

    def flush(self) -> list[BallEvent]:
        """End of stream: close all live tracks."""
        events = [e for t in self._tracks if (e := self._finalize(t))]
        self._tracks = []
        return events

    # -- track -> event -----------------------------------------------------

    def _finalize(self, track: Track) -> Optional[BallEvent]:
        p = self.params
        if track.hits < p.min_track_hits:
            return None

        d = track.detections
        # Radial speed and geometry from points OUTSIDE the doppler null -
        # bridge points (|doppler| ~ 0 at closest approach) would drag the
        # average down, and any trailing clutter would skew the displacement.
        strong = [x for x in d if abs(x.doppler) >= p.min_doppler]
        if len(strong) < p.min_track_hits:
            return None
        radial_ms = sum(abs(x.doppler) for x in strong) / len(strong)

        # Geometry correction: doppler is the line-of-sight component. Use the
        # track displacement direction vs the radial direction at the midpoint
        # to recover the true speed. Guarded: near-perpendicular geometry
        # makes the correction explode, cap it.
        first, last = strong[0], strong[-1]
        disp = (last.x - first.x, last.y - first.y, last.z - first.z)
        disp_len = math.sqrt(sum(v * v for v in disp))
        mid = strong[len(strong) // 2]
        radial = (mid.x, mid.y, mid.z)
        radial_len = math.sqrt(sum(v * v for v in radial))

        cos_theta = 1.0
        if disp_len > 0.1 and radial_len > 0.1:
            dot = sum(a * b for a, b in zip(disp, radial))
            cos_theta = abs(dot) / (disp_len * radial_len)
        cos_theta = max(cos_theta, 0.35)  # cap correction at ~2.9x

        speed_ms = radial_ms / cos_theta

        # Horizontal direction of travel in the radar frame
        direction = math.degrees(math.atan2(disp[0], disp[1])) if disp_len > 0.1 else 0.0

        return BallEvent(
            t_start_ms=first.t_ms,
            t_end_ms=last.t_ms,
            n_hits=track.hits,
            speed_kmh=speed_ms * 3.6,
            radial_speed_kmh=radial_ms * 3.6,
            direction_deg=direction,
            positions=[(x.t_ms, x.x, x.y, x.z) for x in d],
            mean_snr=sum(x.snr for x in d) / len(d),
        )
