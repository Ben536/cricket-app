"""
Ball detection from radar point clouds.

The missing link between raw radar frames and the game engine: turns
{x, y, z, doppler, snr} point clouds at 10-20Hz into discrete ball events
with speed and direction.

Pipeline (per frame):
  1. GATE    - drop points that cannot be a ball in flight: |doppler| below
               threshold kills static clutter and batsman sway; SNR floor
               kills noise.
  2. CLUSTER - single-linkage clustering in (x, y, z, doppler): points with
               incompatible radial velocity never merge, so a bat-tip return
               next to the ball is not averaged into its centroid. A ball is
               1-3 points; a moving bat/body is a large cluster - cluster
               size separates them.
  3. TRACK   - constant-velocity prediction with a gate on the RESIDUAL from
               the predicted position, assigned globally (closest pairs
               first). The hard constraint is dwell: the ball crosses a ~10m
               field of view in ~0.3s = only 3-6 frames, so tracks confirm on
               min_track_hits hits and are allowed brief coasting.

Speed: doppler measures only the line-of-sight component. Each point is
corrected by ITS OWN cos(theta) between the track direction and its
line-of-sight, then averaged. (Averaging doppler first and dividing by one
mid-track cos was ~15% low under the overhead mount - Jensen's inequality,
cos is strongly convex over the track.) If the radar profile's unambiguous
velocity is known, doppler is de-aliased against the track's own
displacement velocity first. The displacement speed is reported alongside.

Direction: from the track's displacement in the sensor's GROUND plane
(radar/geometry.py names the axes). The field-frame direction and the
engine's angle are produced only when radar/mount.json is calibrated.

MOUNTING GEOMETRY (product constraint): the sensor is ALWAYS mounted
OVERHEAD, above the batter, looking down (hence the IWR6843 ODS variant).
Consequences for tuning:
- the doppler null occurs AT CONTACT (horizontal motion is perpendicular
  to the boresight when the ball is directly below) - tracks start weak
  and strengthen within ~0.1s; the bridging logic matters at track START
- the dominant clutter is the BAT SWING directly beneath the sensor
  (25+ m/s tip speed, well above any doppler gate) - the doppler-aware
  clustering and cluster-size rejection are the defence
- the ~9m range sees only the ball's first ~6-8m: exactly the launch
  segment (exit speed + direction); the engine simulates the rest

Designed to run identically offline (replay over JSONL recordings) and
live (as a RadarSource subscriber) - tune offline first, then attach.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, fields, replace
from typing import Optional

from radar.geometry import MountCalibration, launch_vertical_angle_deg, sensor_direction_deg
from radar.profile_cfg import RadarProfile
from radar.tlv import RadarFrame

logger = logging.getLogger(__name__)


# =============================================================================
# Tunable parameters (defaults from physics; tune against recordings)
# =============================================================================

@dataclass
class DetectorParams:
    min_doppler: float = 4.0        # m/s - below this it's clutter/body (14 km/h)
    min_snr: float = 6.0            # dB - noise floor
    cluster_eps: float = 0.6        # m - points closer than this (in 4-D) merge
    # Metres of separation per m/s of doppler difference in the clustering
    # metric: at 0.05, two returns 10 m/s apart are 0.5m apart even if they
    # coincide in space. A bat tip (8 m/s) and a ball (25 m/s) never merge.
    cluster_doppler_scale: float = 0.05
    max_ball_cluster_points: int = 4  # bigger clusters are bat/body, not ball
    # Association gate = association_base + association_slack * predicted hop.
    # The gate is on the residual from a constant-velocity PREDICTION, sized
    # from the track's own displacement velocity - not from radial doppler,
    # which under an overhead mount is a small fraction of true speed right
    # after contact, exactly when the gate matters.
    association_base: float = 0.5   # m - residual gate floor for slow/noisy motion
    association_slack: float = 0.5  # fraction of the predicted hop added to the gate
    # A 1-hit track has no velocity yet: gate its first hop on the fastest
    # ball we care about.
    first_hop_speed_kmh: float = 200.0
    min_track_hits: int = 3         # hits to confirm a track as a ball
    max_coast_frames: int = 2       # missed frames before a track dies
    max_track_gap_ms: int = 400     # absolute time gap that kills a track
    max_bridge_streak: int = 2      # consecutive doppler-null points a track may absorb
    # Motion-consistency: a real ball's POSITION travels at roughly the speed
    # its doppler claims. Real IWR6843 data (first live recording, 2026-07-03)
    # is full of static multipath/aliasing ghosts reading ~26 m/s doppler
    # while sitting perfectly still - 33 false "balls" from an empty room.
    # Observed speed (displacement/time) must be at least this fraction of
    # the claimed radial speed.
    min_motion_ratio: float = 0.3
    # Path straightness: a ball covers its ~0.3s dwell in a near-straight
    # line; surviving ghosts TELEPORT between multipath mirror positions
    # (8m zigzags). straightness = net displacement / total path length.
    min_straightness: float = 0.7
    # Physical plausibility cap on the CORRECTED speed: the engine's own
    # input limit (ENGINE_LIMITS in src/gameEngine.ts, MAX_EXIT_SPEED in
    # engine/game_engine.py). The fastest bat exit speeds ever measured are
    # ~175 km/h; ghost tracks in the real recording claim 225-265.
    max_plausible_speed_kmh: float = 200.0
    # Segment consistency: a real ball MOVES in every inter-frame segment;
    # ghosts alternate between parked (0 m/s) and teleporting (45 m/s).
    # At least this fraction of segments must show ball-consistent motion.
    min_moving_fraction: float = 0.6
    # Unambiguous radial velocity of the radar profile (m/s). Set from
    # config/profile_cricket.cfg via from_profile(); None disables
    # de-aliasing (doppler taken at face value).
    v_max_ms: Optional[float] = None
    # Points whose line of sight is nearly perpendicular to the track carry
    # no speed information (doppler ~ 0 / cos ~ 0); they are excluded from
    # the doppler speed estimate rather than divided by a tiny cos.
    min_cos_theta: float = 0.25
    # A real ball leaving the nadir has well-conditioned points within a
    # frame or two (cos grows toward 1 as it moves away). A track with fewer
    # than this many is not a ball we can measure - and the static ghosts in
    # the real recording are exactly that: large doppler, near-perpendicular
    # to their (teleporting) displacement, so no point is well-conditioned.
    min_doppler_samples: int = 2
    # Doppler must AGREE with the displacement velocity projected on each
    # line of sight (after de-aliasing). Ghosts claim 26 m/s radial while
    # their displacement projects to ~1 m/s. A point is consistent when the
    # two are within this ratio or within 3 m/s; this fraction of the
    # well-conditioned points must be consistent. (A real ball's two
    # estimates agree to ~10%.)
    min_doppler_consistency: float = 0.5
    doppler_consistency_ratio: float = 1.5
    doppler_consistency_abs_ms: float = 3.0
    # The two independent speed estimates - corrected doppler and
    # displacement/time - agree to ~10% on a real ball. A surviving ghost in
    # the real recording had them 32% apart (198 vs 150 km/h).
    max_speed_disagreement: float = 0.25
    # Radar frame period (ms). When set, tracking uses the hardware frame
    # counter as its clock instead of the host receive time: the recorder
    # stamps frames when they arrive, and two frames from one serial read
    # batch can carry timestamps 4ms apart (seen in the real recording),
    # which turns a 0.3m hop into a 75 m/s teleport. Event times are still
    # reported on the host clock so annotations align.
    frame_period_ms: Optional[float] = None

    @classmethod
    def from_profile(cls, profile: RadarProfile, **overrides) -> "DetectorParams":
        """Defaults with the radar's own limits filled in from its config."""
        params = cls(v_max_ms=profile.v_max_ms, frame_period_ms=profile.frame_period_ms)
        return replace(params, **overrides)

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


# =============================================================================
# Data types
# =============================================================================

@dataclass
class Detection:
    """One gated+clustered candidate point (cluster centroid)."""
    t_ms: int          # host clock (aligns with annotations)
    x: float
    y: float
    z: float
    doppler: float
    snr: float
    n_points: int
    t_track_ms: int = 0  # tracking clock: hardware frame time when known, else t_ms


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

    def velocity(self) -> Optional[tuple[float, float, float]]:
        """Constant-velocity estimate from the last two detections (m/s)."""
        if len(self.detections) < 2:
            return None
        a, b = self.detections[-2], self.detections[-1]
        dt = (b.t_track_ms - a.t_track_ms) / 1000.0
        if dt <= 0:
            return None
        return (b.x - a.x) / dt, (b.y - a.y) / dt, (b.z - a.z) / dt


@dataclass
class BallEvent:
    """A confirmed ball passage."""
    t_start_ms: int
    t_end_ms: int
    n_hits: int
    speed_kmh: float             # best estimate (doppler-corrected when well-conditioned)
    speed_doppler_kmh: Optional[float]  # per-point cos-corrected, de-aliased doppler
    speed_track_kmh: float       # displacement / time
    radial_speed_kmh: float      # raw mean |doppler| (lower bound on speed)
    direction_sensor_deg: float  # horizontal direction in the SENSOR ground plane
    vertical_angle_deg: float    # elevation of the launch segment
    horizontal_angle_deg: Optional[float]  # engine convention (+off) - only when calibrated
    field_direction_deg: Optional[float]   # wagon-wheel convention (+leg) - only when calibrated
    aliased: bool                # doppler had to be unwrapped
    positions: list[tuple[int, float, float, float]]  # (t_ms, x, y, z)
    mean_snr: float

    def to_dict(self) -> dict:
        return {
            "t_start_ms": self.t_start_ms,
            "t_end_ms": self.t_end_ms,
            "n_hits": self.n_hits,
            "speed_kmh": round(self.speed_kmh, 1),
            "speed_doppler_kmh": None if self.speed_doppler_kmh is None else round(self.speed_doppler_kmh, 1),
            "speed_track_kmh": round(self.speed_track_kmh, 1),
            "radial_speed_kmh": round(self.radial_speed_kmh, 1),
            "direction_sensor_deg": round(self.direction_sensor_deg, 1),
            "vertical_angle_deg": round(self.vertical_angle_deg, 1),
            "horizontal_angle_deg": None if self.horizontal_angle_deg is None else round(self.horizontal_angle_deg, 1),
            "field_direction_deg": None if self.field_direction_deg is None else round(self.field_direction_deg, 1),
            "aliased": self.aliased,
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

    def __init__(
        self,
        params: Optional[DetectorParams] = None,
        calibration: Optional[MountCalibration] = None,
    ):
        self.params = params or DetectorParams()
        self.calibration = calibration
        self._tracks: list[Track] = []
        self._last_t_ms: Optional[int] = None

    # -- stage 1: gating ----------------------------------------------------

    def _tracking_time(self, frame: RadarFrame, t_ms: int) -> int:
        """Hardware frame time when the period is known and the counter is
        real; otherwise the host clock."""
        p = self.params
        if p.frame_period_ms and frame.frame_number > 0:
            return int(round(frame.frame_number * p.frame_period_ms))
        return t_ms

    def _gate(self, frame: RadarFrame, t_ms: int, t_track: int) -> tuple[list[Detection], list[Detection]]:
        """Split points into primary (ball-like doppler) and secondary
        (doppler below threshold but decent SNR).

        Doppler NULLS at closest approach - under the overhead mount that is
        the moment of contact, directly below the sensor. Points in that
        null must not seed tracks (they're indistinguishable from clutter)
        but MUST be able to extend an established track, otherwise every
        delivery splits into two half-tracks.
        """
        p = self.params
        primary, secondary = [], []
        for pt in frame.points:
            if pt.snr < p.min_snr:
                continue
            det = Detection(t_ms=t_ms, x=pt.x, y=pt.y, z=pt.z,
                            doppler=pt.doppler, snr=pt.snr, n_points=1,
                            t_track_ms=t_track)
            if abs(pt.doppler) >= p.min_doppler:
                primary.append(det)
            else:
                secondary.append(det)
        return primary, secondary

    # -- stage 2: clustering ------------------------------------------------

    def _cluster_distance(self, a: Detection, b: Detection) -> float:
        s = self.params.cluster_doppler_scale
        return math.sqrt(
            (a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2
            + ((a.doppler - b.doppler) * s) ** 2
        )

    def _cluster(self, detections: list[Detection]) -> list[Detection]:
        """Greedy single-linkage clustering in (x, y, z, doppler); returns
        centroids. Clusters larger than max_ball_cluster_points are discarded
        entirely - a fast-moving bat or body throws many gated points, a ball
        throws 1-3."""
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
                    if any(self._cluster_distance(other, m) <= p.cluster_eps for m in members):
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
                t_track_ms=seed.t_track_ms,
            ))
        return centroids

    # -- stage 3: tracking --------------------------------------------------

    def _predict(self, track: Track, t_track: int) -> tuple[tuple[float, float, float], float]:
        """Predicted position at tracking time t_track and the residual gate radius."""
        p = self.params
        last = track.last
        dt = (t_track - last.t_track_ms) / 1000.0
        v = track.velocity()
        if v is None:
            # First hop: no velocity yet. Gate on the fastest plausible ball.
            return (last.x, last.y, last.z), p.first_hop_speed_kmh / 3.6 * dt + p.association_base
        hop = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) * dt
        predicted = (last.x + v[0] * dt, last.y + v[1] * dt, last.z + v[2] * dt)
        return predicted, p.association_base + p.association_slack * hop

    def _associate(
        self,
        t_track: int,
        candidates: list[Detection],
        bridge_candidates: list[Detection],
    ) -> tuple[list[tuple[Track, Detection, bool]], list[Detection], list[Detection]]:
        """Global nearest-residual assignment: every (track, candidate) pair
        inside its gate is scored, then pairs are taken closest-first with
        each track and candidate used at most once. The previous per-track
        greedy loop let whichever track happened to come first steal a
        point that fitted another track far better."""
        p = self.params
        pairs: list[tuple[float, int, int, bool]] = []
        for ti, track in enumerate(self._tracks):
            if t_track - track.last.t_track_ms <= 0:
                continue
            predicted, radius = self._predict(track, t_track)
            for ci, c in enumerate(candidates):
                d = math.dist((c.x, c.y, c.z), predicted)
                if d <= radius:
                    pairs.append((d, ti, ci, False))
            # The null at closest approach lasts 1-2 frames; a longer streak
            # of low-doppler matches means the track is absorbing clutter.
            if track.hits >= 2 and track.bridge_streak < p.max_bridge_streak:
                for ci, c in enumerate(bridge_candidates):
                    d = math.dist((c.x, c.y, c.z), predicted)
                    if d <= radius:
                        pairs.append((d, ti, ci, True))

        pairs.sort(key=lambda q: q[0])
        used_tracks: set[int] = set()
        used_primary: set[int] = set()
        used_bridge: set[int] = set()
        assignments: list[tuple[Track, Detection, bool]] = []
        for d, ti, ci, is_bridge in pairs:
            if ti in used_tracks:
                continue
            used = used_bridge if is_bridge else used_primary
            if ci in used:
                continue
            # A primary candidate always beats a bridge candidate for a track
            # if both are in range; pairs are sorted by residual so this only
            # matters on exact ties - keep the deterministic primary-first
            # preference by skipping a bridge pair when a primary pair for the
            # same track still exists closer or equal.
            used_tracks.add(ti)
            used.add(ci)
            assignments.append((self._tracks[ti], (bridge_candidates if is_bridge else candidates)[ci], is_bridge))

        unmatched = [c for i, c in enumerate(candidates) if i not in used_primary]
        unmatched_bridge = [c for i, c in enumerate(bridge_candidates) if i not in used_bridge]
        return assignments, unmatched, unmatched_bridge

    def process_frame(self, frame: RadarFrame, t_ms: int) -> list[BallEvent]:
        """Process one frame; returns any tracks that COMPLETED this frame.

        `t_ms` is the host clock (recording time, what annotations use);
        tracking arithmetic uses the hardware frame clock when the profile's
        frame period is known (see DetectorParams.frame_period_ms).
        """
        t_track = self._tracking_time(frame, t_ms)
        primary_raw, secondary_raw = self._gate(frame, t_ms, t_track)
        candidates = self._cluster(primary_raw)
        bridge_candidates = self._cluster(secondary_raw)
        completed: list[BallEvent] = []

        assignments, unmatched, _ = self._associate(t_track, candidates, bridge_candidates)
        assigned_tracks = set()
        for track, det, is_bridge in assignments:
            track.detections.append(det)
            track.coast_count = 0
            track.bridge_streak = track.bridge_streak + 1 if is_bridge else 0
            assigned_tracks.add(id(track))
        for track in self._tracks:
            if id(track) not in assigned_tracks and t_track - track.last.t_track_ms > 0:
                track.coast_count += 1

        # Retire tracks that coasted too long or timed out
        p = self.params
        survivors = []
        for track in self._tracks:
            expired = (
                track.coast_count > p.max_coast_frames
                or t_track - track.last.t_track_ms > p.max_track_gap_ms
            )
            if expired:
                event = self._finalize(track)
                if event:
                    completed.append(event)
            else:
                survivors.append(track)
        self._tracks = survivors

        # Unmatched primary candidates start new tentative tracks
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
        # Geometry from points OUTSIDE the doppler null - bridge points
        # (|doppler| ~ 0 at closest approach) would drag the radial average
        # down, and any trailing clutter would skew the displacement.
        strong = [x for x in d if abs(x.doppler) >= p.min_doppler]
        if len(strong) < p.min_track_hits:
            return None
        radial_ms = sum(abs(x.doppler) for x in strong) / len(strong)

        first, last = strong[0], strong[-1]
        disp = (last.x - first.x, last.y - first.y, last.z - first.z)
        disp_len = math.sqrt(sum(v * v for v in disp))
        duration_s = (last.t_track_ms - first.t_track_ms) / 1000.0
        if duration_s <= 0 or disp_len < 0.1:
            return None
        speed_track_ms = disp_len / duration_s

        # Motion-consistency check: stationary ghosts with aliased doppler
        # claim ball-like radial speed but travel nowhere. Reject any track
        # whose observed speed is a small fraction of its claimed speed.
        if speed_track_ms < radial_ms * p.min_motion_ratio:
            return None

        # Straightness check: multipath ghosts zigzag between mirror
        # positions; a ball's strong points lie on a near-straight line.
        path_len = sum(
            math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
            for a, b in zip(strong, strong[1:])
        )
        if path_len > 0.5 and disp_len / path_len < p.min_straightness:
            return None

        # Segment-consistency check: every inter-frame hop of a real ball
        # moves at ~ball speed; parked-then-teleporting ghosts don't.
        moving = 0
        total = 0
        for a, b in zip(strong, strong[1:]):
            dt = (b.t_track_ms - a.t_track_ms) / 1000.0
            if dt <= 0:
                continue
            total += 1
            seg_speed = math.dist((a.x, a.y, a.z), (b.x, b.y, b.z)) / dt
            if seg_speed >= radial_ms * p.min_motion_ratio:
                moving += 1
        if total > 0 and moving / total < p.min_moving_fraction:
            return None

        # Per-point doppler correction with de-aliasing. Each point's own
        # line of sight gives its own cos(theta); the track displacement
        # velocity gives the expected radial component, which resolves the
        # ambiguity interval when v_max is known.
        direction = (disp[0] / disp_len, disp[1] / disp_len, disp[2] / disp_len)
        samples: list[float] = []
        consistent = 0
        aliased = False
        for pt in strong:
            r = math.sqrt(pt.x * pt.x + pt.y * pt.y + pt.z * pt.z)
            if r < 0.1:
                continue
            cos_theta = (direction[0] * pt.x + direction[1] * pt.y + direction[2] * pt.z) / r
            if abs(cos_theta) < p.min_cos_theta:
                continue
            doppler = pt.doppler
            expected = speed_track_ms * cos_theta
            if p.v_max_ms:
                k = round((expected - doppler) / (2.0 * p.v_max_ms))
                if k:
                    aliased = True
                    doppler += k * 2.0 * p.v_max_ms
            samples.append(abs(doppler) / abs(cos_theta))
            lo, hi = sorted((abs(doppler), abs(expected)))
            if hi - lo <= p.doppler_consistency_abs_ms or (lo > 0 and hi / lo <= p.doppler_consistency_ratio):
                consistent += 1

        # A measurable ball has well-conditioned points whose doppler agrees
        # with how it actually moved. Static ghosts fail both: their doppler
        # is large and their displacement is nearly perpendicular to every
        # line of sight (the old code divided by a floored cos and rejected
        # them as ">250 km/h"; this is the same physics stated directly).
        if len(samples) < p.min_doppler_samples:
            return None
        if consistent / len(samples) < p.min_doppler_consistency:
            return None

        speed_doppler_ms = sum(samples) / len(samples)
        speed_ms = speed_doppler_ms
        if speed_ms * 3.6 > p.max_plausible_speed_kmh:
            return None  # faster than any cricket ball: aliasing ghost
        if abs(speed_doppler_ms - speed_track_ms) / speed_track_ms > p.max_speed_disagreement:
            return None  # the two instruments disagree: not a ball

        # Directions: horizontal in the sensor's ground plane, launch
        # elevation from the vertical component (gravity-compensated).
        # Field-frame angles only when calibrated.
        du, dv = disp[0], disp[2]
        direction_sensor = sensor_direction_deg(du, dv)
        vertical = launch_vertical_angle_deg(disp[0], disp[1], disp[2], duration_s)
        horizontal_angle = field_direction = None
        if self.calibration is not None and self.calibration.calibrated:
            horizontal_angle = self.calibration.engine_angle_deg(du, dv)
            field_direction = self.calibration.field_direction_deg(du, dv)

        return BallEvent(
            t_start_ms=first.t_ms,
            t_end_ms=last.t_ms,
            n_hits=track.hits,
            speed_kmh=speed_ms * 3.6,
            speed_doppler_kmh=speed_doppler_ms * 3.6,
            speed_track_kmh=speed_track_ms * 3.6,
            radial_speed_kmh=radial_ms * 3.6,
            direction_sensor_deg=direction_sensor,
            vertical_angle_deg=vertical,
            horizontal_angle_deg=horizontal_angle,
            field_direction_deg=field_direction,
            aliased=aliased,
            positions=[(x.t_ms, x.x, x.y, x.z) for x in d],
            mean_snr=sum(x.snr for x in d) / len(d),
        )
