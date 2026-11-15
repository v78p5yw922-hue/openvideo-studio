"""Import tests: the Markdown we ask DeepSeek to produce must become shots."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-import-")
os.environ["STUDIO_DATA"] = TMP
os.environ["STUDIO_ALLOW_RENDER"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, script_import  # noqa: E402

db.init_db()

SAMPLE = """## 世界设定
- 时代与技术等级：近未来 2070，工业级太空采矿
- 视觉语言：hard sci-fi, brushed metal, cold teal, 35mm
- 色彩脚本：主色冷青 / 辅色琥珀 / 警示色红

## 项目
- 片名：《远星矿脉》
- 一句话故事：采样员发现规律冷光
- 总时长：30 秒（共 6 镜 × 5 秒）
- 空间数量：2 个

## 角色卡
- 名字：林澈
- 年龄 / 身份：29 岁 / 采样工程师
- 英文外观描述：Lin Che, 29, Chinese sampling engineer, clear transparent visor.
- 一致性锁：透明面罩、左眉骨细疤
- 服装锁：深灰织物舱内服

## 场景卡
### 场景 1：烛龙号采样舱
- 英文空间描述：Compact sampling cabin, brushed metal bench.
- 机位：样本台后方，平视朝样本台
- 科技物件：样本台（中）、样本柜（左）、舷窗（右）
- 光源：顶部冷白 LED，左侧冷青
- 母版镜头号：镜 1

### 场景 2：气闸舱
- 英文空间描述：Abandoned airlock with circular door.
- 机位：内侧朝外门
- 科技物件：圆形外门（中）、转轮（左）、压力表（右）
- 光源：顶部冷白应急灯，门缝冷蓝
- 母版镜头号：镜 4

## 镜头表
| 镜号 | 场景 | 出场角色（≤3） | 景别 / 机位 / 运动 | 空间锚点 | 动作 | 台词 | 转场 |
|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 烛龙号采样舱 | 无（空镜） | 中景，平视固定 | 样本台中 | 指示灯呼吸 | 无 | cut |
| 镜 2（5s） | 烛龙号采样舱 | 林澈 | 近景，轻推 | 样本台中 | 俯身触碰 | <d>林澈：矿脉在脉冲。</d> | chain |
| 镜 3（5s） | 气闸舱 | 林澈 | 近景 | 外门中 | 按住转轮 | 林澈：外面有回应。 | cut |

## 每镜可执行 prompt（五段式）
### 镜 1
【场景锚点】采样舱空镜。【角色】无人。【动作】指示灯呼吸。
### 镜 2
【场景锚点】采样舱近景。【角色】林澈。【动作】俯身触碰夹具。
### 镜 3
【场景锚点】气闸舱。【角色】林澈。【动作】按住转轮。
"""


def test_parse_markdown():
    r = script_import.parse(SAMPLE)
    assert r["project"]["title"] == "远星矿脉"
    assert "hard sci-fi" in r["project"]["style"]
    assert r["project"]["target_duration"] == 30
    assert [c["name"] for c in r["characters"]] == ["林澈"]
    assert [s["name"] for s in r["scenes"]] == ["烛龙号采样舱", "气闸舱"]
    assert [s["shot_id"] for s in r["shots"]] == ["s01", "s02", "s03"]


def test_parse_binds_scene_cast_dialogue_transition():
    r = script_import.parse(SAMPLE)
    s1, s2, s3 = r["shots"]
    assert s1["cast"] == [] and s1["anchor"] == "plate"
    assert s1["transition"] == "cut"
    assert s2["cast"] and s2["cast"][0].startswith("char_")
    assert s2["transition"] == "chain"
    assert s2["dialogue"].startswith("<d>")
    assert s3["dialogue"] == "<d>林澈：外面有回应。</d>", "台词缺 <d> 要自动补"
    assert s1["scene_id"] == s2["scene_id"], "同一场景要绑定同一个 id"
    assert s3["scene_id"] != s1["scene_id"], "换场景必须换 id"
    assert s1["scene_id"] in {x["scene_id"] for x in r["scenes"]}


def test_parse_uses_prompt_blocks():
    r = script_import.parse(SAMPLE)
    assert "采样舱空镜" in r["shots"][0]["prompt"]
    assert "按住转轮" in r["shots"][2]["prompt"]


def test_parse_warns_when_prompt_missing():
    text = SAMPLE.replace("### 镜 3\n【场景锚点】气闸舱。【角色】林澈。【动作】按住转轮。\n", "")
    r = script_import.parse(text)
    assert any("s03" in w for w in r["warnings"])


def test_parse_normalises_bilingual_names():
    text = SAMPLE.replace("名字：林澈", "名字：Lin Che（林澈）") \
                 .replace("| 林澈 |", "| Lin Che（林澈） |")
    r = script_import.parse(text)
    assert r["characters"][0]["name"] == "林澈"
    assert all(c == "char_林澈" or c.startswith("char_") for c in r["shots"][1]["cast"])


def test_parse_requires_shot_table():
    with pytest.raises(ValueError):
        script_import.parse("## 项目\n- 片名：空\n")
