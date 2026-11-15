"""Asset lock + version archive: replacements must be blockable and reversible."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-lock-")
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


def _scene_with_plate(tmp_path, contents=b"v1"):
    pid = "lock_test"
    service.create_project(pid, "锁定测试", "")
    plate = tmp_path / "plate.jpg"
    plate.write_bytes(contents)
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))
    return pid, plate


def test_lock_blocks_replacement(tmp_path):
    pid, plate = _scene_with_plate(tmp_path)
    service.set_scene_lock(pid, "sc1", True)
    other = tmp_path / "v2.jpg"
    other.write_bytes(b"v2")
    with pytest.raises(service.LockedError):
        service.upsert_scene(pid, "sc1", name="A", plate_path=str(other))
    # unchanged
    assert service.get_scene(pid, "sc1")["plate_sha"] == service.sha256_file(plate)


def test_unlock_allows_and_archives(tmp_path):
    pid, plate = _scene_with_plate(tmp_path)
    service.set_scene_lock(pid, "sc1", True)
    service.set_scene_lock(pid, "sc1", False)
    other = tmp_path / "v2.jpg"
    other.write_bytes(b"v2")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(other))
    versions = service.list_versions("scene", "sc1")
    assert len(versions) == 1 and versions[0]["path"] == str(plate), "旧母版要进归档"


def test_restore_brings_back_old_plate(tmp_path):
    pid, plate = _scene_with_plate(tmp_path)
    other = tmp_path / "v2.jpg"
    other.write_bytes(b"v2")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(other))
    before = service.get_scene(pid, "sc1")
    service.restore_scene_plate(pid, "sc1", 1)
    after = service.get_scene(pid, "sc1")
    assert after["plate_sha"] == service.sha256_file(plate)
    assert after["plate_sha"] != before["plate_sha"]
    assert len(service.list_versions("scene", "sc1")) == 2, "恢复本身也产生一个版本"


def test_character_lock_and_archive(tmp_path):
    cid = "c1"
    a = tmp_path / "a.png"
    a.write_bytes(b"a")
    b = tmp_path / "b.png"
    b.write_bytes(b"b")
    service.upsert_character(cid, "甲", "desc", [str(a)])
    service.set_character_lock(cid, True)
    with pytest.raises(service.LockedError):
        service.upsert_character(cid, "甲", "desc", [str(b)])
    service.set_character_lock(cid, False)
    service.upsert_character(cid, "甲", "desc", [str(b)])
    v = service.list_versions("character", cid)
    assert len(v) == 1 and v[0]["path"] == str(a)


def test_http_lock_returns_409(tmp_path):
    from fastapi.testclient import TestClient
    from studio.api.app import app
    c = TestClient(app)
    pid, plate = _scene_with_plate(tmp_path)
    c.post(f"/api/projects/{pid}/scenes/sc1/lock", json={"locked": True})
    new = tmp_path / "new.jpg"
    new.write_bytes(b"new")
    with new.open("rb") as fh:
        r = c.post(f"/api/projects/{pid}/scenes/sc1/plate",
                   files={"file": ("n.jpg", fh, "image/jpeg")})
    assert r.status_code == 409, r.text
    assert "锁定" in r.json()["detail"]
