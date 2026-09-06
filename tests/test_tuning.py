"""
The offline tuning harness: load a recording, score against wagon-wheel
taps, recover the mount from the matches.

These tests synthesise a recording whose ground truth is known exactly - a
set of balls at spread directions under a mount with a KNOWN yaw - and check
that the pipeline the nets session depends on recovers it. Without this, the
first time the harness runs on real data is also the first time anyone finds
out whether it works, and by then the session is over.
"""

import json
import math
import random
import subprocess
import sys
from pathlib import Path

from radar.detector import DetectorParams
from radar.geometry import MountCalibration, wrap_deg
from radar.tuning import (
    MATCH_WINDOW_MS,
    detect,
    direction_residual,
    fit_from_matches,
    load_recording,
    match_events,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_detector import FRAME_DT_MS, clutter_points, overhead_ball  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# The mount we hide in the data and expect the fit to recover.
TRUE_YAW = 35.0
TRUE_MIRROR = False

# Directions in the SENSOR frame, deliberately spread: a fit cannot recover a
# rotation from balls that all went the same way.
BALL_DIRECTIONS = [-120.0, -75.0, -30.0, 0.0, 40.0, 85.0, 130.0, 165.0]


def truth_field_deg(sensor_deg, yaw=TRUE_YAW, mirror=TRUE_MIRROR):
    """What the operator would tap for a ball travelling `sensor_deg`."""
    cal = MountCalibration(yaw_deg=yaw, mirror=mirror, calibrated=True)
    rad = math.radians(sensor_deg)
    return cal.field_direction_deg(math.sin(rad), math.cos(rad))


def write_recording(path, directions=BALL_DIRECTIONS, speed_kmh=95.0,
                    tap_lag_ms=600, mock=False, drop_taps=(), seed=7):
    """A 'both' capture: one ball every 2s, each with a wagon-wheel tap.

    `tap_lag_ms` reproduces the operator tapping AFTER the ball has gone,
    which is what actually happens and what the match window exists for.
    """
    rng = random.Random(seed)
    speed_ms = speed_kmh / 3.6
    frames_per_ball = 40           # 2s at 20Hz
    dwell = (8, 14)                # the ball is visible for ~0.35s

    lines = [{"type": "meta", "session_type": "both", "start_time": "2026-09-10T10:00:00+00:00",
              "mock": mock}]
    taps = []

    n = 0
    for ball_i, direction in enumerate(directions):
        for k in range(frames_per_ball):
            t_ms = n * FRAME_DT_MS
            points = [{"x": p.x, "y": p.y, "z": p.z, "doppler": p.doppler,
                       "snr": p.snr, "noise": p.noise} for p in clutter_points(rng)]
            if dwell[0] <= k <= dwell[1]:
                t_s = (k - dwell[0]) * FRAME_DT_MS / 1000.0
                x, y, z, doppler = overhead_ball(t_s, speed_ms, direction, elevation_deg=6.0)
                points.append({"x": x, "y": y, "z": z, "doppler": doppler,
                               "snr": 17.0, "noise": 5.0})
            lines.append({"type": "frame", "t_ms": t_ms, "frame_number": n,
                          "cpu_time_ms": t_ms, "num_points": len(points), "points": points})
            if k == dwell[1] and ball_i not in drop_taps:
                taps.append({"type": "annotation", "t_ms": t_ms + tap_lag_ms,
                             "direction_deg": round(truth_field_deg(direction), 1),
                             "distance_norm": 0.7, "outcome": "4"})
            n += 1

    lines.extend(taps)
    lines.append({"type": "end", "frame_count": n, "annotation_count": len(taps)})
    lines.sort(key=lambda o: (o.get("t_ms", -1) if o["type"] in ("frame", "annotation") else
                              (-1 if o["type"] == "meta" else 10 ** 12)))
    with open(path, "w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")
    return len(taps)


class TestLoading:
    def test_round_trips_frames_meta_and_taps(self, tmp_path):
        path = tmp_path / "rec.jsonl"
        n_taps = write_recording(path)
        rec = load_recording(path)
        assert rec.meta["session_type"] == "both"
        assert not rec.is_mock
        assert len(rec.frames) == len(BALL_DIRECTIONS) * 40
        assert len(rec.labelled) == n_taps
        assert rec.frame_rate_hz is not None and 19 < rec.frame_rate_hz < 21

    def test_truncated_final_line_is_skipped_not_fatal(self, tmp_path):
        """A recording cut short by a power loss still loads."""
        path = tmp_path / "crashed.jsonl"
        write_recording(path)
        with open(path, "a") as f:
            f.write('{"type": "frame", "t_ms": 999, "poi')
        rec = load_recording(path)
        assert len(rec.frames) == len(BALL_DIRECTIONS) * 40

    def test_mock_flag_is_surfaced(self, tmp_path):
        path = tmp_path / "mock.jsonl"
        write_recording(path, mock=True)
        assert load_recording(path).is_mock is True


class TestMatching:
    def test_every_ball_is_detected_and_paired_with_its_tap(self, tmp_path):
        path = tmp_path / "rec.jsonl"
        write_recording(path)
        rec = load_recording(path)
        events = detect(rec, DetectorParams(), MountCalibration())
        scoring = match_events(events, rec.labelled)
        assert scoring.recall == 1.0, f"missed {len(scoring.missed)} of {scoring.n_taps} balls"
        assert scoring.precision == 1.0, f"{len(scoring.unmatched_events)} false detections"

    def test_a_tap_with_no_ball_counts_as_missed_not_matched(self, tmp_path):
        """The operator taps for a ball the radar never saw."""
        path = tmp_path / "rec.jsonl"
        write_recording(path)
        rec = load_recording(path)
        events = detect(rec, DetectorParams(), MountCalibration())
        orphan = {"type": "annotation", "t_ms": 10 ** 6, "direction_deg": 12.0}
        scoring = match_events(events, rec.labelled + [orphan])
        assert orphan in scoring.missed
        assert scoring.recall < 1.0

    def test_taps_outside_the_window_do_not_match(self, tmp_path):
        """An operator who taps a full ball late is not describing this ball.

        Single-ball fixture on purpose: with balls 2s apart a very late tap
        legitimately lands on the NEXT ball, which is correct behaviour and
        would mask what this test is checking.
        """
        path = tmp_path / "one.jsonl"
        write_recording(path, directions=[40.0], tap_lag_ms=MATCH_WINDOW_MS + 800)
        rec = load_recording(path)
        events = detect(rec, DetectorParams(), MountCalibration())
        assert len(events) == 1, "the fixture must produce exactly one ball"
        scoring = match_events(events, rec.labelled)
        assert scoring.matches == []
        assert scoring.recall == 0.0
        assert len(scoring.unmatched_events) == 1

    def test_two_taps_cannot_both_claim_one_event(self, tmp_path):
        """A double-tap on the same ball must not count as two balls found."""
        path = tmp_path / "one.jsonl"
        write_recording(path, directions=[40.0])
        rec = load_recording(path)
        events = detect(rec, DetectorParams(), MountCalibration())
        assert len(events) == 1
        first_tap = dict(rec.labelled[0])
        duplicate = dict(first_tap, t_ms=first_tap["t_ms"] + 10)
        scoring = match_events(events, [first_tap, duplicate])
        assert len(scoring.matches) == 1
        assert len(scoring.missed) == 1
        assert scoring.recall == 0.5

    def test_the_closest_tap_wins_a_contested_event(self, tmp_path):
        """Matching is global closest-first, not first-come: the tap nearest
        in time gets the ball, whichever order they appear in the file."""
        path = tmp_path / "one.jsonl"
        write_recording(path, directions=[40.0])
        rec = load_recording(path)
        events = detect(rec, DetectorParams(), MountCalibration())
        t_end = events[0].t_end_ms
        far = {"type": "annotation", "t_ms": t_end + 1200, "direction_deg": 1.0}
        near = {"type": "annotation", "t_ms": t_end + 100, "direction_deg": 2.0}
        scoring = match_events(events, [far, near])   # far listed first
        assert scoring.matches[0].annotation is near
        assert scoring.missed == [far]


class TestMountRecovery:
    def test_fit_recovers_the_yaw_hidden_in_the_data(self, tmp_path):
        """The whole point of the nets session, end to end."""
        path = tmp_path / "rec.jsonl"
        write_recording(path)
        rec = load_recording(path)
        events = detect(rec, DetectorParams(), MountCalibration())
        scoring = match_events(events, rec.labelled)
        fit = fit_from_matches(scoring.matches)
        assert fit is not None
        yaw, mirror, rms = fit
        assert abs(yaw - TRUE_YAW) < 5.0, f"recovered yaw {yaw}, expected {TRUE_YAW}"
        assert mirror is TRUE_MIRROR
        assert rms < 10.0, f"fit rms {rms} deg is too loose to trust"
        assert direction_residual(scoring.matches, fit) < 10.0

    def test_a_mirrored_mount_is_identified_as_mirrored(self, tmp_path):
        """Mounting the board face-flipped inverts the wheel; the fit must
        catch it rather than returning a yaw that silently swaps off and leg."""
        path = tmp_path / "mirrored.jsonl"
        rng_dirs = BALL_DIRECTIONS
        cal = MountCalibration(yaw_deg=20.0, mirror=True, calibrated=True)
        write_recording(path, directions=rng_dirs)
        rec = load_recording(path)
        # Re-label the taps as a mirrored mount would have them
        for ann, sensor_deg in zip(rec.annotations, rng_dirs):
            rad = math.radians(sensor_deg)
            ann["direction_deg"] = round(cal.field_direction_deg(math.sin(rad), math.cos(rad)), 1)
        events = detect(rec, DetectorParams(), MountCalibration())
        fit = fit_from_matches(match_events(events, rec.labelled).matches)
        assert fit is not None
        yaw, mirror, rms = fit
        assert mirror is True, "a mirrored mount was fitted as un-mirrored"
        assert abs(yaw - 20.0) < 5.0 and rms < 10.0

    def test_all_balls_in_one_direction_cannot_pin_the_mount(self, tmp_path):
        """Why the checklist insists on SPREAD directions.

        With every ball going the same way, the mirrored and un-mirrored
        hypotheses explain the data equally well, so a small RMS proves
        nothing - the fit can report a confident-looking number for a mount
        that is actually flipped. Spread is what breaks the tie, and this
        test is the evidence for that instruction.
        """
        def rms_for(pairs, yaw, mirror):
            cal = MountCalibration(yaw_deg=yaw, mirror=mirror, calibrated=True)
            res = []
            for sensor_deg, truth in pairs:
                rad = math.radians(sensor_deg)
                res.append(wrap_deg(cal.field_direction_deg(math.sin(rad), math.cos(rad)) - truth))
            return math.sqrt(sum(r * r for r in res) / len(res))

        def pairs_for(directions):
            path = tmp_path / f"n{len(directions)}_{directions[0]}.jsonl"
            write_recording(path, directions=directions)
            rec = load_recording(path)
            events = detect(rec, DetectorParams(), MountCalibration())
            matches = match_events(events, rec.labelled).matches
            return [(m.event.direction_sensor_deg, float(m.annotation["direction_deg"]))
                    for m in matches]

        def discrimination(pairs):
            """How much worse the WRONG mirror hypothesis fits, in degrees.
            Near zero means the data cannot tell the two mounts apart."""
            best_true = min(rms_for(pairs, y, False) for y in range(-180, 180))
            best_flip = min(rms_for(pairs, y, True) for y in range(-180, 180))
            return best_flip - best_true

        narrow = discrimination(pairs_for([0.0, 3.0, -3.0, 1.5]))
        spread = discrimination(pairs_for(BALL_DIRECTIONS))

        # Measured: ~4 deg of separation from a 6-deg spread of shots, versus
        # >100 deg once the shots cover the field. The wrong mount is only
        # obviously wrong when the taps span real angles.
        assert narrow < 10.0, f"narrow spread should barely discriminate, got {narrow:.1f} deg"
        assert spread > 20.0, f"a full spread must reject the wrong mirror, got {spread:.1f} deg"
        assert spread > 3 * narrow, (
            f"spreading the shots must sharpen the fit substantially; "
            f"narrow={narrow:.1f} spread={spread:.1f} deg")


class TestTunerCLI:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / "tune_detector.py"), *map(str, args)],
            capture_output=True, text=True, cwd=REPO, timeout=600,
        )

    def test_refuses_mock_recordings(self, tmp_path):
        """64 of the 65 recordings on this laptop are fabricated. Tuning on
        them silently produces meaningless parameters, so the tool must
        refuse rather than report a number."""
        path = tmp_path / "mock.jsonl"
        write_recording(path, mock=True)
        r = self._run(path)
        assert r.returncode == 2
        assert "MOCK" in r.stderr
        assert "No real recordings" in r.stderr

    def test_refuses_when_there_is_no_ground_truth(self, tmp_path):
        path = tmp_path / "untapped.jsonl"
        write_recording(path, drop_taps=tuple(range(len(BALL_DIRECTIONS))))
        r = self._run(path)
        assert r.returncode == 2
        assert "ground truth" in r.stderr

    def test_reports_a_fit_and_reproduction_flags(self, tmp_path):
        path = tmp_path / "rec.jsonl"
        write_recording(path)
        r = self._run(path, "--json")
        assert r.returncode == 0, r.stderr
        report = json.loads(r.stdout)
        assert report["taps"] == len(BALL_DIRECTIONS)
        assert report["best"]["recall"] == 1.0
        assert report["best"]["fit"]["mirror"] is False
        assert abs(report["best"]["fit"]["yaw_deg"] - TRUE_YAW) < 5.0
        assert "set_flags" in report["best"]

    def test_recovers_recall_from_a_deliberately_broken_baseline(self, tmp_path):
        """A too-strict gate finds nothing. The sweep has to notice and say
        which parameter to loosen - this is the on-site salvage path when a
        capture reads 0% recall."""
        path = tmp_path / "rec.jsonl"
        write_recording(path, speed_kmh=95.0)
        rec = load_recording(path)

        strict = DetectorParams(min_doppler=40.0)  # above any real ball's radial speed
        assert len(detect(rec, strict, MountCalibration())) == 0

        r = self._run(path, "--json")
        report = json.loads(r.stdout)
        assert report["best"]["recall"] >= 0.9

    def test_min_recall_constraint_is_respected(self, tmp_path):
        path = tmp_path / "rec.jsonl"
        write_recording(path)
        r = self._run(path, "--json", "--min-recall", "0.8")
        assert r.returncode == 0, r.stderr
        report = json.loads(r.stdout)
        assert report["min_recall"] == 0.8
        assert report["best"]["recall"] >= 0.8
