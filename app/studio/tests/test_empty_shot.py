"""每个 5 秒必须有信息量：EMPTY_SHOT 检查。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-empty-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, service, validate  # noqa: E402

db.init_db()
PID = "emptytest"

HEAD = """## 项目
- 片名：空镜
- 总时长：10 秒

## 世界设定
- 视觉语言：cinematic

## 角色卡

### 阿甲
- 外观描述：A young man.

## 场景卡

### 场景 1：屋子（id：room）
- 机位：平视
- 固定物件：桌
- 光源：窗

## 镜头表
| 镜号 | 场景 | 出场角色 | 景别/机位 | 动作 | 台词 | 音效 | 配乐 | 转场 | 锚定 |
|---|---|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 屋子 | {cast} | 中景 | {action} | 无 | 无 | 无 | cut | 空 |

## 每镜 prompt
### 镜 1
【机位】中景平视
{extra}
【场景】桌中
【画质】35mm
"""


@pytest.fixture(autouse=True)
def fresh():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _run(cast, action, extra=""):
    service.create_project(PID, "空镜", "")
    service.import_markdown(PID, HEAD.format(cast=cast, action=action, extra=extra))
    return [w["code"] for w in validate.validate_project(PID)["warnings"]]


def test_empty_shot_without_beats_warns():
    assert "EMPTY_SHOT" in _run("无（空镜）", "空镜建立")


def test_empty_shot_with_beats_is_allowed():
    codes = _run("无（空镜）", "空镜",
                 extra="【人物】无\n【节奏】0.0 空街 | 1.5 人影入画")
    assert "EMPTY_SHOT" not in codes


def test_shot_with_character_is_fine():
    codes = _run("阿甲", "抬头", extra="【人物】阿甲 | 画面中 | 60%")
    assert "EMPTY_SHOT" not in codes
