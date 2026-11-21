"""有人物时强制 anchor 归零：防止「人物 + 母版锚定」的坏状态。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-anchor-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, service  # noqa: E402

db.init_db()
PID = "anchor_test"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "锚定测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    yield


def _shot(**kw):
    base = {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "p",
            "transition": "cut", "anchor": "", "duration": 5.0}
    base.update(kw)
    service.save_shots(PID, [base])
    return service.list_shots(PID)[0]


def test_empty_shot_keeps_plate_anchor():
    s = _shot(cast=[], anchor="plate")
    assert s["anchor"] == "plate"


def test_person_forces_anchor_off():
    # 即使前端传了 plate，只要有人物就归零
    s = _shot(cast=["c1"], anchor="plate")
    assert s["cast"] == ["c1"]
    assert s["anchor"] == ""


def test_person_without_anchor_stays_empty():
    s = _shot(cast=["c1"], anchor="")
    assert s["anchor"] == ""
