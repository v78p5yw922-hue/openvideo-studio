"""Round-trip safety: re-importing an exported script must not invalidate takes.

If it did, editing a script in DeepSeek and pasting it back would silently throw
away every approved shot — the worst possible failure mode for a production tool.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-rt-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, service  # noqa: E402
from studio.export import markdown  # noqa: E402

db.init_db()

SCRIPT = """## 项目
- 片名：回环安全
- 总时长：15 秒（共 3 镜 × 5 秒）

## 世界设定
- 视觉语言：hard sci-fi, cold teal

## 角色卡
- 名字：阿澈
- 英文外观描述：A 29-year-old engineer with a clear visor.

## 场景卡
### 场景 1：舱内
- 机位：平视固定
- 科技物件：台面（中）、窗（右）
- 光源：顶部冷白

## 镜头表
| 镜号 | 场景 | 出场角色（≤3） | 景别 / 机位 / 运动 | 空间锚点 | 动作 | 台词 | 转场 |
|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 舱内 | 无（空镜） | 中景平视 | 台面中 | 灯呼吸 | 无 | cut |
| 镜 2（5s） | 舱内 | 阿澈 | 近景 | 台面中 | 俯身 | <d>阿澈：有信号。</d> | chain |
| 镜 3（5s） | 舱内 | 阿澈 | 特写 | 台面中 | 抬眼 | 无 | cut |

## 每镜可执行 prompt
### 镜 1
【场景锚点】舱内空镜。【角色】无人。
### 镜 2
【场景锚点】舱内近景。【角色】阿澈。
### 镜 3
【场景锚点】舱内特写。【角色】阿澈。
"""


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _approved_project(pid="rt_test"):
    service.import_markdown(pid, SCRIPT)
    shots = service.recompute_fingerprints(pid)
    for sh in shots:
        service.record_take(pid, sh["shot_id"], f"/tmp/{sh['shot_id']}.mp4",
                            sh["fingerprint"], elapsed_s=500.0, peak_mib=23000)
        service.review_shot(pid, sh["shot_id"], "pass")
    return pid


def _states(pid):
    return {s["shot_id"]: service.shot_state(pid, s)["state"]
            for s in service.recompute_fingerprints(pid)}


def test_all_passed_before_round_trip():
    pid = _approved_project()
    assert set(_states(pid).values()) == {"passed"}


def test_reimport_keeps_every_take_valid():
    pid = _approved_project()
    md = markdown.build_markdown(pid)
    service.import_markdown(pid, md)
    states = _states(pid)
    assert set(states.values()) == {"passed"}, f"回环后不应失效：{states}"


def test_reimport_twice_still_valid():
    pid = _approved_project()
    for _ in range(2):
        service.import_markdown(pid, markdown.build_markdown(pid))
    assert set(_states(pid).values()) == {"passed"}


def test_reimport_reuses_existing_character():
    """Re-import must not mint a second character for the same name."""
    pid = "rt_char"
    service.import_markdown(pid, SCRIPT)
    before = {c["name"]: c["character_id"] for c in service.list_characters()}
    service.import_markdown(pid, markdown.build_markdown(pid))
    after = {c["name"]: c["character_id"] for c in service.list_characters()}
    assert len(after) == len(before), f"角色被重复创建: {before} -> {after}"
    assert after["阿澈"] == before["阿澈"]


def test_reimport_keeps_cast_binding():
    pid = _approved_project()
    before = {s["shot_id"]: s["cast"] for s in service.list_shots(pid)}
    service.import_markdown(pid, markdown.build_markdown(pid))
    after = {s["shot_id"]: s["cast"] for s in service.list_shots(pid)}
    assert before == after


def test_anchor_column_round_trips(tmp_path):
    """A shot whose anchor is deliberately empty must stay empty after export/import."""
    pid = "rt_anchor"
    service.import_markdown(pid, SCRIPT)
    scene_id = service.list_shots(pid)[0]["scene_id"]
    plate = tmp_path / "p.jpg"
    plate.write_bytes(b"plate")
    service.upsert_scene(pid, scene_id, name="舱内", plate_path=str(plate))
    service.assign_shot(pid, "s01", scene_id=scene_id)      # empty shot -> anchor plate
    service.assign_shot(pid, "s03", cast_add=[])            # no cast, anchor stays ""
    shots = {s["shot_id"]: s for s in service.recompute_fingerprints(pid)}
    assert shots["s01"]["anchor"] == "plate"
    service.import_markdown(pid, markdown.build_markdown(pid))
    after = {s["shot_id"]: s for s in service.list_shots(pid)}
    assert after["s01"]["anchor"] == "plate"
    assert after["s03"]["anchor"] == "", "显式空锚定不能被默认为 plate"


def test_reimport_with_custom_ids_keeps_everything():
    """A project built through the API uses ids like `c1`/`scene_a`; the exported
    script only carries names, so import must map names back onto those ids
    instead of minting hashed ones."""
    pid = "rt_custom"
    service.create_project(pid, "自定义 ID", "style")
    service.upsert_scene(pid, "scene_a", name="舱内")
    service.upsert_character("c1", "阿澈", "desc", [])
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "scene_a", "cast": ["c1"], "prompt": "p1",
         "transition": "cut"},
        {"shot_id": "s02", "scene_id": "scene_a", "cast": ["c1"], "prompt": "p2",
         "transition": "chain"},
    ])
    shots = service.recompute_fingerprints(pid)
    for sh in shots:
        service.record_take(pid, sh["shot_id"], f"/tmp/{sh['shot_id']}.mp4", sh["fingerprint"])
        service.review_shot(pid, sh["shot_id"], "pass")
    assert set(_states(pid).values()) == {"passed"}

    preview = service.preview_import(pid, markdown.build_markdown(pid))
    assert preview["changed"] == [], preview

    service.import_markdown(pid, markdown.build_markdown(pid))
    assert set(_states(pid).values()) == {"passed"}
    assert len(service.list_characters()) == 1, "不能重复建角色"
    assert [s["cast"] for s in service.list_shots(pid)] == [["c1"], ["c1"]]


def test_preview_reimport_reports_nothing_invalidated():
    pid = _approved_project()
    p = service.preview_import(pid, markdown.build_markdown(pid))
    assert p["changed"] == [], p
    assert p["kept"] == ["s01", "s02", "s03"]


def test_preview_reports_edit_without_writing():
    pid = _approved_project()
    md = markdown.build_markdown(pid).replace(
        "【场景锚点】舱内空镜。【角色】无人。", "【场景锚点】舱内空镜。【角色】无人。灯灭了。")
    p = service.preview_import(pid, md)
    assert p["changed"] == ["s01", "s02"], "s01 改了，s02 是 chain 跟着失效"
    assert p["kept"] == ["s03"]
    # preview must not mutate anything
    assert set(_states(pid).values()) == {"passed"}


def test_preview_detects_added_and_removed():
    pid = _approved_project()
    md = markdown.build_markdown(pid)
    base = md.split("## 镜头表")[0]
    trimmed = base + """## 镜头表
| 镜号 | 场景 | 出场角色（≤3） | 景别 / 机位 / 运动 | 空间锚点 | 动作 | 台词 | 转场 |
|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 舱内 | 无（空镜） | 中景平视 | 台面中 | 灯呼吸 | 无 | cut |
| 镜 2（5s） | 舱内 | 阿澈 | 近景 | 台面中 | 俯身 | <d>阿澈：有信号。</d> | chain |
| 镜 4（5s） | 舱内 | 阿澈 | 特写 | 台面中 | 抬眼 | 无 | cut |

## 每镜可执行 prompt
### 镜 1
【场景锚点】舱内空镜。【角色】无人。
### 镜 2
【场景锚点】舱内近景。【角色】阿澈。
### 镜 4
【场景锚点】舱内特写。【角色】阿澈。
"""
    p = service.preview_import(pid, trimmed)
    assert "s04" in p["added"]
    assert "s03" in p["removed"]


def test_real_edit_invalidates_that_shot_and_its_chain():
    pid = _approved_project()
    md = markdown.build_markdown(pid)
    edited = md.replace("【场景锚点】舱内空镜。【角色】无人。",
                        "【场景锚点】舱内空镜，灯灭了。【角色】无人。")
    service.import_markdown(pid, edited)
    states = _states(pid)
    assert states["s01"] == "stale", "改了 prompt 就该失效"
    assert states["s02"] == "stale", "s02 是 chain，必须跟着上游失效"
    assert states["s03"] == "passed", "s03 是 cut，不该被影响"
