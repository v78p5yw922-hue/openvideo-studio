"""只读编译预览：草稿、锁帧一致性、结构化错误与零副作用。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402
from studio.exec import render_adapter, runner  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "compile_preview_test"
CID = "char_hidden"
pc = render_adapter.pc


@pytest.fixture(autouse=True)
def project(tmp_path):
    with db.txn() as conn:
        for table in ("shots", "takes", "reviews", "previews", "jobs", "events",
                      "scenes", "asset_versions", "project_cast", "projects", "characters"):
            conn.execute(f"DELETE FROM {table}")
    service.create_project(PID, "编译预览测试", "电影感")
    plate = tmp_path / "plate.jpg"
    plate.write_bytes(b"\xff\xd8" + b"p" * 100)
    service.upsert_scene(PID, "sc1", name="庙街", plate_path=str(plate))
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [],
         "prompt": "【场景】旧码头。", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [],
         "prompt": "【场景】旧码头。", "transition": "chain"},
    ])
    return tmp_path


def _preview(sid: str = "s01", prompt: str = "【场景】新茶铺。【动作】风起。"):
    return client.post(f"/api/projects/{PID}/shots/{sid}/compile-preview",
                       json={"prompt": prompt})


def _snapshot() -> dict:
    tables = ("projects", "shots", "scenes", "characters", "project_cast",
              "jobs", "events", "takes", "reviews", "previews")
    with db.txn() as conn:
        return {table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
                for table in tables}


def _render_module():
    spec = importlib.util.spec_from_file_location(
        "rd_compile_preview", REPO / "scripts" / "render_drama.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_prompt(shot_id: str, draft: str, has_lock: bool) -> str:
    rd = _render_module()
    script = render_adapter.build_script(PID)
    shot = next(s for s in script["shots"] if s["shot_id"] == shot_id)
    shot["visual_prompt"] = draft
    chars = {}
    for char in script["characters"]:
        chars[str(char.get("character_id") or char.get("name"))] = char
        chars[str(char.get("name"))] = char
    scene = next(s for s in script["scenes"] if s["scene_id"] == shot["scene_id"])
    return rd.build_prompt(shot, chars, script["visual_style"], scene, has_lock=has_lock)


def test_draft_preview_is_read_only_and_never_starts_work(monkeypatch):
    before = _snapshot()
    render_json = db.DATA_DIR / "projects" / PID / "render.json"
    assert not render_json.is_file()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("只读预览调用了写入、执行器或子进程")

    for owner, name in ((service, "save_shots"), (service, "recompute_fingerprints"),
                        (service, "enqueue_job"), (render_adapter, "write_script"),
                        (runner, "tick"), (runner, "_run_render"),
                        (runner, "_run_preview"), (subprocess, "run"),
                        (subprocess, "Popen")):
        monkeypatch.setattr(owner, name, forbidden)

    response = _preview()
    assert response.status_code == 200, response.text
    report = response.json()
    assert "新茶铺" in report["compiled"]
    assert "旧码头" not in report["compiled"]
    assert report["compiled"] == "".join(s["text"] for s in report["segments"])
    assert report["elapsed_ms"] >= 0
    assert _snapshot() == before
    assert not render_json.is_file()


def test_validation_errors_use_field_allowed_and_character_names():
    service.upsert_character(CID, "叶一恒", "Wearing a robe.", [])
    service.upsert_character("char_other", "林婉儿", "", [])
    service.assign_shot(PID, "s01", cast_add=[CID, "char_other"])
    draft = (
        "【机位】景别=无人机；高度=平视；角度=正面；运镜=固定；距离=1.5"
        "【人物】叶一恒：位置=左；层级=前景；朝向=面向镜头；视线=看对方；"
        "对象=char_hidden；占比=50%\n"
        "林婉儿：位置=右；层级=中景；朝向=面向镜头；视线=看镜头；占比=50%"
    )
    response = _preview(prompt=draft)
    assert response.status_code == 200, response.text
    errors = response.json()["errors"]
    camera = next(e for e in errors if e["code"] == "CAMERA_ENUM_INVALID")
    assert camera["field"] == "景别" and "大特写" in camera["allowed"]
    target = next(e for e in errors if e["code"] == "EYELINE_TARGET_NOT_IN_CAST")
    assert target["allowed"] == ["叶一恒", "林婉儿"]
    assert CID not in target["allowed"]
    assert any(e["code"] == "RELATION_REQUIRED" for e in errors)


def test_missing_project_and_shot_return_404():
    assert client.post("/api/projects/no_such/shots/s01/compile-preview",
                       json={"prompt": "x"}).status_code == 404
    assert _preview("no_such").status_code == 404


def test_pinned_instruction_matches_renderer_for_empty_and_cast_shots(project):
    frame = project / "pinned.jpg"
    frame.write_bytes(b"\xff\xd8" + b"f" * 100)
    service.set_first_frame(PID, "s01", str(frame))
    assert render_adapter.build_script(PID)["shots"][0]["first_frame"] == str(frame)

    draft = "【场景】空院，风吹竹影。"
    empty = _preview(prompt=draft).json()["compiled"]
    assert empty.startswith(pc.i2v_instruction())
    assert empty == _build_prompt("s01", draft, has_lock=True)

    service.upsert_character(CID, "叶一恒", "Wearing a robe.", [])
    service.assign_shot(PID, "s01", cast_add=[CID])
    service.set_first_frame(PID, "s01", str(frame))
    cast = _preview(prompt=draft).json()["compiled"]
    assert not cast.startswith(pc.i2v_instruction())
    assert cast == _build_prompt("s01", draft, has_lock=False)


def test_chain_lock_requires_existing_upstream_and_same_cast(project):
    clip = project / "upstream.mp4"
    clip.write_bytes(b"v" * 4096)
    first = service.list_shots(PID)[0]
    service.record_take(PID, "s01", str(clip), first["fingerprint"])

    chained = _preview("s02", "【场景】同一空院。").json()["compiled"]
    assert chained.startswith(pc.i2v_instruction())

    service.upsert_character(CID, "叶一恒", "", [])
    service.assign_shot(PID, "s01", cast_add=[CID])
    changed_cast = _preview("s02", "【场景】同一空院。").json()["compiled"]
    assert not changed_cast.startswith(pc.i2v_instruction())
