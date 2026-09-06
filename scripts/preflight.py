#!/usr/bin/env python3
"""
Pre-flight check for a data-gathering session. Run this ON THE PI before you
start recording, and again on the first capture.

Why it exists: 19 of the 20 recordings this project has collected are MOCK -
the radar was not detected when recording started, so the frames are
fabricated and worthless for tuning. Every one of those was a wasted trip.
This refuses to say READY unless the radar is genuinely streaming.

    python3 scripts/preflight.py                 # full check, 10s live capture
    python3 scripts/preflight.py --capture 30    # longer capture (bowl during it)
    python3 scripts/preflight.py --hours 2       # check disk for a 2h session
    python3 scripts/preflight.py --file <recording.jsonl>   # analyse a capture instead

Exit code 0 = READY, 1 = something must be fixed first.

The live capture also answers the project's highest-stakes open question:
is `extendedMaxVelocity` engaging? The radar's base unambiguous velocity is
+/-13.0 m/s (47 km/h); with extendedMaxVelocity and 3 TX it is +/-39.0 m/s
(140 km/h). If it is NOT engaging, every cricket shot aliases and real balls
read as slow or static - no detector can recover that. The evidence is in the
doppler distribution, so it comes free with any capture:

  - any |doppler| above the base limit  -> extended mode IS active
    (base mode physically cannot report a larger value)
  - a pile-up at exactly 2x the base limit is the textbook extended-mode
    misassignment of STATIC targets - also evidence extended mode is on
  - real deliveries reaching toward the extended limit -> confirmed usable
  - real deliveries never exceeding the base limit -> SUSPECT: the profile
    is not doing what the .cfg says
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.profile_cfg import DEFAULT_PROFILE_PATH, RadarProfile, load_profile  # noqa: E402

# Bytes per second of JSONL at 20Hz with a busy scene (measured)
BYTES_PER_SECOND = 175_000
DISK_HEADROOM_MB = 500

SERVICES = [
    "cricket-server.service",
    "cricket-ui.service",
    "cricket-health.service",
    "cricket-radar.service",
]

OK, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    action: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, action: str = "") -> None:
        self.checks.append(Check(name, status, detail, action))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warned(self) -> list[Check]:
        return [c for c in self.checks if c.status == WARN]

    def render(self) -> str:
        width = max((len(c.name) for c in self.checks), default=10)
        lines = []
        for c in self.checks:
            lines.append(f"  [{c.status}] {c.name.ljust(width)}  {c.detail}")
            if c.action and c.status != OK:
                lines.append(f"         {' ' * width}  -> {c.action}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The analysis that answers the v_max question (pure, unit-tested)
# ---------------------------------------------------------------------------

def analyse_capture(frames: list[dict], profile: Optional[RadarProfile]) -> dict:
    """Summarise a set of frames: rate, density, doppler distribution, and
    what that implies about extendedMaxVelocity.

    `frames` are recorder-format dicts: {"t_ms", "num_points", "points":[{doppler,...}]}
    """
    times = [f.get("t_ms", 0) for f in frames]
    counts = [f.get("num_points", len(f.get("points", []))) for f in frames]
    speeds: list[float] = []
    for f in frames:
        for p in f.get("points", []):
            d = p.get("doppler", p.get("v", 0.0))
            if isinstance(d, (int, float)) and math.isfinite(d) and abs(d) < 200:
                speeds.append(abs(d))
    speeds.sort()

    span_s = (max(times) - min(times)) / 1000.0 if len(times) > 1 else 0.0
    out = {
        "frames": len(frames),
        "points": len(speeds),
        "span_s": round(span_s, 2),
        "frame_rate_hz": round((len(frames) - 1) / span_s, 1) if span_s > 0 else None,
        "points_per_frame_avg": round(sum(counts) / len(counts), 1) if counts else 0,
        "points_per_frame_max": max(counts) if counts else 0,
    }

    def pct(q: float) -> Optional[float]:
        if not speeds:
            return None
        return round(speeds[min(len(speeds) - 1, int(q * len(speeds)))], 2)

    out.update({
        "doppler_p50": pct(0.50), "doppler_p90": pct(0.90),
        "doppler_p99": pct(0.99), "doppler_max": round(speeds[-1], 2) if speeds else None,
    })

    if profile is None or not speeds:
        out["vmax_verdict"] = "UNKNOWN"
        out["vmax_detail"] = "no profile or no points"
        return out

    base, extended = profile.v_max_base_ms, profile.v_max_ms
    above_base = sum(1 for v in speeds if v > base * 1.02)
    # The static-misassignment artefact sits at exactly 2x the base limit
    near_2x = sum(1 for v in speeds if abs(v - 2 * base) < 1.0)
    fast = sum(1 for v in speeds if v > base * 2.2)  # beyond the artefact band
    # The band a real ball lives in. A cricket delivery is 17-28 m/s, and its
    # radial component under the mount is less, so genuine motion lands
    # between the clutter floor and the base limit. On 2026-09-06 this band
    # held 0.1% of 215k points while 88.9% sat in the artefact - the measured
    # signature of extendedMaxVelocity destroying the velocity data.
    ball_band = sum(1 for v in speeds if base * 0.3 <= v <= base * 1.85)
    out.update({
        "points_in_ball_band": ball_band,
        "ball_band_pct": round(100.0 * ball_band / len(speeds), 2),
        "artefact_pct": round(100.0 * near_2x / len(speeds), 2),
        "v_max_base_ms": round(base, 2),
        "v_max_ms": round(extended, 2),
        "points_above_base_limit": above_base,
        "points_near_2x_base": near_2x,
        "points_above_artefact_band": fast,
    })

    if not profile.extended_max_velocity:
        # Disabled on purpose (2026-09-06). What matters now is not the mode
        # but whether the doppler band a ball occupies is populated at all.
        if near_2x > len(speeds) * 0.2:
            out["vmax_verdict"] = "ARTEFACT PRESENT"
            out["vmax_detail"] = (
                f"{out['artefact_pct']:.0f}% of points still sit at 2x the base limit "
                f"({2 * base:.1f} m/s) even with extendedMaxVelocity off. The chip is "
                f"probably still running the OLD config - it needs a HARDWARE power-cycle"
            )
        elif ball_band < len(speeds) * 0.005:
            out["vmax_verdict"] = "NO BALL-BAND MOTION"
            out["vmax_detail"] = (
                f"only {out['ball_band_pct']:.2f}% of points fall in {base*0.3:.1f}-"
                f"{base*1.85:.1f} m/s, where a real ball reads. If something was moving "
                f"during this capture, its velocity is not being measured"
            )
        else:
            out["vmax_verdict"] = "BASE MODE OK"
            out["vmax_detail"] = (
                f"extendedMaxVelocity is off by choice: v_max {base:.1f} m/s "
                f"({base * 3.6:.0f} km/h), artefact {out['artefact_pct']:.1f}%, "
                f"{out['ball_band_pct']:.1f}% of points in the ball band. Faster balls "
                f"alias and are de-aliased against track displacement"
            )
    elif above_base == 0:
        out["vmax_verdict"] = "SUSPECT"
        out["vmax_detail"] = (
            f"nothing exceeded the BASE limit {base:.1f} m/s in {len(speeds)} points. "
            f"If there was real fast motion in this capture, extendedMaxVelocity is "
            f"probably not engaging"
        )
    elif fast > 0:
        out["vmax_verdict"] = "CONFIRMED"
        out["vmax_detail"] = (
            f"{fast} point(s) above {base * 2.2:.1f} m/s, up to {speeds[-1]:.1f} m/s - "
            f"the extended range ({extended:.1f} m/s) is live"
        )
    else:
        out["vmax_verdict"] = "ACTIVE (unexercised)"
        out["vmax_detail"] = (
            f"{above_base} point(s) exceed the base limit {base:.1f} m/s "
            f"({near_2x} clustered at 2x base = the static-misassignment artefact), "
            f"so extended mode is ON - but nothing here moved fast enough to prove "
            f"the full {extended:.1f} m/s range. Bowl during a capture to confirm"
        )
    return out


# ---------------------------------------------------------------------------
# Individual checks (Pi-side)
# ---------------------------------------------------------------------------

def check_devices(report: Report) -> None:
    import stat as stat_mod
    for dev, role in (("/dev/ttyUSB0", "config"), ("/dev/ttyUSB1", "data")):
        try:
            st = os.stat(dev)
        except FileNotFoundError:
            report.add(f"radar {role} port", FAIL, f"{dev} not found",
                       "Check the USB DATA cable (charge-only cables enumerate nothing) and the "
                       "powered hub; look for 10c4/CP210x in lsusb, NOT a TI 0451 device")
            continue
        except OSError as e:
            report.add(f"radar {role} port", FAIL, f"cannot stat {dev}: {e}")
            continue
        if not stat_mod.S_ISCHR(st.st_mode):
            report.add(f"radar {role} port", FAIL, f"{dev} is not a character device")
        else:
            report.add(f"radar {role} port", OK, f"{dev} present")


def check_services(report: Report) -> None:
    for svc in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=10)
            state = r.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError) as e:
            report.add(svc, WARN, f"could not query: {e}")
            continue
        if state == "active":
            report.add(svc, OK, "active")
        elif svc == "cricket-radar.service" and state == "activating":
            report.add(svc, WARN, "still waiting for the radar device",
                       "normal for up to 2 minutes after boot")
        else:
            report.add(svc, FAIL, state,
                       f"journalctl -u {svc} -n 50 --no-pager")


# vcgencmd get_throttled bit meanings
THROTTLE_NOW = {0x1: "under-voltage NOW", 0x2: "ARM frequency capped NOW",
                0x4: "throttled NOW", 0x8: "soft temperature limit NOW"}
THROTTLE_EVER = {0x10000: "under-voltage has occurred", 0x20000: "frequency capping has occurred",
                 0x40000: "throttling has occurred", 0x80000: "soft temp limit has occurred"}

PSU_ADVICE = (
    "A Pi 3B+ needs 5.1V/2.5A. A phone charger is typically 5V/1A - about a "
    "third of that - and the radar's draw on top is what browns it out. Use "
    "the official supply or a power bank rated 2.5A+, and prefer a powered hub "
    "for the radar. An undervolted Pi clamps USB power, so the radar never "
    "enumerates."
)


def read_throttled() -> Optional[int]:
    """The raw vcgencmd get_throttled bitmask, or None if unavailable."""
    try:
        r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=10)
        return int(r.stdout.strip().split("=")[1], 16)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def describe_throttled(value: int) -> str:
    flags = [name for bit, name in {**THROTTLE_NOW, **THROTTLE_EVER}.items() if value & bit]
    return f"0x{value:x}" + (f" ({', '.join(flags)})" if flags else " (clean)")


def check_undervoltage(report: Report, before: Optional[int] = None, after: Optional[int] = None) -> None:
    """Undervoltage clamps USB power and the radar never enumerates - the
    root cause of the 2026-06 saga AND the 2026-09 boot cycling.

    `before`/`after` bracket the live radar sample. A flag that appears
    BETWEEN them means the supply sagged while the radar was drawing, which
    is far more damning than a flag left over from boot.
    """
    value = after if after is not None else read_throttled()
    if value is None:
        report.add("power", WARN, "vcgencmd unavailable (not a Pi?)")
        return

    # Did anything newly latch during the sample? That is the supply failing
    # under exactly the load the session will put on it.
    if before is not None and after is not None and after != before:
        newly = [name for bit, name in {**THROTTLE_NOW, **THROTTLE_EVER}.items()
                 if (after & bit) and not (before & bit)]
        report.add("power", FAIL,
                   f"supply sagged DURING the radar sample: {', '.join(newly)} ({describe_throttled(after)})",
                   PSU_ADVICE)
        return

    if value & 0xF:
        report.add("power", FAIL, describe_throttled(value), PSU_ADVICE)
    elif value & 0xF0000:
        report.add("power", WARN,
                   f"{describe_throttled(value)} - since boot, but not right now",
                   "If this happened with the radar attached, the supply is too weak "
                   "for a session. " + PSU_ADVICE)
    else:
        report.add("power", OK, "no undervoltage or throttling (0x0)")


def check_disk(report: Report, hours: float) -> None:
    needed_mb = int(hours * 3600 * BYTES_PER_SECOND / (1024 * 1024)) + DISK_HEADROOM_MB
    try:
        free_mb = shutil.disk_usage("/").free // (1024 * 1024)
    except OSError as e:
        report.add("disk", WARN, f"could not check: {e}")
        return
    detail = f"{free_mb}MB free, ~{needed_mb}MB needed for {hours:.1f}h"
    if free_mb >= needed_mb:
        report.add("disk", OK, detail)
    else:
        report.add("disk", FAIL, detail,
                   "Delete old recordings on the Pi (rsync them off first)")


def check_migrations(report: Report) -> None:
    db = Path(__file__).resolve().parent.parent / "db" / "cricket.db"
    if not db.exists():
        report.add("database", WARN, "db/cricket.db does not exist yet",
                   "it is created on first use; nothing is stored server-side today")
        return
    try:
        import sqlite3
        conn = sqlite3.connect(db)
        applied = [r[0] for r in conn.execute("SELECT name FROM _migrations ORDER BY name")]
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='players'").fetchone()
        conn.close()
    except Exception as e:
        report.add("database", WARN, f"could not read: {e}")
        return
    if len(applied) >= 4:
        report.add("database", OK, f"{len(applied)} migrations applied")
    else:
        report.add("database", FAIL, f"only {len(applied)} migrations applied",
                   "python3 -m db.migrate")
    if schema and "created_at TEXT DEFAULT ''" in schema[0]:
        report.add("database schema", WARN, "players was upgraded from the legacy schema",
                   "rows created since carry empty created_at; delete_profile may raise")


def capture_live(seconds: float) -> tuple[list[dict], bool]:
    """Subscribe to the radar for `seconds` and return (frames, is_mock)."""
    from radar.reader import get_radar_source

    source = get_radar_source()
    frames: list[dict] = []
    start = time.time()

    def on_frame(frame) -> None:
        frames.append({
            "t_ms": int((time.time() - start) * 1000),
            "num_points": frame.num_points,
            "points": [{"doppler": p.doppler, "snr": p.snr} for p in frame.points],
        })

    source.ensure_running()
    source.wait_until_ready(timeout=3.0)
    is_mock = source.is_mock
    source.subscribe(on_frame)
    try:
        time.sleep(seconds)
    finally:
        source.unsubscribe(on_frame)
    return frames, is_mock


def load_recording(path: Path) -> tuple[list[dict], bool]:
    frames, is_mock = [], False
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("type") == "meta":
            is_mock = bool(o.get("mock"))
        elif o.get("type") == "frame":
            frames.append(o)
    return frames, is_mock


def check_capture(report: Report, frames: list[dict], is_mock: bool,
                  profile: Optional[RadarProfile], source: str) -> dict:
    if is_mock:
        report.add("radar data", FAIL, f"{source}: MOCK - the radar was not detected",
                   "STOP. Every frame is fabricated. Fix the radar before recording anything: "
                   "check the data cable, the powered hub and `vcgencmd get_throttled`")
    stats = analyse_capture(frames, profile)
    if not frames:
        report.add("frames", FAIL, f"{source}: no frames at all",
                   "the reader is not producing data")
        return stats
    if is_mock:
        # Fabricated frames must never produce a conclusion about the
        # hardware - that is how 19 worthless recordings came to look fine.
        stats["vmax_verdict"] = "MOCK"
        stats["vmax_detail"] = "fabricated data - tells you nothing about the radar"
        report.add("frame rate", WARN, "not measured: the data is mock")
        report.add("extendedMaxVelocity", WARN, "not assessed: the data is mock")
        return stats
    report.add("radar data", OK, f"{source}: REAL (mock=false)")

    rate = stats["frame_rate_hz"]
    if rate is None:
        report.add("frame rate", WARN, "too few frames to measure")
    elif rate >= 15:
        report.add("frame rate", OK, f"{rate} Hz over {stats['span_s']}s")
    else:
        report.add("frame rate", WARN, f"{rate} Hz (expected ~20)",
                   "20Hz is the ceiling at 921600 baud; a lower rate means dropped frames")

    report.add("scene density", OK,
               f"{stats['points_per_frame_avg']} points/frame avg, {stats['points_per_frame_max']} max")

    verdict = stats.get("vmax_verdict")
    detail = stats.get("vmax_detail", "")
    if verdict == "CONFIRMED":
        report.add("extendedMaxVelocity", OK, detail)
    elif verdict == "ACTIVE (unexercised)":
        report.add("extendedMaxVelocity", OK, detail)
    elif verdict == "BASE MODE OK":
        report.add("doppler mode", OK, detail)
    elif verdict == "ARTEFACT PRESENT":
        report.add("doppler mode", FAIL, detail,
                   "Power-cycle the radar hardware (unplug/replug the USB). A service "
                   "restart reports success without applying a new chirp config")
    elif verdict == "NO BALL-BAND MOTION":
        report.add("doppler mode", FAIL, detail,
                   "Bowl or wave something through the beam during the sample. If the band "
                   "stays empty with real motion present, the velocity measurement is broken "
                   "and no detector setting will find a ball")
    elif verdict in ("SUSPECT", "NOT CONFIGURED"):
        report.add("extendedMaxVelocity", FAIL, detail,
                   "Bowl 10 balls during a --capture and re-check. If it stays SUSPECT, the "
                   "profile needs changing and a HARDWARE power-cycle (a service restart does "
                   "not apply a new chirp config)")
    else:
        report.add("extendedMaxVelocity", WARN, detail or "inconclusive")
    return stats


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--capture", type=float, default=10.0,
                    help="seconds of live radar to sample (bowl during it to test v_max)")
    ap.add_argument("--hours", type=float, default=2.0, help="planned session length, for the disk check")
    ap.add_argument("--file", type=Path, help="analyse this recording instead of capturing live")
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--no-services", action="store_true", help="skip the systemd/power/db checks")
    args = ap.parse_args()

    report = Report()
    profile = None
    try:
        profile = load_profile(args.profile)
        report.add("radar profile", OK, profile.summary())
    except Exception as e:
        report.add("radar profile", FAIL, f"could not read {args.profile}: {e}")

    if args.file:
        frames, is_mock = load_recording(args.file)
        stats = check_capture(report, frames, is_mock, profile, args.file.name)
    else:
        if not args.no_services:
            check_devices(report)
            check_services(report)
            check_disk(report, args.hours)
            check_migrations(report)
        print(f"Sampling the radar for {args.capture:.0f}s "
              f"(bowl a few balls now to test the velocity range)...", file=sys.stderr)
        # Bracket the sample so a supply that sags under the radar's draw is
        # caught in the act, not merely inferred from a boot-time flag.
        throttled_before = read_throttled()
        frames, is_mock = capture_live(args.capture)
        throttled_after = read_throttled()
        if not args.no_services:
            check_undervoltage(report, throttled_before, throttled_after)
        stats = check_capture(report, frames, is_mock, profile, "live")

    if args.json:
        print(json.dumps({
            "ready": not report.failed,
            "checks": [vars(c) for c in report.checks],
            "capture": stats,
        }, indent=1))
        return 1 if report.failed else 0

    print()
    print("=== CricketRadar pre-flight ===")
    print(report.render())
    print()
    if report.failed:
        print(f"NOT READY - {len(report.failed)} check(s) must be fixed:")
        for c in report.failed:
            print(f"  - {c.name}: {c.detail}")
        return 1
    if report.warned:
        print(f"READY, with {len(report.warned)} warning(s). Recording now is worthwhile.")
    else:
        print("READY. Record away.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
