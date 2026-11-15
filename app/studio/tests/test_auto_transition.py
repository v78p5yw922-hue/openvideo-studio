"""Auto transitions: chain only when the cast is unchanged."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-auto-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, service  # noqa: E402

db.init_db()
PID = "auto_test"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "转场测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.upsert_character("a", "甲", "", [])
    service.upsert_character("b", "乙", "", [])
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "p", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": ["a"], "prompt": "p", "transition": "chain"},
        {"shot_id": "s03", "scene_id": "sc1", "cast": ["a"], "prompt": "p", "transition": "chain"},
        {"shot_id": "s04", "scene_id": "sc1", "cast": ["a", "b"], "prompt": "p", "transition": "chain"},
        {"shot_id": "s05", "scene_id": "sc1", "cast": ["a", "b"], "prompt": "p", "transition": "cut"},
    ])
    yield


def test_auto_sets_cut_when_cast_changes():
    r = service.auto_transitions(PID)
    order = dict(r["order"])
    assert order["s01"] == "cut"
    assert order["s02"] == "cut", "空镜→甲，角色变了"
    assert order["s03"] == "chain", "甲→甲，保持 chain"
    assert order["s04"] == "cut", "甲→甲+乙，角色变了"
    assert order["s05"] == "chain", "甲+乙→甲+乙，可以 chain"


def test_plan_warns_on_chain_with_cast_change():
    p = service.plan(PID, draft=False)
    assert any("建议改 cut" in w["msg"] for w in p["warnings"]), p["warnings"]
