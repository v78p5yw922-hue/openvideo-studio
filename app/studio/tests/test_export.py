"""Timeline export tests: EDL/OTIO must reference shot_id takes and sum correctly."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-export-")
os.environ["STUDIO_DATA"] = TMP
os.environ["STUDIO_ALLOW_RENDER"] = "0"
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, service  # noqa: E402
from studio.export import timeline  # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _project(pid="export_test", approved=(0, 1)):
    service.create_project(pid, "导出测试", "")
    service.upsert_scene(pid, "sc1", name="A")
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a",
         "transition": "cut", "duration": 5.0},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b",
         "transition": "cut", "duration": 5.0},
        {"shot_id": "s03", "scene_id": "sc1", "cast": [], "prompt": "c",
         "transition": "cut", "duration": 5.0},
    ])
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    for i in approved:
        sid = f"s{i + 1:02d}"
        service.record_take(pid, sid, f"/tmp/{sid}.mp4", shots[sid]["fingerprint"], 500, 24000)
        service.review_shot(pid, sid, "pass")
    return pid


def test_approved_cut_only_includes_passed():
    pid = _project(approved=(0, 2))
    cut = timeline.approved_cut(pid)
    assert [c["shot_id"] for c in cut] == ["s01", "s03"]


def test_edl_has_one_event_per_clip_and_sums():
    pid = _project(approved=(0, 1))
    d = timeline.build_edl(pid)
    assert d["clips"] == 2
    assert d["frames"] == 240, "两镜各 120 帧（5s @24）"
    assert d["duration_s"] == pytest.approx(10.0, abs=0.1)
    assert "TITLE: 导出测试" in d["edl"]
    assert "001  AX       V     C        00:00:00:00 00:00:05:00 00:00:00:00 00:00:05:00" in d["edl"]
    assert "002  AX       V     C        00:00:00:00 00:00:05:00 00:00:05:00 00:00:10:00" in d["edl"]
    assert "* FROM CLIP NAME: s01_v1" in d["edl"]
    assert [m["shot_id"] for m in d["missing"]] == ["s03"]


def test_otio_structure():
    pid = _project(approved=(0, 1))
    t = timeline.build_otio(pid)
    assert t["OTIO_SCHEMA"] == "Timeline.1"
    track = t["tracks"]["children"][0]
    assert track["kind"] == "Video" and len(track["children"]) == 2
    clip = track["children"][0]
    assert clip["name"] == "s01_v1"
    assert clip["source_range"]["duration"]["rate"] == 24.0
    assert clip["active_media_reference_key"] == "DEFAULT_MEDIA"
    assert clip["media_references"]["DEFAULT_MEDIA"]["target_url"].endswith("s01.mp4")
    assert t["metadata"]["frames"] == 240


def test_export_endpoints():
    # exercise the HTTP layer too
    os.environ["STUDIO_DATA"] = TMP
    from fastapi.testclient import TestClient
    from studio.api.app import app
    pid = _project("http_export", approved=(0,))
    c = TestClient(app)
    r = c.get(f"/api/projects/{pid}/export/edl")
    assert r.status_code == 200 and "TITLE" in r.text
    r2 = c.get(f"/api/projects/{pid}/export/otio")
    assert r2.status_code == 200 and r2.json()["OTIO_SCHEMA"] == "Timeline.1"
