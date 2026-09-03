"""
Sensor frame -> field frame for the OVERHEAD mount.

Product constraint (2026-07-03): the IWR6843ISK-ODS is always mounted above
the batter, looking straight down. The TI point cloud is reported in the
SENSOR frame:

    y = boresight = range axis       -> points DOWN at the ground
    x = lateral
    z = elevation                    -> under the overhead mount, the SECOND
                                        ground-plane axis

Verified against the only real recording: y is sign-constrained (0.6% of
points negative - it is a range), x and z are two-sided. The detector used
to compute direction as atan2(x, y), i.e. in the wrong plane, and discarded
z entirely: a straight drive read as +180 and shots at +30 and +150 both
read +113. That map is many-to-one, so no offset could rescue it.

The FIELD frame is the engine's: batter at the origin, +Y toward the bowler,
+X leg side (right-handed batter). Going from the ground-plane sensor axes
(u = x, v = z) to field axes needs the mount's yaw (how the sensor is
rotated about the vertical) and possibly a mirror (the sensor's ground
axes may have the opposite handedness to the field, depending on which way
the board faces). Neither can be known from the code - they are fitted from
wagon-wheel taps (tools/replay_jsonl.py --fit-yaw) and stored in
radar/mount.json. Until that has been done `calibrated` is false and nothing
downstream may treat a sensor-frame direction as a field direction.

Angle conventions, all in degrees:
    sensor direction   atan2(u, v)      0 = +v axis, +90 = +u axis
    field direction    atan2(X, Y)      0 = bowler, +90 = LEG   (wagon-wheel taps)
    engine angle       atan2(-X, Y)     0 = bowler, +90 = OFF   (simulate_shot)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_MOUNT_PATH = Path(__file__).resolve().parent / "mount.json"

# Which sensor axis is which under the overhead mount. Named so the
# assumption is visible and testable rather than implicit in index arithmetic.
SENSOR_DOWN_AXIS = "y"
SENSOR_GROUND_AXES = ("x", "z")


def ground_plane(x: float, y: float, z: float) -> tuple[float, float]:
    """(u, v): the two horizontal sensor axes. y (range/boresight) is vertical."""
    return x, z


def height_above_ground(y: float, mount_height_m: float) -> float:
    """y is the range DOWN from the sensor, so height = mount height - y."""
    return mount_height_m - y


G_M_S2 = 9.81


def vertical_angle_deg(dx: float, dy: float, dz: float) -> float:
    """Elevation of a displacement vector (the chord) above the ground plane.
    Needs only the axis convention (not the mount height): -dy is 'up'."""
    horizontal = math.hypot(dx, dz)
    return math.degrees(math.atan2(-dy, horizontal))


def launch_vertical_angle_deg(dx: float, dy: float, dz: float, dt_s: float) -> float:
    """The LAUNCH elevation, compensating for gravity over the observed
    segment. The chord of a 0.3s segment dips ~0.44m below the launch line,
    so a 3-degree drive reads as flat or falling from the chord alone.
    With h(t) = h0 + vz*t - g*t^2/2 and rise = -dy over dt:
        vz = (rise + g*dt^2/2) / dt
    """
    if dt_s <= 0:
        return vertical_angle_deg(dx, dy, dz)
    horizontal_speed = math.hypot(dx, dz) / dt_s
    vz0 = (-dy + 0.5 * G_M_S2 * dt_s * dt_s) / dt_s
    return math.degrees(math.atan2(vz0, horizontal_speed))


def sensor_direction_deg(dx: float, dz: float) -> float:
    """Horizontal direction of travel in the sensor's ground plane."""
    return math.degrees(math.atan2(dx, dz))


def wrap_deg(a: float) -> float:
    return ((a + 180.0) % 360.0) - 180.0


class NotCalibratedError(RuntimeError):
    """The mount yaw/mirror have not been fitted; refuse to emit field-frame angles."""


@dataclass(frozen=True)
class MountCalibration:
    mount_height_m: float = 3.0
    yaw_deg: float = 0.0
    mirror: bool = False
    calibrated: bool = False
    notes: str = ""

    def sensor_to_field(self, u: float, v: float) -> tuple[float, float]:
        """Rotate the ground-plane sensor axes into field axes (X=leg, Y=bowler)."""
        psi = math.radians(self.yaw_deg)
        x = u * math.cos(psi) - v * math.sin(psi)
        y = u * math.sin(psi) + v * math.cos(psi)
        if self.mirror:
            x = -x
        return x, y

    def field_direction_deg(self, du: float, dv: float) -> float:
        """0 = bowler, +90 = leg (the wagon-wheel / annotation convention)."""
        self._require_calibrated()
        x, y = self.sensor_to_field(du, dv)
        return math.degrees(math.atan2(x, y))

    def engine_angle_deg(self, du: float, dv: float) -> float:
        """The game engine's horizontal_angle: 0 = bowler, +90 = OFF."""
        self._require_calibrated()
        x, y = self.sensor_to_field(du, dv)
        return math.degrees(math.atan2(-x, y))

    def _require_calibrated(self) -> None:
        if not self.calibrated:
            raise NotCalibratedError(
                "radar/mount.json is not calibrated: fit yaw/mirror from wagon-wheel "
                "taps with `tools/replay_jsonl.py <recording> --fit-yaw` first"
            )

    # --- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path = DEFAULT_MOUNT_PATH) -> "MountCalibration":
        data = json.loads(Path(path).read_text())
        return cls(
            mount_height_m=float(data.get("mount_height_m", 3.0)),
            yaw_deg=float(data.get("yaw_deg", 0.0)),
            mirror=bool(data.get("mirror", False)),
            calibrated=bool(data.get("calibrated", False)),
            notes=str(data.get("notes", "")),
        )

    def save(self, path: Path = DEFAULT_MOUNT_PATH) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")


# ---------------------------------------------------------------------------
# Fitting the mount from ground truth
# ---------------------------------------------------------------------------

def _circular_mean_deg(angles: Sequence[float]) -> float:
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(s, c))


def fit_yaw(pairs: Sequence[tuple[float, float]]) -> Optional[tuple[float, bool, float]]:
    """
    Fit (yaw_deg, mirror) from (sensor_direction_deg, truth_field_direction_deg)
    pairs - detected direction vs the wagon-wheel tap for the same ball.

    With sensor direction a = atan2(u, v) and field direction f = atan2(X, Y):
        no mirror:  f = a - yaw      =>  yaw = mean(a - f)
        mirror:     f = yaw - a      =>  yaw = mean(a + f)
    Both hypotheses are fitted; the one with the smaller RMS residual wins.
    Returns (yaw_deg, mirror, rms_residual_deg), or None with fewer than 2 pairs.
    """
    if len(pairs) < 2:
        return None
    best = None
    for mirror in (False, True):
        diffs = [wrap_deg(a + f) if mirror else wrap_deg(a - f) for a, f in pairs]
        yaw = _circular_mean_deg(diffs)
        cal = MountCalibration(yaw_deg=yaw, mirror=mirror, calibrated=True)
        residuals = []
        for a, f in pairs:
            ra = math.radians(a)
            predicted = cal.field_direction_deg(math.sin(ra), math.cos(ra))
            residuals.append(wrap_deg(predicted - f))
        rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
        if best is None or rms < best[2]:
            best = (wrap_deg(yaw), mirror, rms)
    return best
