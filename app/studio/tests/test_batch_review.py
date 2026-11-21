"""批量审核的后端测试。"""

from __future__ import annotations

import pytest

from studio.core import db, service

db.init_db()


def _mk_project(pid: str) -> None:
    if not service.get_project(pid):
        service.create_project(pid, f"批量审核 {pid}")
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc", "cast": [], "camera": "中景",
         "prompt": "p", "dialogue": "", "sound": "", "music": "",
         "transition": "cut", "anchor": "", "duration": 5.0},
        {"shot_id": "s02", "scene_id": "sc", "cast": [], "camera": "中景",
         "prompt": "p", "dialogue": "", "sound": "", "music": "",
         "transition": "cut", "anchor": "", "duration": 5.0},
    ])


def _add_take(pid: str, sid: str, version: int = 1) -> None:
    with db.txn() as conn:
        conn.execute(
            "INSERT INTO takes(project_id,shot_id,version,path,elapsed_s,peak_mib,note,"
            "fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, sid, version, f"/tmp/{pid}_{sid}.mp4", 1.0, 1, "", "fp", db.now()))


def test_batch_review_passes_and_skips_missing_take():
    _mk_project("p_batch_review")
    _add_take("p_batch_review", "s01")          # s02 故意没有 take

    r = service.batch_review("p_batch_review", ["s01", "s02"], "pass")
    assert r["done"] == ["s01"]
    assert r["skipped"] == ["s02"]
    assert r["count"] == 1

    with db.txn() as conn:
        row = conn.execute(
            "SELECT verdict FROM reviews WHERE project_id=? AND shot_id='s01'",
            ("p_batch_review",)).fetchone()
    assert row["verdict"] == "pass"


def test_batch_review_rejects_unknown_shot():
    _mk_project("p_batch_review2")
    _add_take("p_batch_review2", "s01")
    r = service.batch_review("p_batch_review2", ["s01", "nope"], "reject", "不好")
    assert r["done"] == ["s01"]
    assert "nope" in r["skipped"]


def test_batch_review_validates_input():
    _mk_project("p_batch_review3")
    with pytest.raises(ValueError):
        service.batch_review("p_batch_review3", [], "pass")
    with pytest.raises(ValueError):
        service.batch_review("p_batch_review3", ["s01"], "maybe")
