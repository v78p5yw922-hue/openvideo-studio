"""Reorder: presentation changes, pixels must not."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-order-")
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
    pid = "order_test"
    service.create_project(pid, "排序测试", "")
    plate = tmp_path / "p.jpg"
    plate.write_bytes(b"plate")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))
    service.upsert_character("c1", "甲", "", [])
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": ["c1"], "prompt": "b", "transition": "cut"},
        {"shot_id": "s03", "scene_id": "sc1", "cast": ["c1"], "prompt": "c", "transition": "chain"},
    ])
    return pid


def test_move_before(project):
    r = service.move_shot(project, "s03", before_shot_id="s01")
    assert r["order"] == ["s03", "s01", "s02"]


def test_move_to_end(project):
    r = service.move_shot(project, "s01", to_end=True)
    assert r["order"] == ["s02", "s03", "s01"]


def test_reorder_keeps_cut_fingerprints(project):
    """Reordering is presentation-only for cut shots; a chain shot legitimately
    changes because its upstream changed."""
    before = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(project)}
    service.move_shot(project, "s03", before_shot_id="s01")
    after = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(project)}
    assert before["s01"] == after["s01"], "cut 镜头换顺序不该失效"
    assert before["s02"] == after["s02"]
    assert before["s03"] != after["s03"], "chain 换了上游就该失效（这是对的）"


def test_chain_first_warns(project):
    r = service.move_shot(project, "s03", before_shot_id="s01")
    assert any(w["level"] == "error" and "第一位" in w["msg"] for w in r["warnings"])


def test_chain_upstream_change_warns(project):
    # s02 is cut, s03 is chain(c1). Move s01 between them -> s03's upstream becomes s01 (no cast)
    r = service.move_shot(project, "s01", before_shot_id="s03")
    assert r["order"] == ["s02", "s01", "s03"]
    assert any("角色集不同" in w["msg"] for w in r["warnings"])


def test_http_move(project):
    from fastapi.testclient import TestClient
    from studio.api.app import app
    c = TestClient(app)
    r = c.post(f"/api/projects/{project}/shots/s02/move", json={"before": "s01"})
    assert r.status_code == 200
    assert r.json()["order"] == ["s02", "s01", "s03"]
    bad = c.post(f"/api/projects/{project}/shots/nope/move", json={"before": "s01"})
    assert bad.status_code == 400
