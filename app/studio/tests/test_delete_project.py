"""Deleting projects and shots must actually remove rows and files."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-del-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "del_proj"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "删除项目", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "chain"},
        {"shot_id": "s03", "scene_id": "sc1", "cast": [], "prompt": "c", "transition": "cut"},
    ])
    yield


def _take(sid: str) -> Path:
    p = db.DATA_DIR / "projects" / PID / "takes" / f"{sid}_v1.mp4"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 2000)
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    service.record_take(PID, sid, str(p), shots[sid]["fingerprint"])
    return p


def test_delete_one_shot_keeps_others():
    f = _take("s02")
    r = client.delete(f"/api/projects/{PID}/shots/s02")
    assert r.status_code == 200
    assert r.json()["remaining"] == ["s01", "s03"]
    assert not f.exists(), "片段文件也要删"
    assert [s["shot_id"] for s in service.list_shots(PID)] == ["s01", "s03"]


def test_delete_shot_recomputes_chain_fingerprints():
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "chain"},
    ])
    service.delete_shot(PID, "s01")
    # s02 is now first and still says chain -> plan should block it
    p = service.plan(PID, draft=False)
    assert any("第一镜" in w["msg"] for w in p["blocking"]), p["blocking"]


def test_delete_project_removes_everything():
    _take("s01")
    root = db.DATA_DIR / "projects" / PID
    assert root.is_dir()
    r = client.delete(f"/api/projects/{PID}")
    assert r.status_code == 200
    assert service.get_project(PID) is None
    assert not root.exists()
    assert service.list_shots(PID) == []


def test_delete_missing_project_404():
    assert client.delete("/api/projects/nope").status_code == 404
    assert client.delete(f"/api/projects/{PID}/shots/nope").status_code == 404
