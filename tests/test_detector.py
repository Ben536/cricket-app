"""
Ball detector: synthetic ball crossings in the OVERHEAD sensor frame.

The previous fixtures built trajectories in a forward-looking frame (x/y
horizontal, fixed z) - the geometry the product does not have - and
tolerated +/-30% on speed, which is why a -15% bias and a 180-degree
direction error survived. These fixtures put the sensor above the batter
looking down (+y = down, ground plane = x/z), use the TI sign convention
(positive doppler = moving away), and hold speed to +/-5% and direction to
+/-3 degrees.

Real-signature tuning happens against nets recordings via
tools/replay_jsonl.py; these tests pin the pipeline mechanics.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from radar.detector import BallDetector, BallEvent, DetectorParams
from radar.geometry import MountCalibration, NotCalibratedError, fit_yaw, wrap_deg
from radar.profile_cfg import load_profile, parse_profile
from radar.tlv import RadarFrame, RadarPoint

FRAME_DT_MS = 50  # 20Hz
MOUNT_HEIGHT = 3.0
G = 9.81


def make_frame(n, points):
    return RadarFrame(frame_number=n, cpu_time_ms=n * FRAME_DT_MS,
                      num_points=len(points), points=points)


def clutter_points(rng):
    """Static-ish clutter: near-zero doppler, low SNR - must be gated out."""
    return [
        RadarPoint(x=rng.uniform(-3, 3), y=rng.uniform(1, 3), z=rng.uniform(-3, 3),
                   doppler=rng.uniform(-1.5, 1.5), snr=rng.uniform(3, 9), noise=5.0)
        for _ in range(rng.randint(0, 4))
    ]


def overhead_ball(t_s, speed_ms, direction_deg, elevation_deg=5.0, start=(0.0, 1.0, 0.0)):
    """A ball leaving the bat 1m above ground beneath the sensor, in the
    SENSOR frame: x = lateral, z = second ground axis, y = range DOWN.

    Returns (x, y, z, doppler). Doppler is the velocity projected on the
    line of sight, positive when moving away (TI convention)."""
    phi = math.radians(direction_deg)
    eps = math.radians(elevation_deg)
    vh = speed_ms * math.cos(eps)
    vu, vv = vh * math.sin(phi), vh * math.cos(phi)
    u0, h0, v0 = start
    u = u0 + vu * t_s
    v = v0 + vv * t_s
    h = h0 + speed_ms * math.sin(eps) * t_s - 0.5 * G * t_s * t_s
    y = MOUNT_HEIGHT - h
    vel = (vu, -(speed_ms * math.sin(eps) - G * t_s), vv)  # dy/dt = -dh/dt
    r = math.sqrt(u * u + y * y + v * v) or 1.0
    doppler = (vel[0] * u + vel[1] * y + vel[2] * v) / r
    return u, y, v, doppler


def wrap_doppler(doppler, v_max):
    """What the radar reports when |doppler| exceeds its unambiguous limit."""
    return ((doppler + v_max) % (2 * v_max)) - v_max


def run_ball_crossing(speed_kmh=100.0, direction_deg=60.0, elevation_deg=5.0, n_frames=30,
                      with_bat=False, bat_offset=None, wrap_v_max=None, params=None,
                      calibration=None, visible=(8, 14)):
    """wrap_v_max: simulate the radar's ambiguity by wrapping doppler at this
    limit. Whether the detector KNOWS the limit is params.v_max_ms."""
    rng = random.Random(7)
    speed_ms = speed_kmh / 3.6
    detector = BallDetector(params or DetectorParams(), calibration=calibration)
    events: list[BallEvent] = []

    for n in range(n_frames):
        t_ms = n * FRAME_DT_MS
        points = clutter_points(rng)

        # Ball visible for a ~0.35s dwell (realistic)
        if visible[0] <= n <= visible[1]:
            t_s = (n - visible[0]) * FRAME_DT_MS / 1000.0
            x, y, z, doppler = overhead_ball(t_s, speed_ms, direction_deg, elevation_deg)
            if wrap_v_max:
                doppler = wrap_doppler(doppler, wrap_v_max)
            points.append(RadarPoint(x=x, y=y, z=z, doppler=doppler, snr=17.0, noise=5.0))
            if bat_offset is not None:
                # A single bat-tip return NEXT to the ball with a very
                # different radial velocity - must not be merged into it.
                points.append(RadarPoint(x=x + bat_offset, y=y, z=z, doppler=8.0, snr=15.0, noise=5.0))

        # A bat swing: a large fast cluster - must be rejected by cluster size
        if with_bat and visible[0] <= n <= visible[0] + 3:
            for i in range(7):
                points.append(RadarPoint(x=0.2 + i * 0.15, y=1.5 + i * 0.1,
                                         z=1.0 + i * 0.05, doppler=8.0, snr=15.0, noise=5.0))

        events.extend(detector.process_frame(make_frame(n, points), t_ms))

    events.extend(detector.flush())
    return events


# ---------------------------------------------------------------------------
# Detection mechanics
# ---------------------------------------------------------------------------

def test_detects_single_ball_crossing():
    events = run_ball_crossing()
    assert len(events) == 1, f"expected exactly one ball event, got {len(events)}"
    ev = events[0]
    assert ev.n_hits >= 3
    assert 8 * FRAME_DT_MS <= ev.t_start_ms <= 10 * FRAME_DT_MS


@pytest.mark.parametrize("speed_kmh", [60.0, 100.0, 140.0])
def test_speed_recovered_within_5_percent(speed_kmh):
    """Per-point cos correction. The old mean-then-divide read ~15% low."""
    ev = run_ball_crossing(speed_kmh=speed_kmh)[0]
    assert ev.radial_speed_kmh <= speed_kmh * 1.02, "radial is a lower bound"
    assert abs(ev.speed_kmh - speed_kmh) / speed_kmh < 0.05, (ev.speed_kmh, ev.to_dict())
    assert abs(ev.speed_track_kmh - speed_kmh) / speed_kmh < 0.05


@pytest.mark.parametrize("direction_deg", list(range(-180, 180, 15)))
def test_direction_round_trip_across_the_full_circle(direction_deg):
    """Direction is computed in the ground plane (x, z), not (x, y). The old
    atan2(x, y) mapped a straight drive to +180 and +30/+150 both to +113."""
    events = run_ball_crossing(direction_deg=direction_deg)
    assert len(events) == 1, direction_deg
    err = abs(wrap_deg(events[0].direction_sensor_deg - direction_deg))
    assert err < 3.0, (direction_deg, events[0].direction_sensor_deg)


@pytest.mark.parametrize("elevation_deg", [3.0, 15.0, 30.0])
def test_launch_elevation_recovered(elevation_deg):
    """The LAUNCH elevation, gravity-compensated. The chord of a 0.3s
    segment dips ~0.44m, so a 3-degree drive read as flat-or-falling from
    the chord alone."""
    ev = run_ball_crossing(elevation_deg=elevation_deg, speed_kmh=110)[0]
    assert abs(ev.vertical_angle_deg - elevation_deg) < 3.0, (elevation_deg, ev.vertical_angle_deg)


def test_doppler_is_dealiased_against_the_track():
    """A ball whose radial speed exceeds the profile's unambiguous limit is
    reported wrapped. Knowing the limit, the detector unwraps each point
    against the track's own displacement velocity. (Set the limit to 20 m/s
    so a 100 km/h ball wraps but stays above the doppler gate; at the
    non-extended 13 m/s limit a cricket ball's radial speed lands near 2 x
    v_max and reads as STATIC - which is the 'extendedMaxVelocity not
    engaging' scenario, and why that setting must be verified on hardware.)"""
    limit = 20.0
    ev = run_ball_crossing(speed_kmh=100.0, wrap_v_max=limit,
                           params=DetectorParams(v_max_ms=limit))[0]
    assert ev.aliased is True
    assert abs(ev.speed_kmh - 100.0) / 100.0 < 0.06, ev.to_dict()

    # The same wrapped data with NO knowledge of the limit is far too slow -
    # the silent -21% case in the 2026-08 review, here worse.
    naive = run_ball_crossing(speed_kmh=100.0, wrap_v_max=limit, params=DetectorParams())
    assert not naive or naive[0].speed_kmh < 80.0, [e.to_dict() for e in naive]

    # And an unwrapped ball with a known limit is not touched
    plain = run_ball_crossing(speed_kmh=100.0, params=DetectorParams(v_max_ms=39.0))[0]
    assert plain.aliased is False
    assert abs(plain.speed_kmh - 100.0) / 100.0 < 0.05


def test_a_ball_reading_as_static_is_not_detected_and_that_is_the_hardware_risk():
    """If extendedMaxVelocity is NOT engaging, v_max is ~13 m/s and a 100
    km/h ball's radial speed (~26 m/s) wraps to ~0: the radar reports the
    ball as static clutter and no doppler-gated detector can see it. This
    pins the failure mode so the field test in the plan (T0.5) has a
    concrete signature: real balls, no events."""
    events = run_ball_crossing(speed_kmh=100.0, wrap_v_max=13.0, params=DetectorParams(v_max_ms=13.0))
    assert events == [], [e.to_dict() for e in events]


def test_hardware_frame_clock_beats_jittered_host_timestamps():
    """The recorder stamps frames on arrival; two frames from one serial read
    batch can be 4ms apart in host time (seen in the real recording). With
    the profile's frame period known, tracking uses the frame counter and the
    speed is still right; without it, the jitter corrupts the estimate."""
    speed_kmh = 100.0
    rng = random.Random(11)

    def run(params):
        detector = BallDetector(params)
        events = []
        host_t = 0
        for n in range(30):
            # host time: nominal 50ms, but every third frame arrives 4ms after
            # the previous one (batched), the next one 96ms later
            host_t += 4 if n % 3 == 2 else (96 if n % 3 == 0 and n else 50)
            points = clutter_points(rng)
            if 8 <= n <= 14:
                t_s = (n - 8) * FRAME_DT_MS / 1000.0
                x, y, z, doppler = overhead_ball(t_s, speed_kmh / 3.6, 45.0)
                points.append(RadarPoint(x=x, y=y, z=z, doppler=doppler, snr=17.0, noise=5.0))
            events.extend(detector.process_frame(make_frame(n, points), host_t))
        events.extend(detector.flush())
        return events

    with_clock = run(DetectorParams(frame_period_ms=FRAME_DT_MS))
    assert len(with_clock) == 1, [e.to_dict() for e in with_clock]
    ev = with_clock[0]
    assert abs(ev.speed_kmh - speed_kmh) / speed_kmh < 0.05
    assert abs(ev.speed_track_kmh - speed_kmh) / speed_kmh < 0.05
    # Event times stay on the HOST clock so annotations still align
    assert ev.t_start_ms != 8 * FRAME_DT_MS and ev.t_end_ms > ev.t_start_ms
    # (The displacement speed uses only the first and last strong points, so
    # symmetric jitter cancels over the segment; the clock matters for the
    # per-hop gate and segment checks, which is where the 4ms hop in the real
    # recording turned a 0.3m step into a 75 m/s teleport.)


def test_frame_counter_reset_does_not_strand_tracks():
    """A radar restart (or the mock source restarting at 1) sends the
    hardware frame counter backwards. The tracking clock used to follow it,
    so a live track was neither associated nor expired until the counter
    caught up - minutes. Now the clock stays monotonic, live tracks are
    closed at the reset, and the next ball is detected on its own."""
    speed_ms = 100 / 3.6
    detector = BallDetector(DetectorParams(frame_period_ms=FRAME_DT_MS))
    events = []
    host_t = 0
    # Ball 1 on frames 300..306 (counter high) with its track still LIVE when
    # the counter resets to 1 on the very next frame; ball 2 on frames 8..14.
    schedule = [(300 + i, True, i) for i in range(7)] + [(1 + i, 7 <= i <= 13, i - 7) for i in range(30)]
    first_event_at = None
    for idx, (fn, ball, k) in enumerate(schedule):
        host_t += FRAME_DT_MS
        points = []
        if ball:
            x, y, z, doppler = overhead_ball(k * FRAME_DT_MS / 1000.0, speed_ms, 30.0)
            points.append(RadarPoint(x=x, y=y, z=z, doppler=doppler, snr=17.0, noise=5.0))
        out = detector.process_frame(make_frame(fn, points), host_t)
        if out and first_event_at is None:
            first_event_at = idx
        events.extend(out)
    events.extend(detector.flush())
    assert len(events) == 2, [e.to_dict() for e in events]
    for ev in events:
        assert abs(ev.speed_kmh - 100) / 100 < 0.05
    # The first ball's event was emitted AT the reset frame (idx 7), not
    # stranded until flush() at the end of the stream
    assert first_event_at == 7, first_event_at


def test_bat_swing_cluster_rejected():
    events = run_ball_crossing(with_bat=True)
    assert len(events) == 1, "bat cluster must not create a second ball event"


def test_bat_tip_next_to_the_ball_does_not_corrupt_its_speed():
    """Clustering in (x, y, z, doppler): a return 0.4m from the ball with an
    incompatible radial velocity used to be averaged into the centroid,
    dragging a 100 km/h ball to ~61 km/h."""
    events = run_ball_crossing(speed_kmh=100.0, bat_offset=0.4)
    assert len(events) >= 1
    ball = max(events, key=lambda e: e.speed_kmh)
    assert abs(ball.speed_kmh - 100.0) / 100.0 < 0.06, [e.to_dict() for e in events]


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
    for n in range(60):
        t_ms = n * FRAME_DT_MS
        points = []
        for start in (5, 40):  # two deliveries ~1.75s apart
            if start <= n <= start + 6:
                t_s = (n - start) * FRAME_DT_MS / 1000.0
                x, y, z, doppler = overhead_ball(t_s, speed_ms, 30.0)
                points.append(RadarPoint(x=x, y=y, z=z, doppler=doppler, snr=16.0, noise=5.0))
        events.extend(detector.process_frame(make_frame(n, points), t_ms))
    events.extend(detector.flush())
    assert len(events) == 2, [e.to_dict() for e in events]


def test_real_world_static_clutter_produces_no_events():
    """Regression fixture from the FIRST real IWR6843 recording (2026-07-03):
    an empty room whose multipath/aliasing ghosts claim ~26 m/s doppler and
    once produced 33 false ball events. Motion-consistency, straightness and
    segment checks must silence it completely - with and without v_max."""
    fixture = Path(__file__).parent / "fixtures" / "real_static_clutter.jsonl"
    profile = load_profile()
    for params in (DetectorParams(), DetectorParams.from_profile(profile)):
        detector = BallDetector(params)
        events = []
        for line in fixture.read_text().splitlines():
            obj = json.loads(line)
            if obj.get("type") != "frame":
                continue
            frame = RadarFrame(
                frame_number=obj["frame_number"],
                cpu_time_ms=obj["cpu_time_ms"],
                num_points=obj["num_points"],
                points=[RadarPoint(**p) for p in obj["points"]],
            )
            events.extend(detector.process_frame(frame, obj["t_ms"]))
        events.extend(detector.flush())
        assert events == [], f"{len(events)} ghost events from real static clutter"


# ---------------------------------------------------------------------------
# Field-frame output requires calibration
# ---------------------------------------------------------------------------

def test_uncalibrated_mount_yields_no_field_angles():
    ev = run_ball_crossing(direction_deg=40.0, calibration=MountCalibration.load())[0]
    assert ev.horizontal_angle_deg is None and ev.field_direction_deg is None
    assert abs(wrap_deg(ev.direction_sensor_deg - 40.0)) < 3.0
    with pytest.raises(NotCalibratedError):
        MountCalibration().engine_angle_deg(1.0, 0.0)


@pytest.mark.parametrize("yaw,mirror", [(0.0, False), (30.0, False), (-75.0, True), (180.0, True)])
def test_calibrated_mount_maps_sensor_direction_to_engine_angle(yaw, mirror):
    cal = MountCalibration(yaw_deg=yaw, mirror=mirror, calibrated=True)
    ev = run_ball_crossing(direction_deg=40.0, calibration=cal)[0]
    expected_field = (yaw - 40.0) if mirror else (40.0 - yaw)
    assert abs(wrap_deg(ev.field_direction_deg - expected_field)) < 3.0
    # Engine convention is the mirror of the wagon-wheel convention (+off vs +leg)
    assert abs(wrap_deg(ev.horizontal_angle_deg + ev.field_direction_deg)) < 1e-6


def test_fit_yaw_recovers_the_mount_from_taps():
    rng = random.Random(1)
    for true_yaw, true_mirror in [(25.0, False), (-140.0, True)]:
        cal = MountCalibration(yaw_deg=true_yaw, mirror=true_mirror, calibrated=True)
        pairs = []
        for _ in range(12):
            sensor_dir = rng.uniform(-180, 180)
            rad = math.radians(sensor_dir)
            truth = cal.field_direction_deg(math.sin(rad), math.cos(rad)) + rng.gauss(0, 4)
            pairs.append((sensor_dir, truth))
        yaw, mirror, rms = fit_yaw(pairs)
        assert mirror == true_mirror
        assert abs(wrap_deg(yaw - true_yaw)) < 4.0, (yaw, true_yaw)
        assert rms < 8.0


# ---------------------------------------------------------------------------
# The radar profile
# ---------------------------------------------------------------------------

def test_profile_cfg_derives_the_unambiguous_velocity():
    """profile_cricket.cfg: 60GHz, idle 7us, ramp 24us, 3 TX, 32 loops, 50ms,
    extendedMaxVelocity on. The base limit is ~13 m/s - which is why the
    real recording's ghost doppler sits at exactly 2 x 12.97 m/s - and the
    extended limit ~39 m/s (~140 km/h), NOT the 145 km/h the cfg comment
    claims and nowhere near the 250 km/h the detector used to assume."""
    p = load_profile()
    assert p.num_tx == 3 and p.num_loops == 32
    assert p.frame_period_ms == 50 and p.frame_rate_hz == 20
    assert 12.5 < p.v_max_base_ms < 13.5, p.summary()
    assert (p.range_min_m, p.range_max_m) == (0.25, 12.0)
    # The SHIPPED profile runs in base mode - extendedMaxVelocity was
    # disabled 2026-09-06 because it snapped 88.9% of returns to 2x the base
    # limit and emptied the band a ball reads in. See the .cfg comment.
    assert p.extended_max_velocity is False
    assert abs(p.v_max_ms - p.v_max_base_ms) < 1e-9, p.summary()
    params = DetectorParams.from_profile(p)
    assert params.v_max_ms == p.v_max_ms

    # The extended derivation itself must still be correct, for when the
    # mode is re-tested: 3 TX x 13.0 = 39.0 m/s (140 km/h), NOT the 145 the
    # cfg comment once claimed nor the 250 the detector used to assume.
    ext = parse_profile(load_profile.__globals__["DEFAULT_PROFILE_PATH"].read_text()
                        .replace("extendedMaxVelocity -1 0", "extendedMaxVelocity -1 1"))
    assert ext.extended_max_velocity is True
    assert 37.5 < ext.v_max_ms < 40.5, ext.summary()


def test_profile_without_extended_velocity_has_the_base_limit():
    text = load_profile.__globals__["DEFAULT_PROFILE_PATH"].read_text().replace(
        "extendedMaxVelocity -1 1", "extendedMaxVelocity -1 0")
    assert "extendedMaxVelocity -1 0" in text
    p = parse_profile(text)
    assert p.extended_max_velocity is False
    assert abs(p.v_max_ms - p.v_max_base_ms) < 1e-9


def test_profile_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_profile("frameCfg 0 2 32 0 50 1 0\n")  # no profileCfg
    with pytest.raises(ValueError):
        parse_profile("profileCfg 0 60 7 3 24 0 0 166 1 256 12500 0 0 158\nframeCfg 0 x 32 0 50 1 0\n")
