"""Markdown round-trip: export must be re-importable without losing structure."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-md-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, script_import, service  # noqa: E402
from studio.export import markdown  # noqa: E402

db.init_db()

SAMPLE = """## 项目
- 片名：回环测试
- 总时长：15 秒（共 3 镜 × 5 秒）
- 空间数量：2 个

## 世界设定
- 视觉语言：hard sci-fi, brushed metal, cold teal

## 角色卡
- 名字：阿澈
- 英文外观描述：A 29-year-old engineer with a clear visor.

## 场景卡
### 场景 1：舱内
- 机位：平视固定
- 科技物件：台面（中）、窗（右）
- 光源：顶部冷白

### 场景 2：气闸
- 机位：内侧朝外门
- 科技物件：圆门（中）、转轮（左）
- 光源：门缝冷蓝

## 镜头表
| 镜号 | 场景 | 出场角色（≤3） | 景别 / 机位 / 运动 | 空间锚点 | 动作 | 台词 | 转场 |
|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 舱内 | 无（空镜） | 中景平视 | 台面中 | 灯呼吸 | 无 | cut |
| 镜 2（5s） | 舱内 | 阿澈 | 近景 | 台面中 | 俯身 | <d>阿澈：有信号。</d> | chain |
| 镜 3（5s） | 气闸 | 阿澈 | 特写 | 圆门中 | 抬眼 | 无 | cut |

## 每镜可执行 prompt
### 镜 1
【场景锚点】舱内空镜。【角色】无人。
### 镜 2
【场景锚点】舱内近景。【角色】阿澈。
### 镜 3
【场景锚点】气闸特写。【角色】阿澈。
"""


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _import(pid="md_test"):
    service.import_markdown(pid, SAMPLE)
    return pid


def test_export_contains_expected_sections():
    pid = _import()
    md = markdown.build_markdown(pid)
    for section in ("## 项目", "## 角色卡", "## 场景卡", "## 镜头表", "## 每镜可执行 prompt"):
        assert section in md
    assert "回环测试" in md
    assert "hard sci-fi" in md


def test_round_trip_preserves_structure():
    pid = _import()
    md = markdown.build_markdown(pid)
    parsed = script_import.parse(md)

    original = service.list_shots(pid)
    assert len(parsed["shots"]) == len(original) == 3
    assert [s["shot_id"] for s in parsed["shots"]] == [s["shot_id"] for s in original]

    # scene binding must survive: same names -> same hashed ids
    scenes = {s["scene_id"]: s for s in service.list_scenes(pid)}
    for got, want in zip(parsed["shots"], original):
        assert want["scene_id"] == got["scene_id"], "场景 id 必须稳定"
        assert want["transition"] == got["transition"]
        assert want["dialogue"] == got["dialogue"], want["dialogue"]
        assert want["prompt"].strip() == got["prompt"].strip()

    # cast names must survive and map back to the same character ids
    assert parsed["shots"][1]["cast"] == original[1]["cast"]


def test_round_trip_is_idempotent():
    pid = _import()
    md1 = markdown.build_markdown(pid)
    parsed = script_import.parse(md1)
    service.save_shots(pid, parsed["shots"])
    md2 = markdown.build_markdown(pid)
    assert md1.strip() == md2.strip(), "导出→导入→再导出必须稳定"


def test_export_endpoint():
    from fastapi.testclient import TestClient
    from studio.api.app import app
    pid = _import("md_http")
    c = TestClient(app)
    r = c.get(f"/api/projects/{pid}/export/markdown")
    assert r.status_code == 200
    assert "## 镜头表" in r.text
