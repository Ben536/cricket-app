"""
Shared offline-tuning machinery: load a recording, run the detector over it,
score the result against the wagon-wheel ground truth.

Used by both `tools/replay_jsonl.py` (inspect one run) and
`tools/tune_detector.py` (sweep parameters). It lives here rather than in
either tool so the matching and scoring rules cannot drift apart - the two
tools must agree on what "a detection matched a tap" means or their numbers
are not comparable.

Ground truth is a tap on the wagon wheel: `direction_deg` in the FIELD frame
(0 = bowler, +90 = leg) and optionally `distance_norm` and `outcome`.
Detections carry `direction_sensor_deg` in the SENSOR frame. The two differ
by the mount rotation, which is exactly what `fit_yaw` recovers - so
direction error can only be judged AFTER a fit, and a fit needs matches. The
scoring below therefore reports detection quality (recall/precision) and the
fit residual separately.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from radar.detector import BallDetector, BallEvent, DetectorParams
from radar.geometry import MountCalibration, fit_yaw, wrap_deg
from radar.tlv import RadarFrame, RadarPoint

# A tap lands within this many ms of the ball it describes. Generous: the
# operator taps after seeing where the ball went, not at the moment of contact.
MATCH_WINDOW_MS = 1500


@dataclass
class Recording:
    """A loaded capture: metadata, frames on the recording clock, and taps."""
    path: Path
    meta: dict = field(default_factory=dict)
    frames: list[tuple[int, RadarFrame]] = field(default_factory=list)
    annotations: list[dict] = field(default_factory=list)
    # Frames recorded while the radar was absent. Non-zero with is_mock False
    # means the radar dropped out part-way: the file looks genuine but part
    # of it is fabricated.
    mock_frame_count: int = 0
    mode_changes: int = 0

    @property
    def is_mock(self) -> bool:
        return bool(self.meta.get("mock"))

    @property
    def partial_mock(self) -> bool:
        return not self.is_mock and self.mock_frame_count > 0

    @property
    def session_type(self) -> str:
        return self.meta.get("session_type", self.path.parent.name)

    @property
    def duration_s(self) -> float:
        return (self.frames[-1][0] - self.frames[0][0]) / 1000.0 if len(self.frames) > 1 else 0.0

    @property
    def frame_rate_hz(self) -> Optional[float]:
        return (len(self.frames) - 1) / self.duration_s if self.duration_s > 0 else None

    @property
    def labelled(self) -> list[dict]:
        """Taps that carry a direction - the only ones usable as truth."""
        return [a for a in self.annotations if isinstance(a.get("direction_deg"), (int, float))]


def load_recording(path: Path) -> Recording:
    """Read a JSONL capture. Truncated final lines (a crash) are skipped."""
    rec = Recording(path=Path(path))
    cur_mock = False
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind == "meta":
                rec.meta = obj
                cur_mock = bool(obj.get("mock", False))
            elif kind == "mode_change":
                cur_mock = bool(obj.get("mock"))
                rec.mode_changes += 1
            elif kind == "frame":
                if cur_mock:
                    rec.mock_frame_count += 1
                t_ms = obj.get("t_ms", obj.get("timestamp_ms", 0))
                rec.frames.append((t_ms, RadarFrame(
                    frame_number=obj.get("frame_number", 0),
                    cpu_time_ms=obj.get("cpu_time_ms", obj.get("timestamp_ms", 0)),
                    num_points=obj.get("num_points", len(obj.get("points", []))),
                    points=[
                        RadarPoint(x=p["x"], y=p["y"], z=p["z"],
                                   doppler=p.get("doppler", p.get("v", 0.0)),
                                   snr=p.get("snr", 0.0), noise=p.get("noise", 0.0))
                        for p in obj.get("points", [])
                    ],
                )))
            elif kind == "annotation":
                rec.annotations.append(obj)
    return rec


def detect(rec: Recording, params: DetectorParams,
           calibration: Optional[MountCalibration] = None) -> list[BallEvent]:
    """Run the detector over a loaded recording."""
    detector = BallDetector(params, calibration=calibration)
    events: list[BallEvent] = []
    for t_ms, frame in rec.frames:
        events.extend(detector.process_frame(frame, t_ms))
    events.extend(detector.flush())
    return events


@dataclass
class Match:
    annotation: dict
    event: BallEvent
    dt_ms: int


@dataclass
class Scoring:
    matches: list[Match]
    unmatched_events: list[BallEvent]   # detections with no tap near them
    missed: list[dict]                  # taps with no detection near them
    n_taps: int
    n_events: int

    @property
    def recall(self) -> Optional[float]:
        return len(self.matches) / self.n_taps if self.n_taps else None

    @property
    def precision(self) -> Optional[float]:
        return len(self.matches) / self.n_events if self.n_events else None

    @property
    def f1(self) -> Optional[float]:
        r, p = self.recall, self.precision
        if r is None or p is None or (r + p) == 0:
            return None
        return 2 * r * p / (r + p)


def match_events(events: Sequence[BallEvent], annotations: Sequence[dict],
                 window_ms: int = MATCH_WINDOW_MS) -> Scoring:
    """Pair detections with taps, closest in time first.

    Global closest-first rather than per-tap greedy: with several balls in
    quick succession, taking each tap's nearest free event in file order can
    steal an event that fitted a later tap far better.
    """
    pairs = []
    for ai, ann in enumerate(annotations):
        t = ann.get("t_ms", 0)
        for ei, ev in enumerate(events):
            dt = abs(ev.t_end_ms - t)
            if dt <= window_ms:
                pairs.append((dt, ai, ei))
    pairs.sort()

    used_ann, used_ev = set(), set()
    matches: list[Match] = []
    for dt, ai, ei in pairs:
        if ai in used_ann or ei in used_ev:
            continue
        used_ann.add(ai)
        used_ev.add(ei)
        matches.append(Match(annotation=annotations[ai], event=events[ei], dt_ms=dt))

    matches.sort(key=lambda m: m.event.t_start_ms)
    return Scoring(
        matches=matches,
        unmatched_events=[e for i, e in enumerate(events) if i not in used_ev],
        missed=[a for i, a in enumerate(annotations) if i not in used_ann],
        n_taps=len(annotations),
        n_events=len(events),
    )


def fit_from_matches(matches: Sequence[Match]) -> Optional[tuple[float, bool, float]]:
    """Fit (yaw_deg, mirror, rms_deg) from matched (sensor, truth) directions."""
    pairs = [(m.event.direction_sensor_deg, float(m.annotation["direction_deg"]))
             for m in matches
             if isinstance(m.annotation.get("direction_deg"), (int, float))]
    return fit_yaw(pairs)


def speed_error(matches: Sequence[Match]) -> Optional[float]:
    """Mean |error| in km/h against taps that carry a truth speed, if any.

    Most taps will not have one - the operator cannot judge exit speed - so
    this is usually None. It exists for the case where a speed gun is used
    alongside, which is the only way to validate speed in the field.
    """
    errs = [abs(m.event.speed_kmh - float(m.annotation["speed_kmh"]))
            for m in matches if isinstance(m.annotation.get("speed_kmh"), (int, float))]
    return sum(errs) / len(errs) if errs else None


def direction_residual(matches: Sequence[Match], fit: Optional[tuple[float, bool, float]]) -> Optional[float]:
    """RMS direction error in degrees after applying a fitted mount."""
    if fit is None or not matches:
        return None
    yaw, mirror, _ = fit
    cal = MountCalibration(yaw_deg=yaw, mirror=mirror, calibrated=True)
    residuals = []
    for m in matches:
        truth = m.annotation.get("direction_deg")
        if not isinstance(truth, (int, float)):
            continue
        rad = math.radians(m.event.direction_sensor_deg)
        predicted = cal.field_direction_deg(math.sin(rad), math.cos(rad))
        residuals.append(wrap_deg(predicted - float(truth)))
    if not residuals:
        return None
    return (sum(r * r for r in residuals) / len(residuals)) ** 0.5
