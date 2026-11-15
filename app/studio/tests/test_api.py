"""API smoke tests: plan -> confirm, and the guard rails around them."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-api-")
os.environ["STUDIO_DATA"] = TMP
os.environ["STUDIO_ALLOW_RENDER"] = "0"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402

client = TestClient(app)

SHOTS = [
    {"shot_id": "s01", "scene_id": "sc1", "cast": [], "camera": "medium",
     "prompt": "a", "dialogue": "", "transition": "cut", "anchor": "plate"},
    {"shot_id": "s02", "scene_id": "sc1", "cast": ["c1"], "camera": "close",
     "prompt": "b", "dialogue": "<d>甲：走。</d>", "transition": "chain"},
]


@pytest.fixture(autouse=True)
def setup(tmp_path):
    from studio.core import db as _db
    with _db.txn() as conn:                      # isolate tests: no leftover jobs/takes
        for t in ("takes", "reviews", "previews", "jobs", "events"):
            conn.execute(f"DELETE FROM {t}")
    pid = "api_test"
    client.post("/api/projects", json={"id": pid, "title": "API 测试"})
    plate = tmp_path / "p.jpg"
    plate.write_bytes(b"plate")
    client.post(f"/api/projects/{pid}/scenes",
                json={"scene_id": "sc1", "name": "A", "plate_path": str(plate)})
    client.post("/api/characters",
                json={"character_id": "c1", "name": "甲", "refs": []})
    client.put(f"/api/projects/{pid}/shots", json={"shots": SHOTS})
    yield


def test_health_reports_executor_off():
    r = client.get("/api/health").json()
    assert r["ok"] and r["executor"] is False


def test_project_detail_has_states():
    d = client.get("/api/projects/api_test").json()
    assert d["counts"]["total"] == 2
    assert {s["state"] for s in d["shots"]} == {"empty"}


def test_plan_then_confirm_queues_job():
    p = client.post("/api/projects/api_test/plan", json={"shot_ids": None}).json()
    assert p["count"] == 2
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": p["plan_id"], "draft": False})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["executor"] is False
    assert body["job"]["state"] == "queued"


def test_confirm_accepts_subset_plan():
    """Plan for a subset must be confirmable (regression: confirm used to
    recompute the full plan and always 409)."""
    p = client.post("/api/projects/api_test/plan",
                    json={"shot_ids": ["s01"], "draft": True}).json()
    assert p["count"] == 1
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": p["plan_id"], "draft": True})
    assert r.status_code == 200, r.text
    assert json.loads(r.json()["job"]["payload"])["shots"] == ["s01"]


def test_confirm_rejects_unknown_plan_id():
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": "does-not-exist", "draft": True})
    assert r.status_code == 409


def test_confirm_rejects_stale_plan():
    p = client.post("/api/projects/api_test/plan", json={}).json()
    changed = [dict(s) for s in SHOTS]
    changed[0]["prompt"] = "改了"
    client.put("/api/projects/api_test/shots", json={"shots": changed})
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": p["plan_id"], "draft": False})
    assert r.status_code == 409, "输入变了，旧计划必须被拒绝"


def test_confirm_blocks_when_scene_missing_plate():
    client.post("/api/projects/api_test/scenes", json={"scene_id": "sc2", "name": "空"})
    broken = [dict(SHOTS[0], scene_id="sc2")]
    client.put("/api/projects/api_test/shots", json={"shots": broken})
    p = client.post("/api/projects/api_test/plan", json={}).json()
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": p["plan_id"], "draft": False})
    assert r.status_code == 400
    assert "母版" in r.json()["detail"]


def test_review_requires_take():
    r = client.post("/api/projects/api_test/review",
                    json={"shot_id": "s01", "verdict": "pass"})
    assert r.status_code == 400


def test_plan_reports_worst_case():
    p = client.post("/api/projects/api_test/plan", json={}).json()
    assert p["worst_minutes"] == pytest.approx(p["est_minutes"] * 3, rel=0.01)


def test_confirm_queues_one_job_per_shot():
    p = client.post("/api/projects/api_test/plan", json={"draft": True}).json()
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": p["plan_id"], "draft": True})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == p["count"] == 2
    shots = [json.loads(j["payload"])["shots"] for j in body["jobs"]]
    assert shots == [["s01"], ["s02"]], "一镜一任务，且保持顺序"


def test_selftest_passes():
    r = client.get("/api/selftest").json()
    assert r["passed"] == r["total"], [c for c in r["checks"] if not c["ok"]]


def test_review_queue_only_pending():
    # no takes yet -> empty queue
    q = client.get("/api/projects/api_test/review-queue").json()
    assert q["total"] == 0

    from studio.core import service
    shots = service.list_shots("api_test")
    service.record_take("api_test", "s01", "/tmp/a.mp4", shots[0]["fingerprint"])
    q = client.get("/api/projects/api_test/review-queue").json()
    assert q["total"] == 1 and q["shots"][0]["shot_id"] == "s01"

    service.review_shot("api_test", "s01", "pass")
    q = client.get("/api/projects/api_test/review-queue").json()
    assert q["total"] == 0, "通过后应离开待审队列"


def test_cancel_job_returns_shot_to_empty():
    p = client.post("/api/projects/api_test/plan", json={"draft": True}).json()
    r = client.post("/api/projects/api_test/confirm",
                    json={"plan_id": p["plan_id"], "draft": True})
    jid = r.json()["job"]["id"]
    detail = client.get("/api/projects/api_test").json()
    assert detail["shots"][0]["state"] == "queued"

    c = client.post(f"/api/jobs/{jid}/cancel")
    assert c.status_code == 200
    detail = client.get("/api/projects/api_test").json()
    assert detail["shots"][0]["state"] == "empty", "取消后应回到未生成"


def test_clear_queue_cancels_all(  ):
    p = client.post("/api/projects/api_test/plan", json={"draft": True}).json()
    client.post("/api/projects/api_test/confirm", json={"plan_id": p["plan_id"], "draft": True})
    r = client.post("/api/projects/api_test/clear-queue").json()
    assert r["cancelled"] >= 1
    assert client.get("/api/projects/api_test/review-queue").json()["total"] == 0
