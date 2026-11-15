"""Cancellation must actually stop work, including a running render."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-cancel-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402
from studio.exec import runner  # noqa: E402

db.init_db()
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean(tmp_path):
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    pid = "cancel_test"
    service.create_project(pid, "取消测试", "")
    service.upsert_scene(pid, "sc1", name="A")
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "cut"},
    ])
    yield


def test_queued_job_can_be_cancelled():
    job = service.enqueue_job("cancel_test", "render", {"shots": ["s01"], "kind": "render"})
    r = client.post(f"/api/jobs/{job['id']}/cancel")
    assert r.status_code == 200
    assert service.get_job(job["id"])["state"] == "cancelled"


def test_running_job_is_killed_then_cancelled(monkeypatch):
    job = service.enqueue_job("cancel_test", "render", {"shots": ["s01"]})
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
    called = {}

    class FakeProc:
        def terminate(self): called["term"] = True
        def wait(self, timeout=None): return 0
        def kill(self): called["kill"] = True

    runner.RUNNING[job["id"]] = FakeProc()
    try:
        r = client.post(f"/api/jobs/{job['id']}/cancel")
        assert r.status_code == 200 and r.json()["killed"] is True
        assert called.get("term") is True
        assert service.get_job(job["id"])["state"] == "cancelled"
    finally:
        runner.RUNNING.pop(job["id"], None)


def test_clear_queue_stops_running_too(monkeypatch):
    j1 = service.enqueue_job("cancel_test", "render", {"shots": ["s01"]})
    j2 = service.enqueue_job("cancel_test", "render", {"shots": ["s02"]})
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (j1["id"],))

    class FakeProc:
        def terminate(self): pass
        def wait(self, timeout=None): return 0
        def kill(self): pass

    runner.RUNNING[j1["id"]] = FakeProc()
    try:
        r = client.post("/api/projects/cancel_test/clear-queue")
        body = r.json()
        assert body["cancelled"] == 2
        assert j1["id"] in body["killed"], "正在渲的也要停"
        assert service.get_job(j1["id"])["state"] == "cancelled"
        assert service.get_job(j2["id"])["state"] == "cancelled"
    finally:
        runner.RUNNING.pop(j1["id"], None)


def test_global_stop(monkeypatch):
    j = service.enqueue_job("cancel_test", "render", {"shots": ["s01"]})
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (j["id"],))

    class FakeProc:
        def terminate(self): pass
        def wait(self, timeout=None): return 0
        def kill(self): pass

    runner.RUNNING[j["id"]] = FakeProc()
    try:
        r = client.post("/api/stop")
        assert r.status_code == 200
        assert r.json()["cancelled"] >= 1
        assert service.get_job(j["id"])["state"] == "cancelled"
    finally:
        runner.RUNNING.pop(j["id"], None)


def test_indicator_hidden_when_idle():
    q = client.get("/api/queue/global").json()
    assert q["count"] == 0
