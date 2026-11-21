"""镜内时间轴（【节奏】）的回归测试。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-beat-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, service, validate  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "pc_beat", Path("/home/agent/openvideo/video-agent/scripts/prompt_compiler.py"))
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)

db.init_db()
PID = "beattest"


def _shot(prompt, dialogue="", dur=5.0):
    return {"visual_prompt": prompt, "camera": "中景",
            "dialogue": dialogue, "soundscape": "", "music": "",
            "characters_present": ["叶一恒"], "duration": dur}


def test_beats_parse_in_order():
    beats = pc.parse_beats("0.0 站定 | 1.8 合扇 | 3.0 切近景，拱手 | 4.3 抬眼")
    assert [b[0] for b in beats] == [0.0, 1.8, 3.0, 4.3]
    assert beats[0][1] == "站定"


def test_beats_render_as_timestamps():
    shot = _shot("【机位】中景\n【节奏】0.0 站定 | 2.0 合扇\n【场景】庙会街")
    out = pc.compile_shot(shot, "cinematic", ["叶一恒"])
    assert "At 00:02.000" in out
    assert "开场" in out


def test_cut_beat_becomes_camera_cut():
    shot = _shot("【机位】中景\n【节奏】0.0 站定 | 3.0 切近景，拱手\n【场景】庙会街")
    out = pc.compile_shot(shot, "cinematic", ["叶一恒"])
    assert "the camera cuts to" in out
    assert "切近景" not in out.split("camera cuts to")[1][:8]   # 不重复「切」


def test_beats_replace_action_slot():
    shot = _shot("【机位】中景\n【动作】站着不动\n【节奏】0.0 站定 | 2.0 合扇\n【场景】街")
    out = pc.compile_shot(shot, "cinematic", ["叶一恒"])
    assert "站着不动" not in out
    assert "合扇" in out


def test_beat_overrun_is_blocking():
    md = """## 项目
- 片名：节拍
- 总时长：5 秒

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
| 镜 1（5s） | 屋子 | 阿甲 | 中景 | 抬头 | 无 | 无 | 无 | cut | 空 |

## 每镜 prompt
### 镜 1
【机位】中景平视
【人物】阿甲 | 画面中 | 60%
【节奏】0.0 站定 | 7.0 转身
【场景】桌中
【画质】35mm
"""
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "节拍", "")
    service.import_markdown(PID, md)
    rep = validate.validate_project(PID)
    assert "BEAT_OVERRUN" in [e["code"] for e in rep["errors"]]
