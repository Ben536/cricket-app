"""
Listing recordings and reading one back.

The phone needs a log of what was captured and the labels for each session,
so a nets trip can be reviewed on the spot. Two things must hold:

  - frames are NEVER returned (a 2h gathering session holds ~1.25GB of them;
    the labels are a few KB)
  - the file path comes from the network, so it must be resolved inside
    recordings/ or refused
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from radar.recorder import RadarRecorder
from radar.reader import RadarSource


@pytest.fixture
def recorder(tmp_path):
    return RadarRecorder(recordings_dir=str(tmp_path), source=RadarSource("/dev/nonexistent"))


def write_session(recorder, session_type="both", name="2026-09-06_10-00-00.jsonl",
                  marks=(), frames=30, end=True, mock=False):
    path = recorder.recordings_dir / session_type / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps({"type": "meta", "session_type": session_type,
                            "start_time": "2026-09-06T10:00:00+00:00", "mock": mock,
                            "max_duration_seconds": 300}) + "\n")
        for i in range(frames):
            f.write(json.dumps({"type": "frame", "t_ms": i * 50, "frame_number": i,
                                "cpu_time_ms": i * 50, "num_points": 2,
                                "points": [{"x": 1.0, "y": 2.0, "z": 0.5, "doppler": 20.0,
                                            "snr": 15.0, "noise": 5.0}] * 2}) + "\n")
        for j, m in enumerate(marks):
            f.write(json.dumps({"type": "annotation", "t_ms": 1000 + j * 500, **m}) + "\n")
        if end:
            f.write(json.dumps({"type": "end", "end_time": "2026-09-06T10:05:00+00:00",
                                "duration_seconds": 300.0, "frame_count": frames,
                                "annotation_count": len(marks)}) + "\n")
    return path


# ---------------------------------------------------------------------------
# read_annotations
# ---------------------------------------------------------------------------

def test_labels_come_back_without_any_frames(recorder):
    marks = [{"direction_deg": 35.0, "distance_norm": 0.9, "outcome": "4"},
             {"direction_deg": -70.5, "distance_norm": 0.4, "outcome": "1"}]
    write_session(recorder, marks=marks)
    out = recorder.read_annotations("both/2026-09-06_10-00-00.jsonl")

    assert out["session_type"] == "both"
    assert out["frame_count"] == 30
    assert out["annotation_count"] == 2
    assert out["incomplete"] is False
    assert out["mock"] is False
    assert [a["direction_deg"] for a in out["annotations"]] == [35.0, -70.5]
    assert [a["outcome"] for a in out["annotations"]] == ["4", "1"]
    # The whole point: no frame data anywhere in the reply
    blob = json.dumps(out)
    assert "points" not in blob and "doppler" not in blob


def test_absolute_path_from_the_listing_also_works(recorder):
    path = write_session(recorder, marks=[{"direction_deg": 1.0}])
    out = recorder.read_annotations(str(path))
    assert out["annotation_count"] == 1


def test_a_crashed_recording_is_flagged_and_still_readable(recorder):
    write_session(recorder, marks=[{"direction_deg": 10.0}], end=False)
    out = recorder.read_annotations("both/2026-09-06_10-00-00.jsonl")
    assert out["incomplete"] is True
    assert out["frame_count"] == 30          # recovered by scanning
    assert out["annotation_count"] == 1


def test_mock_recordings_are_flagged(recorder):
    write_session(recorder, mock=True)
    assert recorder.read_annotations("both/2026-09-06_10-00-00.jsonl")["mock"] is True


def test_a_truncated_final_line_does_not_break_the_read(recorder):
    path = write_session(recorder, marks=[{"direction_deg": 5.0}])
    with open(path, "a") as f:
        f.write('{"type": "annotation", "trunc')
    out = recorder.read_annotations("both/2026-09-06_10-00-00.jsonl")
    assert out["annotation_count"] == 1


def test_the_reply_is_bounded(recorder, monkeypatch):
    monkeypatch.setattr(RadarRecorder, "MAX_ANNOTATIONS_RETURNED", 5)
    write_session(recorder, marks=[{"direction_deg": float(i)} for i in range(20)])
    out = recorder.read_annotations("both/2026-09-06_10-00-00.jsonl")
    assert len(out["annotations"]) == 5
    assert out["annotations_truncated"] is True
    assert out["annotation_count"] == 20     # the true total is still reported


@pytest.mark.parametrize("bad", [
    "../../../etc/passwd",
    "both/../../../etc/passwd",
    "/etc/passwd",
    "both/nope.jsonl",
    "",
])
def test_paths_outside_recordings_are_refused(recorder, bad):
    write_session(recorder)
    with pytest.raises(ValueError):
        recorder.read_annotations(bad)


def test_a_symlink_out_of_the_directory_is_refused(recorder, tmp_path):
    write_session(recorder)
    secret = tmp_path.parent / "secret.jsonl"
    secret.write_text('{"type":"meta"}\n')
    link = recorder.recordings_dir / "both" / "sneaky.jsonl"
    link.symlink_to(secret)
    with pytest.raises(ValueError):
        recorder.read_annotations("both/sneaky.jsonl")


def test_non_recording_files_are_refused(recorder):
    other = recorder.recordings_dir / "both" / "notes.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("hello")
    with pytest.raises(ValueError):
        recorder.read_annotations("both/notes.txt")


# ---------------------------------------------------------------------------
# list_recordings
# ---------------------------------------------------------------------------

def test_listing_summarises_every_type(recorder):
    write_session(recorder, "both", "2026-09-06_10-00-00.jsonl", marks=[{"direction_deg": 1.0}])
    write_session(recorder, "bowling", "2026-09-06_09-00-00.jsonl", frames=10)
    listed = recorder.list_recordings()
    by_type = {r["session_type"]: r for r in listed}
    assert set(by_type) == {"both", "bowling"}
    assert by_type["both"]["annotation_count"] == 1
    assert by_type["bowling"]["frame_count"] == 10
    assert all("annotations" not in r for r in listed), "the listing must stay light"


def test_listing_can_filter_by_type(recorder):
    write_session(recorder, "both", "a.jsonl")
    write_session(recorder, "racket", "b.jsonl")
    assert {r["session_type"] for r in recorder.list_recordings("racket")} == {"racket"}
