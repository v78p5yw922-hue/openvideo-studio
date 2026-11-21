"""Validate that our exported OTIO really loads in the official library.

A hand-written JSON that "looks right" is worthless if DaVinci rejects it, so we
round-trip through opentimelineio itself.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-otio-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

otio = pytest.importorskip("opentimelineio")

from studio.core import db, service  # noqa: E402
from studio.export import timeline  # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _project(pid="otio_test"):
    service.create_project(pid, "OTIO 测试", "")
    service.upsert_scene(pid, "sc1", name="A")
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a",
         "transition": "cut", "duration": 5.0},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b",
         "transition": "chain", "duration": 5.0},
    ])
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    for sid in ("s01", "s02"):
        service.record_take(pid, sid, f"/tmp/{sid}.mp4", shots[sid]["fingerprint"])
        service.review_shot(pid, sid, "pass")
    return pid


def test_exported_otio_loads_in_library(tmp_path):
    pid = _project()
    data = timeline.build_otio(pid)
    f = tmp_path / f"{pid}.otio"
    f.write_text(json.dumps(data), encoding="utf-8")

    tl = otio.adapters.read_from_file(str(f))
    assert isinstance(tl, otio.schema.Timeline)
    assert tl.name == "OTIO 测试"
    track = tl.tracks[0]
    assert track.kind == otio.schema.TrackKind.Video
    assert len(track) == 2
    assert track[0].name == "s01_v1"
    assert track[0].duration().to_seconds() == pytest.approx(5.0)
    # total duration must equal the sum of clips
    assert tl.duration().to_seconds() == pytest.approx(10.0, abs=0.1)


def test_otio_clips_are_contiguous(tmp_path):
    pid = _project()
    data = timeline.build_otio(pid)
    f = tmp_path / "cut.otio"
    f.write_text(json.dumps(data), encoding="utf-8")
    tl = otio.adapters.read_from_file(str(f))
    cursor = 0.0
    for clip in tl.tracks[0]:
        assert clip.range_in_parent().start_time.to_seconds() == pytest.approx(cursor, abs=0.01)
        cursor += clip.duration().to_seconds()


def test_edl_is_cmx3600_shaped(tmp_path):
    """No cmx3600 adapter ships with otio, so assert the format itself."""
    pid = _project()
    edl = timeline.build_edl(pid)["edl"]
    assert edl.startswith("TITLE:")
    assert "FCM: NON-DROP FRAME" in edl
    events = [ln for ln in edl.splitlines() if ln[:3].isdigit()]
    assert len(events) == 2
    for ev in events:
        parts = ev.split()
        assert parts[3] == "C", "事件类型必须是 C"
        assert len(parts[4:8]) == 4, "要有 源入/源出/录入/录出 四个时间码"
        assert all(":" in tc for tc in parts[4:8])
