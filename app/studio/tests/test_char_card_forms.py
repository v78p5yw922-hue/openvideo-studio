"""角色卡的三种写法都必须能解析（大模型经常不按模板写）。

另外：
- 写在最后一镜后面的自检清单，不能混进该镜的 prompt。
- 有人物时 anchor 强制归零（防止「人物 + 母版锚定」坏状态）。
"""

from __future__ import annotations

import pytest

from studio.core import script_import, service, db


@pytest.fixture(autouse=True)
def _clean():
    """锚定测试会写库，必须清干净，否则污染后面的执行器测试。"""
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield

BASE = """## 项目
- 片名：测试
- 总时长：10 秒（共 2 镜 × 5 秒）

## 世界设定
- 视觉语言：cinematic

## 角色卡
{chars}

## 场景卡
### 场景 1：屋子（id：room）
- 机位：平视 3 米
- 固定物件：桌中、窗左
- 光源：左窗
- 母版镜头号：镜 1

## 镜头表
| 镜号 | 场景 | 出场角色 | 景别/机位 | 台词 | 转场 | 锚定 |
|---|---|---|---|---|---|---|
| 镜 1（5s） | 屋子 | 无（空镜） | 中景平视 | 无 | cut | plate |
| 镜 2（5s） | 屋子 | 阿甲 | 近景平视 | <d>阿甲：嗯。</d> | cut | 空 |

## 每镜 prompt
### 镜 1
【机位】中景平视
【人物】无
【动作】空镜
【场景】桌中、窗左
【画质】35mm

### 镜 2
【机位】近景平视
【人物】阿甲 | 画面中 | 60%
【动作】抬头
【场景】桌中、窗左
【画质】35mm

【输出前自检，逐条打勾】
☑ 标题
☑ 列数
"""

HEADING_CARD = """### 阿甲
- 年龄 / 身份：20 岁
- 外观描述：A young man in a grey shirt.
- 一致性锁：短发、灰衬衫
- 服装锁：灰衬衫"""

KEYED_CARD = """- 名字：阿甲
- 年龄 / 身份：20 岁
- 外观描述：A young man in a grey shirt.
- 一致性锁：短发、灰衬衫
- 服装锁：灰衬衫"""

LOOSE_CARD = """- 阿甲：
- 年龄 / 身份：20 岁
- 外观描述：A young man in a grey shirt.
- 一致性锁：短发、灰衬衫
- 服装锁：灰衬衫"""


@pytest.mark.parametrize("card,label", [
    (HEADING_CARD, "### 标题式"),
    (KEYED_CARD, "- 名字："),
    (LOOSE_CARD, "- 名字：（大模型常用）"),
])
def test_all_character_card_forms_parse(card, label):
    r = script_import.parse(BASE.format(chars=card))
    assert [c["name"] for c in r["characters"]] == ["阿甲"], label
    assert len(r["characters"][0]["description"]) > 10, label
    assert r["characters"][0]["lock"] == "短发、灰衬衫", label
    s02 = [s for s in r["shots"] if s["shot_id"] == "s02"][0]
    assert s02["cast"] == [script_import._ascii_id("阿甲", "char")], label


def test_self_check_tail_is_not_part_of_last_prompt():
    r = script_import.parse(BASE.format(chars=HEADING_CARD))
    s02 = [s for s in r["shots"] if s["shot_id"] == "s02"][0]
    assert "自检" not in s02["prompt"]
    assert "☑" not in s02["prompt"]
    assert s02["prompt"].rstrip().endswith("35mm")


# ---------------------------------------------------------------- 锚定归一化
PID_A = "anchor_norm_test"


def _shot(**kw):
    base = {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "p",
            "transition": "cut", "anchor": "", "duration": 5.0}
    base.update(kw)
    service.save_shots(PID_A, [base])
    return service.list_shots(PID_A)[0]


def test_empty_shot_keeps_plate_anchor():
    service.create_project(PID_A, "锚定测试", "")
    service.upsert_scene(PID_A, "sc1", name="A")
    s = _shot(cast=[], anchor="plate")
    assert s["anchor"] == "plate"


def test_person_forces_anchor_off():
    s = _shot(cast=["c1"], anchor="plate")
    assert s["cast"] == ["c1"]
    assert s["anchor"] == ""


def test_person_without_anchor_stays_empty():
    s = _shot(cast=["c1"], anchor="")
    assert s["anchor"] == ""
