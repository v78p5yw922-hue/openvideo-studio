"""契约校验器的回归测试：每条规则都要能被触发。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-validate-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, service, validate  # noqa: E402

db.init_db()
PID = "vtest"

HEAD = """## 项目
- 片名：校验
- 总时长：10 秒（共 2 镜 × 5 秒）

## 世界设定
- 视觉语言：{style}

## 角色卡

### 阿甲
- 外观描述：A young man in a grey shirt.
- 一致性锁：短发
- 服装锁：灰衬衫

### 阿乙
- 外观描述：A young woman in a blue dress.
- 一致性锁：马尾
- 服装锁：蓝裙

## 场景卡

### 场景 1：屋子（id：room）
- 机位：平视
- 固定物件：桌中
- 光源：左窗
- 母版镜头号：镜 1

## 镜头表
| 镜号 | 场景 | 出场角色 | 景别/机位 | 动作 | 台词 | 音效 | 配乐 | 转场 | 锚定 |
|---|---|---|---|---|---|---|---|---|---|
{rows}

## 每镜 prompt
### 镜 1
【机位】中景平视
【人物】无
【动作】树影移动
【场景】桌中
【画质】35mm

### 镜 2
【机位】近景平视
【人物】阿甲 | 画面中 | 60%
【动作】抬头
【场景】桌中
【画质】35mm
"""

ROW1 = "| 镜 1（5s） | 屋子 | 无（空镜） | 中景平视 | 空镜 | 无 | 风过窗 | 无 | cut | 空 |"
ROW2 = "| 镜 2（5s） | 屋子 | 阿甲 | 近景平视 | 抬头 | <d>阿甲：有人吗</d> | 翻书声 | 低音提琴长音 | cut | 空 |"


def _md(rows=(ROW1, ROW2), style="cinematic, soft daylight"):
    return HEAD.format(style=style, rows="\n".join(rows))


@pytest.fixture(autouse=True)
def fresh():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "校验", "")
    yield


def _load(md):
    service.import_markdown(PID, md)
    return validate.validate_project(PID)


def _codes(rep, level="error"):
    items = rep["warnings"] if level == "warn" else rep["errors"]
    return [x["code"] for x in items]


def test_clean_script_passes_contract():
    rep = _load(_md())
    errs = [c for c in _codes(rep) if c in {
        "STYLE_COSTUME", "CAST_TOO_MANY", "SCENE_UNKNOWN", "PLATE_MISSING",
        "DLG_SPEAKER_NOT_IN_CAST", "DLG_TARGET_NOT_IN_CAST", "DLG_NO_SPEAKER"}]
    assert errs == [], rep


def test_style_costume_is_blocking():
    rep = _load(_md(style="cinematic, crimson official robe, soft light"))
    assert "STYLE_COSTUME" in _codes(rep)


def test_dialogue_target_missing_from_cast_is_blocking():
    row2 = "| 镜 2（5s） | 屋子 | 阿甲 | 近景平视 | 抬头 | <d>阿甲→阿乙：有人吗</d> | 翻书声 | 无 | cut | 空 |"
    rep = _load(_md(rows=(ROW1, row2)))
    assert "DLG_TARGET_NOT_IN_CAST" in _codes(rep)


def test_dialogue_target_present_passes():
    row2 = ("| 镜 2（5s） | 屋子 | 阿甲、阿乙 | 近景平视 | 抬头 "
            "| <d>阿甲→阿乙：有人吗</d> | 翻书声 | 无 | cut | 空 |")
    rep = _load(_md(rows=(ROW1, row2)))
    assert "DLG_TARGET_NOT_IN_CAST" not in _codes(rep)


def test_speaker_not_in_cast_is_blocking():
    row2 = "| 镜 2（5s） | 屋子 | 阿乙 | 近景平视 | 抬头 | <d>阿甲：有人吗</d> | 翻书声 | 无 | cut | 空 |"
    rep = _load(_md(rows=(ROW1, row2)))
    assert "DLG_SPEAKER_NOT_IN_CAST" in _codes(rep)


def test_cast_too_many_is_blocking():
    row2 = ("| 镜 2（5s） | 屋子 | 阿甲、阿乙、阿丙、阿丁 | 近景 | 抬头 "
            "| 无 | 无 | 无 | cut | 空 |")
    rep = _load(_md(rows=(ROW2, row2)))
    assert "CAST_TOO_MANY" in _codes(rep)


def test_plate_without_master_is_blocking():
    row1 = ROW1.replace("| cut | 空 |", "| cut | plate |")
    rep = _load(_md(rows=(row1, ROW2)))
    assert "PLATE_MISSING" in _codes(rep)


def test_music_mood_is_warning():
    row2 = ROW2.replace("低音提琴长音", "低音提琴长音，带一点悲伤")
    rep = _load(_md(rows=(ROW1, row2)))
    assert "MUSIC_MOOD" in _codes(rep, level="warn")


def test_split_dialogue_parses_arrow():
    assert validate.split_dialogue("<d>甲→乙：你好</d>") == ("甲", "乙", "你好")
    assert validate.split_dialogue("<d>甲：你好</d>") == ("甲", "", "你好")
    assert validate.split_dialogue("") == ("", "", "")


def test_plan_blocks_on_contract_error():
    _load(_md(style="cinematic, crimson official robe"))
    p = service.plan(PID, None, draft=False, force=True)
    assert any(b["code"] == "STYLE_COSTUME" for b in p["blocking"]), p["blocking"]
