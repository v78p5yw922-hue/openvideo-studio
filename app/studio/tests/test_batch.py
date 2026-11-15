"""Batch edit: apply one change to several shots without a per-shot JSON edit."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-batch-")
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
    pid = "batch_test"
    service.create_project(pid, "批量测试", "")
    plate = tmp_path / "p.jpg"
    plate.write_bytes(b"plate")
    service.upsert_scene(pid, "scA", name="A", plate_path=str(plate))
    service.upsert_scene(pid, "scB", name="B", plate_path=str(plate))
    service.upsert_character("c1", "甲", "", [])
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "scA", "cast": [], "prompt": "p1", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "scA", "cast": [], "prompt": "p2", "transition": "cut"},
        {"shot_id": "s03", "scene_id": "scA", "cast": [], "prompt": "p3", "transition": "cut"},
    ])
    return pid


def test_batch_scene(project):
    service.batch_update(project, ["s01", "s03"], scene_id="scB")
    shots = {s["shot_id"]: s for s in service.list_shots(project)}
    assert shots["s01"]["scene_id"] == "scB"
    assert shots["s03"]["scene_id"] == "scB"
    assert shots["s02"]["scene_id"] == "scA", "未选中的不能变"


def test_batch_transition_and_stale(project):
    shots = service.list_shots(project)
    service.record_take(project, "s02", "/tmp/a.mp4", shots[1]["fingerprint"])
    service.review_shot(project, "s02", "pass")
    service.batch_update(project, ["s02"], transition="chain")
    changed = next(s for s in service.list_shots(project) if s["shot_id"] == "s02")
    assert changed["transition"] == "chain"
    assert service.shot_state(project, changed)["state"] == "stale"


def test_batch_cast_limit_rolls_back(project):
    service.upsert_character("c2", "乙", "", [])
    service.upsert_character("c3", "丙", "", [])
    service.upsert_character("c4", "丁", "", [])
    service.batch_update(project, ["s01"], cast_add=["c1", "c2", "c3"])
    with pytest.raises(ValueError):
        service.batch_update(project, ["s01"], cast_add=["c4"])
    assert len(next(s for s in service.list_shots(project)
                   if s["shot_id"] == "s01")["cast"]) == 3


def test_batch_rejects_unknown_shot(project):
    with pytest.raises(ValueError):
        service.batch_update(project, ["s01", "nope"], transition="cut")


def test_batch_http(project):
    from fastapi.testclient import TestClient
    from studio.api.app import app
    c = TestClient(app)
    r = c.post(f"/api/projects/{project}/shots/batch",
               json={"shot_ids": ["s01", "s02"], "set": {"scene_id": "scB"}})
    assert r.status_code == 200 and r.json()["count"] == 2
    bad = c.post(f"/api/projects/{project}/shots/batch", json={"shot_ids": [], "set": {}})
    assert bad.status_code == 400
