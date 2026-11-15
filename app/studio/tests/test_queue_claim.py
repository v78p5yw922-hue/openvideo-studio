"""Verify single-executor claim and orphan cleanup."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-claim-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, service  # noqa: E402
from studio.exec import runner  # noqa: E402

db.init_db()
PID = "claim_test"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "认领测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "cut"},
    ])
    yield


def test_second_tick_does_not_start_while_one_runs(monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    j1 = service.enqueue_job(PID, "render", {"shots": ["s01"]})
    j2 = service.enqueue_job(PID, "render", {"shots": ["s02"]})
    # pretend j1 is already running (claimed by another process)
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (j1["id"],))
    assert runner.tick() is None, "有任务在跑时不能再认领"
    assert service.get_job(j2["id"])["state"] == "queued"


def test_claim_is_atomic(monkeypatch):
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    service.enqueue_job(PID, "render", {"shots": ["s01"]})
    calls = {"n": 0}

    def fake_stream(cmd, job_id, shot_id):
        calls["n"] += 1
        out = Path(cmd[cmd.index("--out") + 1])
        (out / f"{int(shot_id[1:]):04d}.mp4").write_bytes(b"z" * 5000)
        return {"returncode": 0, "output": "[drama] OK 100.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    assert runner.tick() is not None
    assert calls["n"] == 1


def test_orphan_running_is_failed_on_startup():
    j = service.enqueue_job(PID, "render", {"shots": ["s01"]})
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state='running' WHERE id=?", (j["id"],))
    assert service.fail_orphan_running() == 1
    assert service.get_job(j["id"])["state"] == "failed"
