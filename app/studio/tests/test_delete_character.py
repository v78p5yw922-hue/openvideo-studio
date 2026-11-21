"""Delete characters; refuse while still cast unless forced."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-delchar-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "del_test"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "删除测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": ["ghost"], "prompt": "a", "transition": "cut"},
    ])
    yield


def _ref(name: str) -> Path:
    d = db.DATA_DIR / "characters"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\xff\xd8" + b"x" * 40)
    return p


def test_delete_unused_character():
    service.upsert_character("spare", "多余角色", "", [str(_ref("s.jpg"))])
    r = client.delete("/api/characters/spare")
    assert r.status_code == 200
    assert not any(c["character_id"] == "spare" for c in service.list_characters())


def test_refuses_when_cast_unless_forced():
    service.upsert_character("ghost", "路人", "", [str(_ref("g.jpg"))])
    r = client.delete("/api/characters/ghost")
    assert r.status_code == 400
    assert "在用" in r.json()["detail"]

    r2 = client.delete("/api/characters/ghost", params={"force": 1})
    assert r2.status_code == 200
    shots = service.list_shots(PID)
    assert shots[0]["cast"] == [], "强制删除要把角色从镜头里摘掉"


def test_ref_files_removed():
    p = _ref("h.jpg")
    service.upsert_character("tmpc", "临时", "", [str(p)])
    client.delete("/api/characters/tmpc")
    assert not p.exists()
