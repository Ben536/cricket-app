"""
The pre-flight capture analysis - in particular the extendedMaxVelocity
verdict, which is the project's highest-stakes open question and is decided
by this function alone.

Base unambiguous velocity for profile_cricket.cfg is 13.0 m/s; with
extendedMaxVelocity and 3 TX it is 39.0 m/s. In BASE mode the radar
physically cannot report more than 13.0, so any larger value proves extended
mode is active. Static targets misassigned by one ambiguity interval land at
exactly 2x base (25.9 m/s) - the artefact visible in the project's one real
recording.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from preflight import analyse_capture, load_recording  # noqa: E402

from radar.profile_cfg import load_profile, parse_profile  # noqa: E402

PROFILE = load_profile()
BASE = PROFILE.v_max_base_ms  # ~13.0 m/s


def frames_with(dopplers, n_frames=20, period_ms=50):
    """One point per frame, cycling through `dopplers`."""
    return [
        {"t_ms": i * period_ms, "num_points": 1,
         "points": [{"doppler": dopplers[i % len(dopplers)], "snr": 15.0}]}
        for i in range(n_frames)
    ]


def test_profile_limits_are_what_the_verdict_rests_on():
    assert 12.5 < BASE < 13.5
    assert PROFILE.extended_max_velocity is True
    assert 37.5 < PROFILE.v_max_ms < 40.5


def test_slow_scene_only_is_suspect():
    """Nothing above the base limit: if there WAS fast motion, extended mode
    is not engaging - and no cricket shot could ever be measured."""
    stats = analyse_capture(frames_with([0.5, 2.0, 8.0, 12.0]), PROFILE)
    assert stats["vmax_verdict"] == "SUSPECT"
    assert stats["points_above_base_limit"] == 0


def test_static_misassignment_artefact_shows_extended_mode_is_on():
    """The real 2026-07 recording: an empty room whose ghosts sit at 2x base."""
    stats = analyse_capture(frames_with([2 * BASE, 2 * BASE + 0.8, 0.0]), PROFILE)
    assert stats["vmax_verdict"] == "ACTIVE (unexercised)"
    assert stats["points_near_2x_base"] > 0


def test_real_fast_motion_confirms_the_full_range():
    stats = analyse_capture(frames_with([30.0, 34.0, 2.0]), PROFILE)
    assert stats["vmax_verdict"] == "CONFIRMED"
    assert stats["points_above_artefact_band"] > 0


def test_profile_without_extended_velocity_is_reported_as_misconfigured():
    text = Path(PROFILE and str(load_profile.__globals__["DEFAULT_PROFILE_PATH"])).read_text()
    off = parse_profile(text.replace("extendedMaxVelocity -1 1", "extendedMaxVelocity -1 0"))
    stats = analyse_capture(frames_with([30.0]), off)
    assert stats["vmax_verdict"] == "NOT CONFIGURED"


def test_rate_and_density_are_measured():
    stats = analyse_capture(frames_with([5.0], n_frames=21, period_ms=50), PROFILE)
    assert stats["frame_rate_hz"] == 20.0
    assert stats["points_per_frame_avg"] == 1.0
    assert stats["frames"] == 21


def test_empty_and_garbage_input_do_not_raise():
    assert analyse_capture([], PROFILE)["vmax_verdict"] == "UNKNOWN"
    junk = [{"t_ms": 0, "num_points": 2, "points": [
        {"doppler": float("nan")}, {"doppler": 1e30}]}]
    stats = analyse_capture(junk, PROFILE)
    assert stats["points"] == 0
    assert analyse_capture(frames_with([5.0]), None)["vmax_verdict"] == "UNKNOWN"


def test_mock_data_never_yields_a_hardware_verdict():
    """19 of the 20 recordings this project holds are mock. Fabricated
    frames must produce no conclusion about the radar at all."""
    from preflight import Report, check_capture

    report = Report()
    stats = check_capture(report, frames_with([30.0, 34.0]), True, PROFILE, "mock.jsonl")
    assert stats["vmax_verdict"] == "MOCK"
    assert any(c.status == "FAIL" and "MOCK" in c.detail for c in report.checks)
    assert not any(c.name == "extendedMaxVelocity" and c.status == "PASS" for c in report.checks)


def test_the_one_real_recording_reads_as_extended_mode_active():
    """The project's only non-mock capture (an empty room, 2026-07-03):
    88% of its points sit at 2x the base limit. That is only possible in
    extended mode - so extendedMaxVelocity WAS engaging that day."""
    path = Path(__file__).resolve().parent.parent / "recordings" / "bowling" / "2026-07-03_09-09-36.jsonl"
    if not path.exists():
        pytest.skip("recordings/ is gitignored; only present on the capture machine")
    frames, is_mock = load_recording(path)
    assert is_mock is False
    stats = analyse_capture(frames, PROFILE)
    assert stats["points_above_base_limit"] > 0
    assert stats["vmax_verdict"] in ("ACTIVE (unexercised)", "CONFIRMED")
