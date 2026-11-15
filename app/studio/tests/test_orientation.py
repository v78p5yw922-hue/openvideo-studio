"""Landscape default + draft take must not block the final render."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-orient-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, model, service  # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


@pytest.fixture
def project(tmp_path):
    pid = "orient_test"
    service.create_project(pid, "横屏测试", "")
    plate = tmp_path / "p.jpg"
    plate.write_bytes(b"plate")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a",
         "transition": "cut", "anchor": "plate"},
    ])
    return pid


def test_default_is_landscape():
    assert model.RENDER_PARAMS_DEFAULT["width"] > model.RENDER_PARAMS_DEFAULT["height"]
    assert (model.RENDER_PARAMS_DEFAULT["width"], model.RENDER_PARAMS_DEFAULT["height"]) == (1344, 768)


def test_plan_includes_shot_whose_take_is_draft(project):
    shots = service.recompute_fingerprints(project)
    service.record_take(project, "s01", "/tmp/a.mp4", shots[0]["fingerprint"],
                        note="draft")
    assert service.shot_state(project, service.list_shots(project)[0])["state"] == "review"

    final_plan = service.plan(project, draft=False)
    assert [s["shot_id"] for s in final_plan["shots"]] == ["s01"], \
        "草稿不能挡住正式渲染"

    draft_plan = service.plan(project, draft=True)
    assert draft_plan["count"] == 0, "已有待审草稿时不必再出草稿"


def test_final_take_blocks_final_plan(project):
    shots = service.recompute_fingerprints(project)
    service.record_take(project, "s01", "/tmp/a.mp4", shots[0]["fingerprint"],
                        note="executor")
    assert service.plan(project, draft=False)["count"] == 0, "正式 take 待审时不该重复渲"


def test_passed_blocks_everything(project):
    shots = service.recompute_fingerprints(project)
    service.record_take(project, "s01", "/tmp/a.mp4", shots[0]["fingerprint"])
    service.review_shot(project, "s01", "pass")
    assert service.plan(project, draft=False)["count"] == 0
    assert service.plan(project, draft=True)["count"] == 0


def test_landscape_changes_fingerprint(project):
    """Orientation is part of render params, so switching to landscape invalidates."""
    shots = service.recompute_fingerprints(project)
    portrait = model.shot_fingerprint(
        {**shots[0]}, None, [], {"width": 768, "height": 1344})
    landscape = model.shot_fingerprint(
        {**shots[0]}, None, [], {"width": 1344, "height": 768})
    assert portrait != landscape
