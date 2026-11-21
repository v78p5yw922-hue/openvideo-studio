"""Chain linkage must be visible and must block a start without an upstream."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-link-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "link_test"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "接续测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "chain"},
        {"shot_id": "s03", "scene_id": "sc1", "cast": [], "prompt": "c", "transition": "chain"},
    ])
    yield


def _detail():
    return {s["shot_id"]: s for s in client.get(f"/api/projects/{PID}").json()["shots"]}


def test_detail_reports_chain_source_not_ready():
    d = _detail()
    assert d["s01"]["chain_from"] is None
    assert d["s02"]["chain_from"] == {"shot_id": "s01", "version": None, "ready": False}
    assert d["s03"]["chain_from"]["shot_id"] == "s02"


def test_detail_reports_ready_after_upstream_take():
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    service.record_take(PID, "s01", "/tmp/a.mp4", shots["s01"]["fingerprint"])
    d = _detail()
    assert d["s02"]["chain_from"]["ready"] is True
    assert d["s02"]["chain_from"]["version"] == 1
    assert d["s03"]["chain_from"]["ready"] is False, "s02 还没有片段"


def test_plan_blocks_chain_missing_upstream():
    p = client.post(f"/api/projects/{PID}/plan",
                    json={"shot_ids": ["s02"], "draft": False}).json()
    assert any("上游" in w["msg"] for w in p["blocking"]), p["blocking"]


def test_plan_allows_when_upstream_earlier_in_plan():
    p = client.post(f"/api/projects/{PID}/plan", json={"draft": False}).json()
    assert not [w for w in p["blocking"] if "上游" in w["msg"]], p["blocking"]
