"""镜头时长解析 + 台词容量的回归测试。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-dur-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, script_import, service, validate  # noqa: E402

db.init_db()
PID = "durtest"

HEAD = """## 项目
- 片名：时长
- 总时长：10 秒

## 世界设定
- 视觉语言：cinematic

## 角色卡

### 阿甲
- 外观描述：A young man in a grey shirt.

## 场景卡

### 场景 1：屋子（id：room）
- 机位：平视
- 固定物件：桌中
- 光源：左窗

## 镜头表
| 镜号 | 场景 | 出场角色 | 景别/机位 | 动作 | 台词 | 音效 | 配乐 | 转场 | 锚定 |
|---|---|---|---|---|---|---|---|---|---|
{row}

## 每镜 prompt
### 镜 1
【机位】中景平视
【人物】阿甲 | 画面中 | 60%
【动作】抬头
【场景】桌中
【画质】35mm
"""


def _row(stamp: str, line: str) -> str:
    return f"| {stamp} | 屋子 | 阿甲 | 近景平视 | 抬头 | {line} | 无 | 无 | cut | 空 |"


@pytest.fixture(autouse=True)
def fresh():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _import(row):
    md = HEAD.format(row=row)
    service.create_project(PID, "时长", "")
    service.import_markdown(PID, md)
    return service.list_shots(PID)[0]


def test_duration_parsed_from_shot_header():
    s = _import(_row("镜 1（8s）", "无"))
    assert abs(float(s["duration"]) - 8.0) < 1e-6


def test_duration_defaults_to_five_without_stamp():
    s = _import(_row("镜 1", "无"))
    assert abs(float(s["duration"]) - 5.0) < 1e-6


def test_duration_clamped_to_h3_range():
    s = _import(_row("镜 1（30s）", "无"))
    assert float(s["duration"]) == 15.0


def test_short_line_fits_in_five_seconds():
    _import(_row("镜 1（5s）", "<d>阿甲：三天之内，我娶你回家</d>"))
    rep = validate.validate_project(PID)
    assert "DIALOGUE_TOO_LONG" not in [e["code"] for e in rep["errors"]]


def test_long_line_blocks_five_second_shot():
    long_line = "<d>阿甲：三天之内我娶你回家林婉儿你听好了这庙会街上的每一个人都会看着我把你娶回去</d>"
    _import(_row("镜 1（5s）", long_line))
    rep = validate.validate_project(PID)
    assert "DIALOGUE_TOO_LONG" in [e["code"] for e in rep["errors"]]


def test_long_line_ok_in_longer_shot():
    long_line = "<d>阿甲：三天之内我娶你回家林婉儿你听好了这庙会街上的每一个人都会看着我把你娶回去</d>"
    _import(_row("镜 1（15s）", long_line))
    rep = validate.validate_project(PID)
    assert "DIALOGUE_TOO_LONG" not in [e["code"] for e in rep["errors"]]
