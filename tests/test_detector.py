"""Ball detector: synthetic ball crossings with clutter and bat decoys.

Real-signature tuning happens against nets recordings via
tools/replay_jsonl.py; these tests pin the pipeline mechanics.
"""

import math
import random

from radar.detector import BallDetector, BallEvent, DetectorParams
from radar.tlv import RadarFrame, RadarPoint

FRAME_DT_MS = 50  # 20Hz


def make_frame(n, points):
    return RadarFrame(frame_number=n, cpu_time_ms=n * FRAME_DT_MS,
                      num_points=len(points), points=points)


def clutter_points(rng):
    """Static-ish clutter: near-zero doppler, low SNR - must be gated out."""
    return [
        RadarPoint(x=rng.uniform(-3, 3), y=rng.uniform(1, 8), z=rng.uniform(0, 2),
                   doppler=rng.uniform(-1.5, 1.5), snr=rng.uniform(3, 9), noise=5.0)
        for _ in range(rng.randint(0, 4))
    ]


def ball_point(t_s, speed_ms, direction_rad):
    """A ball crossing the field of view at constant velocity."""
    x = -4 + speed_ms * t_s * math.sin(direction_rad)
    y = 2 + speed_ms * t_s * math.cos(direction_rad)
    # Radial (doppler) component: velocity projected on the line of sight
    r = math.sqrt(x * x + y * y) or 1.0
    vx = speed_ms * math.sin(direction_rad)
    vy = speed_ms * math.cos(direction_rad)
    doppler = (vx * x + vy * y) / r
    return x, y, doppler


def run_ball_crossing(speed_kmh=100.0, direction_deg=60.0, n_frames=30, with_bat=False):
    rng = random.Random(7)
    speed_ms = speed_kmh / 3.6
    direction = math.radians(direction_deg)
    detector = BallDetector(DetectorParams())
    events: list[BallEvent] = []

    for n in range(n_frames):
        t_ms = n * FRAME_DT_MS
        points = clutter_points(rng)

        # Ball visible for frames 8..14 (~0.35s dwell, realistic)
        if 8 <= n <= 14:
            t_s = (n - 8) * FRAME_DT_MS / 1000.0
            x, y, doppler = ball_point(t_s, speed_ms, direction)
            points.append(RadarPoint(x=x, y=y, z=0.8, doppler=doppler, snr=17.0, noise=5.0))

        # A bat swing: a large fast cluster - must be rejected by cluster size
        if with_bat and 8 <= n <= 11:
            for i in range(7):
                points.append(RadarPoint(x=0.2 + i * 0.15, y=1.5 + i * 0.1,
                                         z=1.0 + i * 0.05, doppler=8.0, snr=15.0, noise=5.0))

        events.extend(detector.process_frame(make_frame(n, points), t_ms))

    events.extend(detector.flush())
    return events


def test_detects_single_ball_crossing():
    events = run_ball_crossing()
    assert len(events) == 1, f"expected exactly one ball event, got {len(events)}"
    ev = events[0]
    assert ev.n_hits >= 3
    assert 8 * FRAME_DT_MS <= ev.t_start_ms <= 10 * FRAME_DT_MS


def test_speed_recovered_within_tolerance():
    ev = run_ball_crossing(speed_kmh=100.0)[0]
    # Geometry-corrected speed should land near truth; radial is a lower bound
    assert ev.radial_speed_kmh <= 105.0
    assert 70.0 <= ev.speed_kmh <= 130.0, ev.speed_kmh


def test_bat_swing_cluster_rejected():
    events = run_ball_crossing(with_bat=True)
    assert len(events) == 1, "bat cluster must not create a second ball event"


def test_clutter_only_produces_no_events():
    rng = random.Random(3)
    detector = BallDetector()
    events = []
    for n in range(40):
        events.extend(detector.process_frame(make_frame(n, clutter_points(rng)), n * FRAME_DT_MS))
    events.extend(detector.flush())
    assert events == []


def test_two_separated_balls_two_events():
    detector = BallDetector()
    events = []
    speed_ms = 90 / 3.6
    direction = math.radians(30)
    for n in range(60):
        t_ms = n * FRAME_DT_MS
        points = []
        for start in (5, 40):  # two deliveries ~1.75s apart
            if start <= n <= start + 6:
                t_s = (n - start) * FRAME_DT_MS / 1000.0
                x, y, doppler = ball_point(t_s, speed_ms, direction)
                points.append(RadarPoint(x=x, y=y, z=0.8, doppler=doppler, snr=16.0, noise=5.0))
        events.extend(detector.process_frame(make_frame(n, points), t_ms))
    events.extend(detector.flush())
    assert len(events) == 2, [e.to_dict() for e in events]
