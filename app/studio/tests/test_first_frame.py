"""Pinned first frame: override chain/anchor for a single shot."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-ff-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402
from studio.exec import runner  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "ff_test"


@pytest.fixture(autouse=True)
def clean(tmp_path):
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "首帧测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "chain"},
    ])
    yield


def _shots():
    return {s["shot_id"]: s for s in service.list_shots(PID)}


def test_pin_changes_fingerprint_and_clears_anchor(tmp_path):
    before = _shots()["s02"]["fingerprint"]
    img = tmp_path / "ff.jpg"
    img.write_bytes(b"\xff\xd8" + b"x" * 200)
    r = service.set_first_frame(PID, "s02", str(img))
    assert r["first_frame"] == str(img)
    assert r["anchor"] == ""
    assert _shots()["s02"]["fingerprint"] != before


def test_pinned_shot_no_longer_blocked_by_missing_upstream(tmp_path):
    p = client.post(f"/api/projects/{PID}/plan",
                    json={"shot_ids": ["s02"], "draft": False}).json()
    assert any("上游" in w["msg"] for w in p["blocking"])

    img = tmp_path / "ff.jpg"
    img.write_bytes(b"\xff\xd8" + b"y" * 200)
    service.set_first_frame(PID, "s02", str(img))
    p2 = client.post(f"/api/projects/{PID}/plan",
                     json={"shot_ids": ["s02"], "draft": False}).json()
    assert not [w for w in p2["blocking"] if "上游" in w["msg"]], p2["blocking"]


def test_executor_passes_first_frame_flag(tmp_path, monkeypatch):
    img = tmp_path / "ff.jpg"
    img.write_bytes(b"\xff\xd8" + b"z" * 200)
    service.set_first_frame(PID, "s02", str(img))
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")

    captured = {}

    def fake_stream(cmd, job_id, shot_id):
        captured["cmd"] = cmd
        out = Path(cmd[cmd.index("--out") + 1])
        (out / "0002.mp4").write_bytes(b"x" * 4096)
        return {"returncode": 0, "output": "[drama] [02] OK 100.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(PID, "render", {"shots": ["s02"], "draft": False})
    runner.tick()
    cmd = captured["cmd"]
    assert "--first-frame" in cmd
    assert cmd[cmd.index("--first-frame") + 1] == str(img)


def test_clear_restores_chain_behaviour(tmp_path):
    img = tmp_path / "ff.jpg"
    img.write_bytes(b"\xff\xd8" + b"w" * 200)
    service.set_first_frame(PID, "s02", str(img))
    service.clear_first_frame(PID, "s02")
    assert _shots()["s02"]["first_frame"] == ""
    d = client.get(f"/api/projects/{PID}").json()
    s02 = next(s for s in d["shots"] if s["shot_id"] == "s02")
    assert s02["chain_from"]["shot_id"] == "s01"
