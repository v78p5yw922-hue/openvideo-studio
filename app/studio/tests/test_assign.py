"""Drag-and-drop assignment: cast/scene binding must be atomic and auditable."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-assign-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, service  # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


@pytest.fixture
def project(tmp_path):
    pid = "assign_test"
    service.create_project(pid, "绑定测试", "")
    plate = tmp_path / "p.jpg"
    plate.write_bytes(b"plate")
    service.upsert_scene(pid, "scA", name="A", plate_path=str(plate))
    service.upsert_scene(pid, "scB", name="B")
    service.upsert_character("c1", "甲", "", [])
    service.upsert_character("c2", "乙", "", [])
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "scA", "cast": [], "prompt": "p",
         "transition": "cut", "anchor": "plate"},
        {"shot_id": "s02", "scene_id": "scA", "cast": [], "prompt": "p",
         "transition": "chain"},
    ])
    return pid


def test_drop_character_binds_and_clears_anchor(project):
    r = service.assign_shot(project, "s01", cast_add=["c1"])
    assert r["cast"] == ["c1"]
    assert r["anchor"] == "", "有角色就该清掉母版锚定"
    assert r["state"] == "empty"


def test_drop_scene_reanchors_empty_shot(project):
    r = service.assign_shot(project, "s01", scene_id="scA")
    assert r["scene_id"] == "scA" and r["anchor"] == "plate"
    assert r["scene_changed"] is False, "同场景不算变化"

    r2 = service.assign_shot(project, "s01", scene_id="scB")
    assert r2["scene_changed"] is True
    assert r2["anchor"] == "", "scB 没有母版，不能写 anchor"


def test_cast_limit_three(project):
    service.assign_shot(project, "s01", cast_add=["c1", "c2"])
    service.upsert_character("c3", "丙", "", [])
    service.upsert_character("c4", "丁", "", [])
    service.assign_shot(project, "s01", cast_add=["c3"])
    with pytest.raises(ValueError):
        service.assign_shot(project, "s01", cast_add=["c4"])


def test_remove_cast(project):
    service.assign_shot(project, "s01", cast_add=["c1"])
    r = service.assign_shot(project, "s01", cast_remove=["c1"])
    assert r["cast"] == []


def test_assignment_invalidates_existing_take(project):
    service.assign_shot(project, "s01", cast_add=["c1"])
    sh = next(s for s in service.list_shots(project) if s["shot_id"] == "s01")
    service.record_take(project, "s01", "/tmp/a.mp4", sh["fingerprint"])
    service.review_shot(project, "s01", "pass")
    assert service.shot_state(project, service.list_shots(project)[0])["state"] == "passed"

    service.assign_shot(project, "s01", cast_add=["c2"])
    assert service.shot_state(project, service.list_shots(project)[0])["state"] == "stale"


def test_http_assign_and_validation(project):
    from fastapi.testclient import TestClient
    from studio.api.app import app
    c = TestClient(app)
    r = c.post(f"/api/projects/{project}/shots/s02/assign", json={"cast_add": ["c1"]})
    assert r.status_code == 200 and r.json()["cast"] == ["c1"]
    assert r.json()["transition"] == "chain", "绑定角色不应改转场"
    bad = c.post(f"/api/projects/{project}/shots/nope/assign", json={"cast_add": ["c1"]})
    assert bad.status_code == 400
